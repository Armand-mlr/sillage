"""Sillage: private loopback HTTP app, cancellable worker and persistent library."""
import argparse
try:
    import fcntl  # macOS / Linux
except ImportError:  # Windows
    fcntl = None
    import msvcrt
import json
import mimetypes
import os
from pathlib import Path
import re
import runpy
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

# --- Gestion des sous-processus quand Sillage est compilé avec PyInstaller ---
# Comparaison exacte : "import_worker.py" se termine aussi par "worker.py".
_SUBCOMMAND = Path(sys.argv[1]).name if len(sys.argv) > 1 else ""

if _SUBCOMMAND == "worker.py":
    import worker
    try:
        worker.run(*sys.argv[2:])
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        worker.emit(status="error", error=worker.friendly_error(exc), progress=0)
        sys.exit(1)
    sys.exit(0)

if _SUBCOMMAND == "import_worker.py":
    runpy.run_module("import_worker", run_name="__main__")
    sys.exit(0)
# -----------------------------------------------------------------------------

from audio import youtube_url
from batches import BatchManager

if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS)
else:
    ROOT = Path(__file__).resolve().parent

# Dossier durable : le dossier temporaire est vidé au redémarrage (historique perdu).
if sys.platform == "win32":
    DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Sillage"
else:
    DATA_DIR = Path.home() / "Library" / "Application Support" / "Sillage"
ACTIVE = {"queued", "analyzing", "downloading", "converting"}
VERSION = "1.2.0"


class Library:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.jobs = {}
        self.processes = {}
        self.lock = threading.RLock()
        self.history_file = self.directory / "history.json"
        self.stopping = False
        self.origin = ""
        self.token = secrets.token_urlsafe(32)
        if self.history_file.exists():
            try:
                for job in json.loads(self.history_file.read_text()):
                    if re.fullmatch(r"[a-f0-9]{32}", job.get("id", "")) and self.file_for(job):
                        self.jobs[job["id"]] = job
            except (OSError, ValueError, TypeError, AttributeError):
                pass
        # Supprime uniquement les anciens dossiers de tâches abandonnés
        for folder in self.directory.iterdir():
            if folder.is_dir() and re.fullmatch(r"[a-f0-9]{32}", folder.name) and folder.name not in self.jobs:
                shutil.rmtree(folder, ignore_errors=True)
        self.batches = BatchManager(self)

    def file_for(self, job):
        name = job.get("filename")
        if not name or Path(name).name != name:
            return None
        path = self.directory / job["id"] / name
        return path if path.is_file() and path.resolve().is_relative_to(self.directory) else None

    def persist(self):
        ready = [j for j in self.jobs.values() if j["status"] == "done"]
        temp = self.history_file.with_suffix(".tmp")
        temp.write_text(json.dumps(ready, ensure_ascii=False, indent=2))
        temp.replace(self.history_file)

    def snapshot(self):
        with self.lock:
            return {
                "version": VERSION,
                "jobs": sorted((dict(j) for j in self.jobs.values()), key=lambda j: j["created"], reverse=True),
                "imports": self.batches.summaries(),
                "active_import": self.batches.active_id,
                "export_root": str(self.directory / "exports"),
            }

    def start(self, url, audio_format, batch_id=None, item=None, background=True):
        url = youtube_url(url)
        if audio_format not in ("wav", "mp3"):
            raise ValueError("Choisis le format WAV ou MP3.")
        with self.lock:
            if (
                self.stopping
                or any(j["status"] in ACTIVE for j in self.jobs.values())
                or (self.batches.active_id and self.batches.active_id != batch_id)
                or (batch_id and self.batches.stop.is_set())
            ):
                raise RuntimeError("Une conversion est déjà en cours. Attends sa fin ou annule-la.")
            job_id = secrets.token_hex(16)
            job = {
                "id": job_id,
                "url": url,
                "format": audio_format,
                "status": "queued",
                "progress": 0,
                "title": "Analyse de la vidéo…",
                "author": "YouTube",
                "duration": 0,
                "created": datetime.now(timezone.utc).isoformat(),
            }
            self.jobs[job_id] = job
            if batch_id:
                job["batch_id"] = batch_id
                item["job_id"] = job_id
                self.batches.persist()
        if background:
            threading.Thread(target=self.run, args=(job_id,), daemon=True).start()
        else:
            self.run(job_id)
        with self.lock:
            return dict(job)

    def run(self, job_id):
        folder = self.directory / job_id
        folder.mkdir(exist_ok=True)
        process = None
        try:
            with self.lock:
                job = self.jobs[job_id]
                if job["status"] == "cancelled" or self.stopping:
                    return
                job["status"] = "analyzing"
                log = open(folder / "conversion.log", "w")
                try:
                    process = subprocess.Popen(
                        [sys.executable, str(ROOT / "worker.py"), job["url"], job["format"], str(folder)],
                        stdout=subprocess.PIPE,
                        stderr=log,
                        text=True,
                        start_new_session=True,
                        cwd=ROOT,
                        env={**os.environ, "PYTHONUNBUFFERED": "1"},
                    )
                finally:
                    log.close()
                self.processes[job_id] = process
            timer = threading.Timer(3600, self.timeout, args=(job_id,))
            timer.daemon = True
            timer.start()
            try:
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    with self.lock:
                        if job["status"] not in ("cancelled", "error"):
                            for key in (
                                "status", "progress", "title", "author", "duration",
                                "video_id", "sample_rate", "channels", "filename",
                                "size", "error", "speed", "eta", "downloaded", "total"
                            ):
                                if key in event:
                                    job[key] = event[key]
                            if job["status"] == "done":
                                if not self.file_for(job):
                                    job.update(status="error", error="Le fichier final est introuvable.")
                                else:
                                    self.persist()
                code = process.wait()
                with self.lock:
                    if job["status"] in ACTIVE:
                        job.update(status="error", error="La conversion s’est interrompue. Tu peux réessayer.", progress=0)
            finally:
                timer.cancel()
        except Exception as exc:
            with self.lock:
                job = self.jobs[job_id]
                if job["status"] != "cancelled":
                    job.update(status="error", error="Impossible de terminer la conversion. " + str(exc)[-250:])
        finally:
            if process:
                if process.poll() is None:
                    self.stop_process(process)
                if process.stdout:
                    process.stdout.close()
            with self.lock:
                self.processes.pop(job_id, None)
                if self.jobs[job_id]["status"] != "done":
                    shutil.rmtree(folder, ignore_errors=True)

    @staticmethod
    def stop_process(process):
        if sys.platform == "win32":
            # Pas de groupes de processus : taskkill /T arrête aussi FFmpeg et Node lancés par le worker
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise ValueError("Conversion introuvable.")
            if job["status"] in ACTIVE:
                job.update(status="cancelled", progress=0)
            process = self.processes.get(job_id) if job["status"] == "cancelled" else None
        if process:
            self.stop_process(process)

    def timeout(self, job_id):
        self.cancel(job_id)
        with self.lock:
            job = self.jobs[job_id]
            if job["status"] == "cancelled":
                job.update(status="error", error="La conversion a dépassé une heure. Réessaie avec une vidéo plus courte.")

    def shutdown(self):
        with self.lock:
            self.stopping = True
            ids = list(self.processes)
        self.batches.close()
        for job_id in ids:
            self.cancel(job_id)


