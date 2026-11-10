"""Lanceur Windows de Sillage : serveur local + fenêtre WebView2 (Edge, présent sur Windows 10/11)."""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if getattr(sys, "frozen", False):
    ROOT = Path(sys._MEIPASS)
sys.path.insert(0, str(ROOT))

# Aucune fenêtre console pour FFmpeg, Node ou les workers.
if sys.platform == "win32":
    _popen_init = subprocess.Popen.__init__

    def _no_console(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _no_console

node = ROOT / "bin" / "node.exe"
if node.is_file():
    os.environ["SILLAGE_NODE"] = str(node)

# Sous-processus (worker.py / import_worker.py) : server.py les traite dès son import.
if len(sys.argv) > 1 and Path(sys.argv[1]).name in ("worker.py", "import_worker.py"):
    import server  # noqa: F401  (se termine par sys.exit)

import server  # noqa: E402

DATA_DIR = server.DATA_DIR
SELF = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, __file__]


def run_server(session_file):
    sys.exit(server.serve(0, DATA_DIR, session_file, False) or 0)


def main():
    if len(sys.argv) > 2 and sys.argv[1] == "--server":
        run_server(sys.argv[2])
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    session = DATA_DIR / f"session-{os.getpid()}.json"
    log = open(DATA_DIR / "sillage.log", "w", encoding="utf-8")
    proc = subprocess.Popen(SELF + ["--server", str(session)], stdout=log, stderr=log)
    try:
        deadline = time.time() + 90
        while not session.exists():
            if proc.poll() is not None or time.time() > deadline:
                raise SystemExit("Sillage n'a pas pu démarrer. Journal : " + str(DATA_DIR / "sillage.log"))
            time.sleep(0.15)
        time.sleep(0.1)
        import json
        url = json.loads(session.read_text())["url"]
        try:
            import webview
            webview.settings["ALLOW_DOWNLOADS"] = True
            webview.create_window("Sillage", url, width=1060, height=800, min_size=(390, 600))
            webview.start()
        except Exception:
            # Repli : navigateur par défaut, Sillage tourne jusqu'à fermeture du processus
            import webbrowser
            webbrowser.open(url)
            proc.wait()
    finally:
        server.Library.stop_process(proc)
        session.unlink(missing_ok=True)
        log.close()


if __name__ == "__main__":
    main()
