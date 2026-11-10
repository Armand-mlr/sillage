"""An isolated conversion job. Emit JSON lines for the local UI."""
import json
import os
from pathlib import Path
import shutil
import sys
import time

from audio import convert_audio, ffmpeg_path, safe_filename, youtube_url


def emit(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def friendly_error(raw):
    message = str(raw)
    lower = message.lower()
    if any(x in lower for x in ("sign in", "confirm you’re not a bot", "confirm you're not a bot", "po token", "http error 403")):
        return "YouTube refuse l’accès à cette vidéo depuis cette connexion. Essaie une autre vidéo ou réessaie plus tard. Aucun fichier n’a été créé."
    if any(x in lower for x in ("private video", "video unavailable", "not available", "removed", "members-only", "age-restricted")):
        return "Cette vidéo est indisponible ou demande un accès particulier (privée, âge ou abonnement). Essaie une vidéo publique accessible sans connexion."
    if any(x in lower for x in ("unable to download", "timed out", "name resolution", "nodename", "network is unreachable", "connection refused")):
        return "Impossible de joindre YouTube. Vérifie ta connexion Internet et réessaie."
    if "requested format is not available" in lower:
        return "YouTube ne fournit pas de piste audio accessible pour cette vidéo. Essaie une autre vidéo ou mets à jour le moteur avec le fichier de mise à jour."
    return message.removeprefix("ERROR: ")[-700:]


class Logger:
    def debug(self, message):
        pass
    def warning(self, message):
        print(message, file=sys.stderr, flush=True)
    def error(self, message):
        print(message, file=sys.stderr, flush=True)


def downloader_options():
    # Priorité au Node embarqué dans Sillage.app (chemin transmis par la fenêtre native).
    node = os.environ.get("SILLAGE_NODE")
    if not (node and Path(node).is_file()):
        node = shutil.which("node")
    if not node:
        for path in ("/usr/local/bin/node", "/opt/homebrew/bin/node"):
            if Path(path).exists():
                node = path
                break
    return {
        "format": "bestaudio/best", "noplaylist": True, "quiet": True,
        "no_warnings": False, "logger": Logger(),
        "overwrites": True,
        "socket_timeout": 20, "retries": 3, "fragment_retries": 3,
        "concurrent_fragment_downloads": 2, "cachedir": False,
        "max_filesize": 2 * 1024**3, "fixup": "never",
        "ffmpeg_location": ffmpeg_path(),
        "js_runtimes": {"node": {"path": node}} if node else {},
    }


def run(url, audio_format, directory):
    import yt_dlp
    url = youtube_url(url)
    work = Path(directory)
    work.mkdir(parents=True, exist_ok=True)
    last = [0.0]

    def hook(event):
        now = time.monotonic()
        if event["status"] == "downloading" and now - last[0] > 0.25:
            last[0] = now
            total = event.get("total_bytes") or event.get("total_bytes_estimate")
            downloaded = event.get("downloaded_bytes", 0)
            emit(status="downloading", progress=round(min(99, downloaded / total * 100), 1) if total else None,
                 downloaded=downloaded, total=total, speed=event.get("speed"), eta=event.get("eta"))
        elif event["status"] == "finished":
            emit(status="converting", progress=0, speed=None, eta=None)

    options = downloader_options()
    options.update({"outtmpl": str(work / "source.%(ext)s"), "progress_hooks": [hook]})
    emit(status="analyzing", progress=None)
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=False)
        if not info or info.get("_type") in ("playlist", "multi_video"):
            raise ValueError("Ajoute le lien d’une seule vidéo.")
        if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming", "post_live"):
            raise ValueError("Les directs en cours ne sont pas pris en charge. Réessaie lorsque la rediffusion est disponible.")
        duration = float(info.get("duration") or 0)
        if duration > 6 * 3600:
            raise ValueError("Cette version accepte les vidéos de 6 heures maximum.")
        channels = int(info.get("audio_channels") or 2)
        sample_rate = int(info.get("asr") or 48000)
        estimated = duration * (max(sample_rate, 48000) * max(channels, 2) * 2 if audio_format == "wav" else 40000)
        if shutil.disk_usage(work).free < max(400 * 1024**2, estimated * 1.4 + 200 * 1024**2):
            raise ValueError("Il manque de l’espace libre sur le disque pour créer ce fichier audio.")
        title = info.get("title") or "Audio YouTube"
        emit(status="downloading", progress=0, title=title, author=info.get("uploader") or "YouTube",
             duration=duration, video_id=info.get("id"), sample_rate=info.get("asr"), channels=channels if audio_format == "wav" else min(channels, 2))
        downloader.process_info(info)
    sources = [p for p in work.glob("source.*") if p.suffix not in (".part", ".ytdl", ".temp")]
    if not sources:
        raise ValueError("La piste audio n’a pas pu être téléchargée (elle peut dépasser la limite de 2 Go).")
    source = max(sources, key=lambda p: p.stat().st_size)
    destination = work / (safe_filename(title) + "." + audio_format)
    emit(status="converting", progress=0)
    convert_audio(source, destination, audio_format, duration,
                  lambda percent: emit(status="converting", progress=round(percent, 1)))
    for path in work.glob("source.*"):
        path.unlink(missing_ok=True)
    emit(status="done", progress=100, filename=destination.name, size=destination.stat().st_size)


if __name__ == "__main__":
    try:
        run(*sys.argv[1:])
    except Exception as exc:
        print(str(exc), file=sys.stderr, flush=True)
        emit(status="error", error=friendly_error(exc), progress=0)
        sys.exit(1)