class Handler(BaseHTTPRequestHandler):
    server_version = "Sillage/1.2"

    @property
    def library(self):
        return self.server.library

    def log_message(self, fmt, *args):
        pass

    def send_headers(self, status, content_type, length=None, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if length is not None:
            self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' https://i.ytimg.com data:; media-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def respond(self, status, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_headers(status, "application/json; charset=utf-8", len(data))
        self.wfile.write(data)

    def host_ok(self):
        return self.headers_in.get("Host") == self.library.origin.removeprefix("http://")

    @property
    def headers_in(self):
        return self.__dict__["headers"]

    def authorized(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers_in.get("Cookie", ""))
            value = cookie.get("sillage")
            return value is not None and secrets.compare_digest(value.value, self.library.token)
        except Exception:
            return False

    def do_GET(self):
        try:
            self.get()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def get(self):
        if not self.host_ok():
            return self.respond(403, {"error": "Accès local uniquement."})
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self.respond(200, {"app": "sillage", "version": VERSION})
        key = parse_qs(parsed.query).get("key", [""])[0]
        if parsed.path == "/" and key and secrets.compare_digest(key, self.library.token):
            self.send_headers(
                303,
                "text/plain",
                0,
                {"Location": "/", "Set-Cookie": "sillage=" + self.library.token + "; HttpOnly; SameSite=Strict; Path=/"},
            )
            return
        if not self.authorized():
            return self.respond(401, {"error": "Ouvre Sillage depuis son application ou son lanceur."})
        if parsed.path == "/api/state":
            return self.respond(200, self.library.snapshot())
        import_match = re.fullmatch(r"/api/imports/([a-f0-9]{32})", parsed.path)
        if import_match:
            try:
                return self.respond(200, self.library.batches.detail(import_match[1]))
            except ValueError as exc:
                return self.respond(404, {"error": str(exc)})
        match = re.fullmatch(r"/api/(download|audio)/([a-f0-9]{32})", parsed.path)
        if match:
            with self.library.lock:
                job = self.library.jobs.get(match[2], {})
                file = self.library.file_for(job) if job.get("status") == "done" else None
            if not file:
                return self.respond(404, {"error": "Ce fichier n’est plus disponible."})
            size = file.stat().st_size
            start, end, status = 0, size - 1, 200
            extras = {"Accept-Ranges": "bytes"}
            if match[1] == "download":
                extras["Content-Disposition"] = (
                    "attachment; filename=\"Sillage." + job["format"] + "\"; filename*=UTF-8''" + quote(file.name)
                )
            requested = self.headers_in.get("Range")
            if requested:
                range_match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                if not range_match or not any(range_match.groups()):
                    self.send_headers(416, "text/plain", 0, {"Content-Range": f"bytes */{size}"})
                    return
                if range_match[1]:
                    start = int(range_match[1])
                    end = min(int(range_match[2]), size - 1) if range_match[2] else size - 1
                else:
                    start = max(0, size - int(range_match[2]))
                if start > end or start >= size:
                    self.send_headers(416, "text/plain", 0, {"Content-Range": f"bytes */{size}"})
                    return
                status = 206
                extras["Content-Range"] = f"bytes {start}-{end}/{size}"
            self.send_headers(status, "audio/wav" if job["format"] == "wav" else "audio/mpeg", end - start + 1, extras)
            with file.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    block = stream.read(min(256 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
            return
        assets = {
            "/": "index.html",
            "/app.css": "app.css",
            "/app.js": "app.js",
            "/batch.js": "batch.js",
            "/favicon.svg": "favicon.svg",
        }
        if parsed.path not in assets:
            return self.respond(404, {"error": "Page introuvable."})
        path = ROOT / "web" / assets[parsed.path]
        data = path.read_bytes()
        if parsed.path == "/":
            data = data.replace(b"__SILLAGE_TOKEN__", self.library.token.encode())
        self.send_headers(200, (mimetypes.guess_type(path.name)[0] or "application/octet-stream") + "; charset=utf-8", len(data))
        self.wfile.write(data)

    def do_POST(self):
        try:
            self.post()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def post(self):
        if (
            not self.host_ok()
            or not self.authorized()
            or not secrets.compare_digest(self.headers_in.get("X-Sillage-Token", ""), self.library.token)
        ):
            return self.respond(403, {"error": "Session invalide. Relance Sillage."})
        if self.headers_in.get("Origin") not in (None, self.library.origin):
            return self.respond(403, {"error": "Origine refusée."})
        try:
            length = int(self.headers_in.get("Content-Length", "0"))
            limit = 3 * 1024 * 1024 if self.path.startswith("/api/imports") else 4096
            if length < 0 or length > limit:
                return self.respond(413, {"error": "Requête trop volumineuse."})
            body = json.loads(self.rfile.read(length) or "{}")
            if not isinstance(body, dict):
                raise ValueError("Requête invalide.")
            if self.path == "/api/jobs":
                return self.respond(202, self.library.start(body.get("url"), body.get("format", "wav")))
            if self.path == "/api/imports":
                return self.respond(202, self.library.batches.create(body))
            import_match = re.fullmatch(r"/api/imports/([a-f0-9]{32})/(start|cancel|search|selection)", self.path)
            if import_match:
                batch_id, action = import_match.groups()
                if action == "start":
                    return self.respond(202, self.library.batches.start(batch_id, body))
                if action == "search":
                    return self.respond(202, self.library.batches.search_again(batch_id))
                if action == "selection":
                    return self.respond(200, self.library.batches.save_selection(batch_id, body))
                self.library.batches.cancel(batch_id)
                return self.respond(200, {"ok": True})
            match = re.fullmatch(r"/api/jobs/([a-f0-9]{32})/cancel", self.path)
            if match:
                self.library.cancel(match[1])
                return self.respond(200, {"ok": True})
            return self.respond(404, {"error": "Action introuvable."})
        except (ValueError, TypeError) as exc:
            return self.respond(400, {"error": str(exc)})
        except RuntimeError as exc:
            return self.respond(409, {"error": str(exc)})
        except OSError:
            return self.respond(500, {"error": "Impossible de lire ou d’enregistrer les fichiers. Vérifie l’espace libre et l’accès au dossier."})


def serve(port=0, directory=None, session_file=None, open_browser=False):
    data_directory = Path(directory or DATA_DIR)
    data_directory.mkdir(parents=True, exist_ok=True)
    instance_lock = (data_directory / ".instance.lock").open("a")
    try:
        if fcntl:
            fcntl.flock(instance_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            msvcrt.locking(instance_lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("Sillage est déjà ouvert. Ferme l’autre fenêtre de Sillage avant de relancer cette version.", file=sys.stderr)
        return 1
    library = Library(data_directory)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.library = library
    library.origin = f"http://127.0.0.1:{server.server_port}"
    session = {"url": library.origin + "/?key=" + library.token, "pid": os.getpid(), "origin": library.origin}
    if session_file:
        target = Path(session_file)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(session))
        target.chmod(0o600)
    print("Sillage est prêt : " + library.origin, flush=True)
    if open_browser:
        import webbrowser
        webbrowser.open(session["url"])

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        library.shutdown()
        server.server_close()
        if session_file:
            Path(session_file).unlink(missing_ok=True)
        instance_lock.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir")
    parser.add_argument("--session-file")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    sys.exit(serve(args.port, args.data_dir, args.session_file, args.open) or 0)
