"""Isolated playlist discovery / title search. One JSON object on stdin."""
import json
import sys
from imports import MAX_ITEMS, candidate, playlist_url, rank_candidates, text
from worker import Logger, emit, friendly_error, downloader_options


def run(payload):
    import yt_dlp
    options = downloader_options()
    options.update({'extract_flat': True, 'skip_download': True, 'noplaylist': False,
                    'ignoreerrors': True, 'socket_timeout': 12, 'retries': 1, 'extractor_retries': 1,
                    'playlistend': MAX_ITEMS + 1, 'logger': Logger()})
    if payload['kind'] == 'playlist':
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(playlist_url(payload['url']), download=False)
        if not info or info.get('_type') not in ('playlist', 'multi_video'):
            raise ValueError('Cette playlist est vide ou inaccessible. Utilise une playlist publique ou non répertoriée accessible sans connexion.')
        entries = list(info.get('entries') or [])
        if len(entries) > MAX_ITEMS or (info.get('playlist_count') or 0) > MAX_ITEMS:
            raise ValueError(f'Cette playlist dépasse {MAX_ITEMS} vidéos. Divise-la en plusieurs playlists.')
        if not entries:
            raise ValueError('Aucune vidéo accessible n’a été trouvée dans cette playlist.')
        hidden = (info.get('playlist_count') or 0) > len(entries)
        emit(type='meta', title=text(info.get('title')) or 'Playlist YouTube',
             notice='YouTube masque certains éléments indisponibles. Seules les vidéos retournées sont affichées.' if hidden else '')
        for position, entry in enumerate(entries):
            found = candidate(entry) if entry else None
            available = found and entry.get('availability') not in ('private', 'premium_only', 'subscriber_only', 'needs_auth') and found['title'] not in ('[Private video]', '[Deleted video]')
            emit(type='playlist_item', source=dict(title=found['title'] if found else 'Vidéo indisponible', artist=found['author'] if found else '', duration=found['duration'] if found else 0),
                 candidates=[{**found, 'confidence': 'direct', 'reason': 'Vidéo de la playlist'}] if available else [],
                 position=position, error='' if available else 'Cette vidéo est privée, supprimée ou inaccessible.')
    else:
        options['playlistend'] = 5
        with yt_dlp.YoutubeDL(options) as downloader:
            for source in payload['tracks']:
                if not source.get('title'):
                    emit(type='match', index=source['index'], candidates=[], error='Le titre manque dans le fichier exporté.')
                    continue
                query = ' '.join(filter(None, [source.get('artist'), source['title'], source.get('version')]))
                try:
                    info = downloader.extract_info('ytsearch5:' + query, download=False)
                    found = [candidate(entry) for entry in (info or {}).get('entries', []) if entry]
                    matches = rank_candidates(source, found)
                    emit(type='match', index=source['index'], candidates=matches, error='' if matches else 'Aucun résultat accessible. Tu peux ajouter un lien YouTube.')
                except Exception as exc:
                    emit(type='match', index=source['index'], candidates=[], error=friendly_error(exc))
    emit(type='complete')


if __name__ == '__main__':
    try:
        run(json.load(sys.stdin))
    except Exception as exc:
        emit(type='error', error=friendly_error(exc))
        sys.exit(1)
