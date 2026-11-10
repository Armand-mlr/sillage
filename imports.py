"""Pure import parsing and candidate ranking; no network or downloads."""
import csv
import io
import math
import plistlib
import re
import unicodedata
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from audio import youtube_url

MAX_ITEMS = 500
MAX_FILE_BYTES = 2 * 1024 * 1024


def playlist_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('Colle le lien d’une playlist YouTube.')
    value = value.strip()
    if not value.startswith(('http://', 'https://')):
        value = 'https://' + value
    try:
        u = urlparse(value)
        code = parse_qs(u.query).get('list', [''])[0]
        if (u.scheme not in ('http', 'https') or u.username or u.password or u.port not in (None, 80, 443)
                or u.hostname not in ('youtube.com', 'www.youtube.com', 'music.youtube.com', 'm.youtube.com', 'youtu.be')
                or not re.fullmatch(r'[A-Za-z0-9_-]{10,150}', code)):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Ce lien ne contient pas de playlist YouTube. Il doit comporter « list=… ».') from None
    if code.startswith('RD'):
        raise ValueError('Les mixes automatiques ne sont pas des playlists fixes. Utilise une playlist enregistrée sur YouTube.')
    return 'https://www.youtube.com/playlist?list=' + code


def text(value, limit=300):
    return re.sub(r'[\x00-\x1f\x7f]', ' ', str(value or '')).strip()[:limit]


def seconds(value, milliseconds=False):
    try:
        if isinstance(value, str) and ':' in value:
            parts = value.split(':')
            if len(parts) not in (2, 3):
                return 0
            result = 0
            for part in parts:
                result = result * 60 + float(part)
        else:
            result = float(value or 0) / (1000 if milliseconds else 1)
        return result if math.isfinite(result) and 0 < result <= 24 * 3600 else 0
    except (TypeError, ValueError):
        return 0


def track(title, artist='', duration=0, version=''):
    return dict(title=text(title), artist=text(artist), duration=seconds(duration), version=text(version))


def normalized(value):
    value = unicodedata.normalize('NFKD', str(value)).casefold()
    return ' '.join(re.findall(r'[^\W_]+', ''.join(c for c in value if not unicodedata.combining(c)), re.UNICODE))


def parse_music_file(raw, filename='Playlist.txt'):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_FILE_BYTES:
        raise ValueError('Choisis un fichier XML ou texte non vide, de 2 Mo maximum.')
    title = text(Path(filename).stem) or 'Playlist Musique'
    if raw.lstrip().startswith((b'<?xml', b'<plist')) or filename.lower().endswith('.xml'):
        if b'<!ENTITY' in raw.upper():
            raise ValueError('Ce XML contient des entités non prises en charge. Réexporte la playlist depuis Musique.')
        try:
            data = plistlib.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get('Tracks'), dict):
                raise TypeError
            playlists = [p for p in data.get('Playlists', []) if isinstance(p, dict) and 'Playlist Items' in p]
            if len(playlists) > 1:
                raise ValueError('Ce fichier contient plusieurs playlists. Exporte une seule playlist depuis Musique.')
            if not playlists:
                raise ValueError('La playlist XML est vide ou ne contient pas de liste de morceaux.')
            playlist = playlists[0]
            title = text(playlist.get('Name')) or title
            records = playlist['Playlist Items']
            if not isinstance(records, list):
                raise TypeError
            if len(records) > MAX_ITEMS:
                raise ValueError(f'Une importation accepte {MAX_ITEMS} morceaux maximum. Divise cette playlist.')
            tracks = []
            for record in records:
                item = data['Tracks'].get(str(record.get('Track ID')), {})
                tracks.append(track(item.get('Name'), item.get('Artist'), seconds(item.get('Total Time'), True)))
        except (plistlib.InvalidFileException, KeyError, TypeError, AttributeError, OverflowError):
            raise ValueError('Ce XML n’est pas un export de playlist Musique. Utilise Fichier → Bibliothèque → Exporter la playlist → XML.') from None
    else:
        try:
            if raw.startswith((b'\xff\xfe', b'\xfe\xff')):
                content = raw.decode('utf-16')
            elif b'\x00' in raw[:200]:
                content = raw.decode('utf-16-le')
            else:
                content = raw.decode('utf-8-sig')
        except UnicodeError:
            try:
                content = raw.decode('cp1252')
            except UnicodeError:
                raise ValueError('Le fichier texte est illisible. Réexporte-le depuis Musique ou en UTF-8.') from None
        lines = [line for line in content.splitlines() if line.strip()]
        if not lines:
            raise ValueError('Ce fichier ne contient aucun morceau.')
        if len(lines) > MAX_ITEMS + 1:
            raise ValueError(f'Une importation accepte {MAX_ITEMS} morceaux maximum. Divise cette playlist.')
        delimiter = '\t' if '\t' in lines[0] else '|' if '|' in lines[0] else ';' if ';' in lines[0] else ','
        rows = list(csv.reader(io.StringIO('\n'.join(lines)), delimiter=delimiter))
        headings = [normalized(c) for c in rows[0]]
        named = next((i for i, h in enumerate(headings) if h in {'name', 'nom', 'titre', 'title', 'track name', 'song'}), None)
        art = next((i for i, h in enumerate(headings) if h in {'artist', 'artiste', 'artists'}), None)
        dur = next((i for i, h in enumerate(headings) if h in {'total time', 'temps total', 'duration', 'duree', 'time', 'temps'}), None)
        tracks = []
        if named is not None and art is not None and delimiter != '|':
            def col(row, index):
                return row[index] if index is not None and index < len(row) else ''
            for row in rows[1:]:
                tracks.append(track(col(row, named), col(row, art), seconds(col(row, dur), dur is not None and headings[dur] in {'total time', 'temps total'})))
        else:
            if delimiter != '|':
                raise ValueError('Ce texte n’est pas un export Musique. Pour une liste manuelle, utilise une ligne « Artiste | Titre », avec éventuellement « | Version ».')
            for row in rows:
                if len(row) not in (2, 3):
                    raise ValueError('Chaque ligne doit suivre « Artiste | Titre » ou « Artiste | Titre | Version ».')
                tracks.append(track(row[1], row[0], version=row[2] if len(row) == 3 else ''))
    if not tracks:
        raise ValueError('Cette playlist ne contient aucun morceau.')
    if len(tracks) > MAX_ITEMS:
        raise ValueError(f'Une importation accepte {MAX_ITEMS} morceaux maximum. Divise cette playlist.')
    return title, tracks


