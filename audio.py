"""Shared validation and the actual audio encoder. No shell interpolation."""
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import re
import subprocess


def youtube_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Colle le lien d’une vidéo YouTube.")
    value = value.strip()
    if not value.startswith(("https://", "http://")):
        value = "https://" + value
    try:
        url = urlparse(value)
        if url.scheme not in ("https", "http") or url.username or url.password or url.port not in (None, 443, 80):
            raise ValueError
        host = (url.hostname or "").lower()
        parts = url.path.strip("/").split("/")
        video = None
        if host in ("youtu.be", "www.youtu.be") and len(parts) == 1:
            video = parts[0]
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
            if url.path == "/watch":
                video = parse_qs(url.query).get("v", [None])[0]
            elif len(parts) == 2 and parts[0] in ("shorts", "live", "embed"):
                video = parts[1]
        if not video or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("Ce lien ne correspond pas à une vidéo YouTube. Essaie un lien youtube.com/watch ou youtu.be.") from None
    return "https://www.youtube.com/watch?v=" + video


def safe_filename(title):
    cleaned = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]', "", str(title))
    # macOS limits a filename by bytes, not by Unicode characters.
    return cleaned.strip(" .")[:110].encode("utf-8")[:180].decode("utf-8", errors="ignore").strip() or "Audio YouTube"


def ffmpeg_path():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def convert_audio(source, destination, audio_format, duration=0, progress=None):
    """Decode the best source into PCM WAV, or encode MP3 at 320 kb/s.

    Preserve source sample rate / channel count. Never claim to restore lost quality.
    Caller runs in a process group so cancel also terminates the encoder.
    """
    if audio_format not in ("wav", "mp3"):
        raise ValueError("Format audio inconnu.")
    destination = Path(destination)
    args = [ffmpeg_path(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-map", "0:a:0", "-vn", "-map_metadata", "-1"]
    args += ["-c:a", "pcm_s16le", "-rf64", "auto"] if audio_format == "wav" else ["-c:a", "libmp3lame", "-b:a", "320k", "-id3v2_version", "3"]
    args += ["-progress", "pipe:1", "-nostats", str(destination)]
    import tempfile
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=errors, text=True) as process:
            for line in process.stdout:
                key, _, val = line.strip().partition("=")
                if key == "out_time_us" and duration and progress:
                    try:
                        progress(min(99, float(val) / 1_000_000 / duration * 100))
                    except ValueError:
                        pass
            code = process.wait()
        if code:
            errors.seek(0)
            detail = errors.read(8192).decode(errors="replace")
            raise RuntimeError("La conversion audio a échoué. " + detail[-1200:])
    if not destination.is_file() or destination.stat().st_size < 44:
        raise RuntimeError("Le fichier audio obtenu est vide.")
    return destination