def candidate(info):
    video_id = info.get('id', '')
    if not isinstance(video_id, str) or not re.fullmatch(r'[\w-]{11}', video_id, re.ASCII):
        return None
    if info.get('live_status') in ('is_live', 'is_upcoming') or info.get('is_live'):
        return None
    return dict(url=youtube_url('https://youtu.be/' + video_id), video_id=video_id,
                title=text(info.get('title')), author=text(info.get('channel') or info.get('uploader')), duration=seconds(info.get('duration')))


def rank_candidates(source, candidates):
    """Metadata heuristic, not a calibrated probability or an audio match."""
    wanted_title = set(normalized(source['title']).split())
    wanted_artist = set(normalized(source.get('artist', '')).split())
    wanted = normalized(source['title'] + ' ' + source.get('version', ''))
    variants = ('live', 'remix', 'cover', 'karaoke', 'instrumental', 'slowed', 'sped up', 'extended', 'radio edit', 'remaster')
    result, seen = [], set()
    for entry in candidates:
        if not entry or entry['video_id'] in seen:
            continue
        seen.add(entry['video_id'])
        title, author = normalized(entry['title']), normalized(entry['author'])
        title_tokens = set(title.split())
        title_fit = len(wanted_title & title_tokens) / max(1, len(wanted_title))
        artist_fit = len(wanted_artist & (title_tokens | set(author.split()))) / max(1, len(wanted_artist))
        score, reasons = 60 * title_fit + 30 * artist_fit, []
        if title_fit < .8:
            reasons.append('Titre différent')
        if not wanted_artist or artist_fit < .8:
            reasons.append('Artiste à vérifier')
        wanted_version = set(normalized(source.get('version', '')).split())
        if wanted_version and not wanted_version.issubset(title_tokens):
            score -= 30
            reasons.append('Version demandée non confirmée')
        for variant in variants:
            if bool(re.search(r'\b' + re.escape(variant) + r'\b', title)) != bool(re.search(r'\b' + re.escape(variant) + r'\b', wanted)):
                score -= 30
                reasons.append('Version à vérifier')
                break
        expected, actual = source.get('duration', 0), entry.get('duration', 0)
        if expected and actual:
            delta = abs(expected - actual)
            if delta <= max(5, expected * .03):
                score += 7
            elif delta > max(15, expected * .1):
                score -= 25
                reasons.append('Durée différente')
        if 'topic' in author or 'official audio' in title:
            score += 3
        probable = title_fit >= .85 and artist_fit >= .85 and bool(wanted_artist) and not reasons and score >= 80
        result.append({**entry, 'score': round(max(0, min(100, score))), 'confidence': 'probable' if probable else 'review',
                       'reason': 'Titre et artiste concordants' if probable else ' · '.join(dict.fromkeys(reasons)) or 'Correspondance à vérifier'})
    return sorted(result, key=lambda c: c['score'], reverse=True)[:5]
