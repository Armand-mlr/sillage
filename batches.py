"""Persistent import preparation, serial conversion queue and non-overwriting exports."""
import base64
import binascii
import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading

from audio import safe_filename, youtube_url
from imports import MAX_FILE_BYTES, MAX_ITEMS, parse_music_file, playlist_url, text

ACTIVE_BATCH = {'preparing', 'running', 'stopping'}
ROOT = Path(__file__).resolve().parent


class BatchManager:
    def __init__(self, library):
        self.library = library
        self.lock = library.lock
        self.path = library.directory / 'imports.json'
        self.batches = {}
        self.active_id = None
        self.process = None
        self.thread = None
        self.stop = threading.Event()
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                for batch in saved:
                    if not re.fullmatch(r'[a-f0-9]{32}', batch.get('id', '')) or not isinstance(batch.get('items'), list):
                        continue
                    if batch['status'] in ACTIVE_BATCH:
                        batch['status'] = 'interrupted'
                        batch['notice'] = 'L’import a été interrompu. Les fichiers terminés sont conservés.'
                    for item in batch['items']:
                        if item.get('status') == 'running':
                            item['status'] = 'pending'
                        if item.get('status') == 'done' and not Path(item.get('export_path') or '/nonexistent-sillage-export').is_file():
                            item.update(status='error', error='Le fichier exporté a été déplacé ou supprimé. Reprends l’import pour le recréer.')
                            if batch['status'] == 'done':
                                batch['status'] = 'partial'
                    self.batches[batch['id']] = batch
            except (OSError, ValueError, TypeError, AttributeError):
                pass

    def persist(self):
        with self.lock:
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(list(self.batches.values()), ensure_ascii=False, indent=2))
            temp.replace(self.path)

    def get(self, batch_id):
        batch = self.batches.get(batch_id)
        if not batch:
            raise ValueError('Cet import est introuvable.')
        return batch

    def assert_idle(self):
        if self.library.stopping or self.active_id or any(j['status'] in {'queued', 'analyzing', 'downloading', 'converting'} for j in self.library.jobs.values()):
            raise RuntimeError('Une opération est déjà en cours. Attends sa fin ou arrête-la.')

    def detail(self, batch_id):
        with self.lock:
            result = copy.deepcopy(self.get(batch_id))
            for item in result['items']:
                job = self.library.jobs.get(item.get('job_id'))
                if item.get('status') == 'running' and job:
                    item['progress'] = job.get('progress')
                    item['stage'] = job['status']
            return result

    def summaries(self):
        with self.lock:
            return [dict(id=b['id'], title=b['title'], kind=b['kind'], status=b['status'], format=b.get('format', 'wav'),
                         total=len(b['items']), selected=sum(bool(i['selected']) for i in b['items']),
                         done=sum(i['status'] == 'done' for i in b['items']), errors=sum(i['status'] == 'error' for i in b['items']),
                         folder=b.get('folder', ''), created=b['created'])
                    for b in sorted(self.batches.values(), key=lambda b: b['created'], reverse=True)]

    @staticmethod
    def make_item(source, index):
        return dict(index=index, source=source, candidates=[], url='', selected=False, searched=False,
                    status='pending', error='', search_error='', job_id=None, export_path='')

    def create(self, body):
        kind = body.get('kind')
        tracks, url = [], ''
        if kind == 'playlist':
            url = playlist_url(body.get('url'))
            title = 'Playlist YouTube'
        elif kind == 'music':
            encoded = body.get('content', '')
            if not isinstance(encoded, str) or len(encoded) > (MAX_FILE_BYTES * 4 // 3 + 8):
                raise ValueError('Le fichier doit faire 2 Mo maximum.')
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise ValueError('Le fichier est illisible. Sélectionne-le à nouveau.') from None
            title, tracks = parse_music_file(raw, text(body.get('filename', 'Playlist.txt')))
        else:
            raise ValueError('Choisis une playlist YouTube ou un export Musique.')
        with self.lock:
            self.assert_idle()
            batch_id = secrets.token_hex(16)
            batch = dict(id=batch_id, kind=kind, title=title, url=url, items=[self.make_item(t, i) for i, t in enumerate(tracks)],
                         status='preparing', created=datetime.now(timezone.utc).isoformat(), format='wav', folder='', notice='')
            self.batches[batch_id] = batch
            self.launch(batch, self.prepare)
            return copy.deepcopy(batch)

    def launch(self, batch, target):
        self.active_id = batch['id']
        self.stop.clear()
        self.persist()
        self.thread = threading.Thread(target=target, args=(batch['id'],), daemon=True)
        self.thread.start()

    def search_again(self, batch_id):
        with self.lock:
            self.assert_idle()
            batch = self.get(batch_id)
            if batch.get('folder'):
                raise ValueError('Cet import a déjà démarré. Ajoute directement le lien manquant dans sa ligne.')
            if batch['kind'] == 'playlist':
                batch['items'] = []
            elif not any(not i['candidates'] for i in batch['items']):
                raise ValueError('Chaque morceau a déjà des propositions. Choisis une autre vidéo dans sa ligne.')
            batch.update(status='preparing', notice='')
            self.launch(batch, self.prepare)
            return self.detail(batch_id)

    def prepare(self, batch_id):
        batch = self.get(batch_id)
        complete = False
        timer = None
        process = None
        try:
            payload = {'kind': batch['kind'], 'url': batch['url'],
                       'tracks': [{**i['source'], 'index': i['index']} for i in batch['items'] if not i['candidates']]}
            with tempfile.TemporaryFile() as errors:
                with self.lock:
                    if self.stop.is_set():
                        return
                    process = subprocess.Popen([sys.executable, str(ROOT / 'import_worker.py')], stdin=subprocess.PIPE,
                                               stdout=subprocess.PIPE, stderr=errors, text=True, start_new_session=True, cwd=ROOT)
                    self.process = process
                timer = threading.Timer(900, self.cancel, args=(batch_id, 'La recherche a dépassé 15 minutes. Tu peux relancer les recherches manquantes.'))
                timer.daemon = True
                timer.start()
                process.stdin.write(json.dumps(payload, ensure_ascii=False))
                process.stdin.close()
                for line in process.stdout:
                    if self.stop.is_set():
                        break
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    with self.lock:
                        if self.stop.is_set():
                            break
                        event_type = event.get('type')
                        if event_type == 'meta':
                            batch['title'] = text(event.get('title')) or batch['title']
                            batch['notice'] = text(event.get('notice'), 1200)
                        elif event_type == 'playlist_item' and len(batch['items']) < MAX_ITEMS:
                            item = self.make_item(event['source'], len(batch['items']))
                            self.apply_match(item, event)
                            batch['items'].append(item)
                        elif event_type == 'match':
                            index = event.get('index')
                            if isinstance(index, int) and 0 <= index < len(batch['items']):
                                self.apply_match(batch['items'][index], event)
                        elif event_type == 'error':
                            batch['notice'] = text(event.get('error'), 1200)
                        elif event_type == 'complete':
                            complete = True
                        self.persist()
                process.wait(timeout=5)
        except Exception as exc:
            if not self.stop.is_set():
                batch['notice'] = 'La préparation n’a pas abouti. ' + str(exc)[-500:]
        finally:
            if timer:
                timer.cancel()
            if process:
                if process.poll() is None:
                    self.library.stop_process(process)
                for stream in (process.stdin, process.stdout):
                    if stream and not stream.closed:
                        stream.close()
            with self.lock:
                if not complete and not batch['notice']:
                    batch['notice'] = 'Préparation interrompue. Tu peux relancer les recherches manquantes.' if self.stop.is_set() else 'La préparation a échoué. Vérifie la connexion ou le lien et réessaie.'
                batch['status'] = 'review' if batch['items'] else ('cancelled' if self.stop.is_set() else 'error')
                self.process = None
                self.active_id = None
                self.persist()

    @staticmethod
    def apply_match(item, event):
        matches = event.get('candidates', [])
        item.update(candidates=matches, searched=True, search_error=text(event.get('error'), 1000))
        item['url'] = matches[0]['url'] if matches else ''
        item['selected'] = bool(matches and matches[0].get('confidence') in ('probable', 'direct'))

    def save_selection(self, batch_id, body):
        with self.lock:
            batch = self.get(batch_id)
            if batch['status'] in ACTIVE_BATCH:
                raise RuntimeError('La sélection ne peut pas changer pendant le traitement.')
            choices = body.get('items')
            if not isinstance(choices, list) or len(choices) != len(batch['items']):
                raise ValueError('La sélection est incomplète.')
            updates = []
            for item, choice in zip(batch['items'], choices):
                if not isinstance(choice, dict) or choice.get('index') != item['index']:
                    raise ValueError('La sélection est invalide.')
                url = choice.get('url', '')
                if not isinstance(url, str) or len(url) > 2048:
                    raise ValueError('Le lien est trop long.')
                updates.append((choice.get('selected') is True, url.strip()))
            audio_format = body.get('format', batch['format'])
            if audio_format not in ('wav', 'mp3'):
                raise ValueError('Choisis WAV ou MP3.')
            destination = body.get('destination', batch.get('destination', ''))
            if not isinstance(destination, str) or len(destination) > 4096:
                raise ValueError('Le dossier de destination est invalide.')
            for item, (selected, url) in zip(batch['items'], updates):
                if item['status'] == 'done':
                    continue
                if item['url'] != url:
                    item['job_id'] = None
                item.update(selected=selected, url=url)
            if not batch.get('folder'):
                batch.update(format=audio_format, destination=destination)
            self.persist()
            return {'ok': True}

    def start(self, batch_id, body):
        with self.lock:
            self.assert_idle()
            batch = self.get(batch_id)
            audio_format = body.get('format', batch['format'])
            if audio_format not in ('wav', 'mp3'):
                raise ValueError('Choisis WAV ou MP3.')
            if batch.get('folder') and audio_format != batch['format']:
                raise ValueError('Le format de cet import est déjà fixé. Crée un nouvel import pour un autre format.')
            choices = body.get('items')
            # Validate the entire request before changing a saved selection.
            updates = []
            if choices is not None:
                if not isinstance(choices, list) or len(choices) != len(batch['items']):
                    raise ValueError('La sélection est incomplète. Recharge cet import.')
                for item, choice in zip(batch['items'], choices):
                    if not isinstance(choice, dict) or choice.get('index') != item['index']:
                        raise ValueError('La sélection est invalide.')
                    chosen = choice.get('selected') is True
                    url = youtube_url(choice.get('url')) if chosen and item['status'] != 'done' else item['url']
                    updates.append((chosen, url))
            else:
                updates = [(i['selected'], i['url']) for i in batch['items']]
            remaining = [i for i, (selected, url) in zip(batch['items'], updates) if selected and url and i['status'] != 'done']
            if not remaining:
                raise ValueError('Sélectionne au moins un morceau restant avec un lien YouTube valide.')
            if not batch.get('folder'):
                destination = body.get('destination') or str(self.library.directory / 'exports')
                if not isinstance(destination, str) or len(destination) > 4096:
                    raise ValueError('Le dossier de destination est invalide.')
                parent = Path(destination).expanduser().resolve()
                if parent == (self.library.directory / 'exports').resolve():
                    parent.mkdir(exist_ok=True)
                if not parent.is_dir():
                    raise ValueError('Le dossier choisi n’existe plus. Choisis un autre dossier.')
                name = safe_filename(batch['title']) + ' - ' + datetime.now().strftime('%Y-%m-%d %H-%M') + ' - ' + batch_id[:6]
                folder = parent / name
                try:
                    folder.mkdir(exist_ok=False)
                except OSError as exc:
                    raise ValueError('Impossible de créer le dossier d’export. Choisis un dossier accessible. ' + str(exc)[-150:]) from None
                batch['folder'] = str(folder)
            elif not Path(batch['folder']).is_dir():
                raise ValueError('Le dossier de cet import a été déplacé ou supprimé. Remets-le à son emplacement avant de reprendre.')
            for item, (selected, url) in zip(batch['items'], updates):
                if item['status'] == 'done':
                    continue
                if item['url'] != url:
                    item['job_id'] = None
                item.update(selected=selected, url=url, status='pending' if selected else 'skipped', error='')
            batch.update(format=audio_format, status='running', notice='')
            self.launch(batch, self.download)
            return self.detail(batch_id)

    def export_audio(self, job, batch, item):
        source = self.library.file_for(job)
        if not source:
            raise ValueError('Le fichier converti est introuvable. Relance le morceau.')
        folder = Path(batch['folder'])
        if not folder.is_dir():
            raise ValueError('Le dossier d’export n’est plus accessible.')
        title = ' - '.join(filter(None, [item['source'].get('artist'), item['source'].get('title')])) or job['title']
        stem = f"{item['index'] + 1:03d} - {safe_filename(title)}"
        for suffix in range(1, 10000):
            target = folder / (stem + (f' ({suffix})' if suffix > 1 else '') + '.' + job['format'])
            if shutil.disk_usage(folder).free < source.stat().st_size + 100 * 1024**2:
                raise ValueError('Il manque de l’espace libre dans le dossier de destination.')
            created = False
            try:
                with target.open('xb') as output, source.open('rb') as input_file:
                    created = True
                    while block := input_file.read(1024 * 1024):
                        if self.stop.is_set():
                            raise InterruptedError('Export interrompu.')
                        output.write(block)
                return target
            except FileExistsError:
                continue
            except Exception:
                if created:
                    target.unlink(missing_ok=True)
                raise
        raise ValueError('Trop de fichiers portent déjà ce nom dans le dossier.')

    def download(self, batch_id):
        batch = self.get(batch_id)
        try:
            for item in batch['items']:
                with self.lock:
                    if self.stop.is_set() or self.library.stopping:
                        break
                    if not item['selected'] or item['status'] == 'done':
                        continue
                    item.update(status='running', error='')
                    self.persist()
                    cached = self.library.jobs.get(item.get('job_id'))
                try:
                    if cached and cached['status'] == 'done' and self.library.file_for(cached):
                        job = cached
                    else:
                        job = self.library.start(item['url'], batch['format'], batch_id=batch_id, item=item, background=False)
                    if self.stop.is_set():
                        with self.lock:
                            item['status'] = 'pending'
                        break
                    if job['status'] != 'done':
                        raise ValueError(job.get('error') or 'La conversion n’a pas abouti.')
                    target = self.export_audio(job, batch, item)
                    with self.lock:
                        item.update(status='done', export_path=str(target), error='')
                        self.persist()
                except Exception as exc:
                    with self.lock:
                        item.update(status='pending' if self.stop.is_set() else 'error', error=str(exc)[-1000:])
                        self.persist()
            with self.lock:
                batch['status'] = 'cancelled' if self.stop.is_set() or self.library.stopping else ('partial' if any(i['selected'] and i['status'] != 'done' for i in batch['items']) else 'done')
        except Exception as exc:
            with self.lock:
                batch.update(status='partial', notice='Le traitement s’est interrompu. ' + str(exc)[-500:])
        finally:
            with self.lock:
                try:
                    self.write_report(batch)
                except OSError:
                    batch['notice'] = 'Le bilan n’a pas pu être enregistré dans le dossier. Les résultats restent visibles ici.'
                self.active_id = None
                self.persist()

    def write_report(self, batch):
        lines = [batch['title'], 'Format : ' + batch['format'].upper(), 'Source : ' + (batch['url'] or 'Export Musique / liste de titres'), '',
                 'Les correspondances Musique sont des propositions fondées sur les métadonnées, pas une identification du signal audio.', '']
        for item in batch['items']:
            label = {'done': 'ENREGISTRÉ', 'error': 'ÉCHEC', 'skipped': 'NON SÉLECTIONNÉ', 'pending': 'À REPRENDRE', 'running': 'À REPRENDRE'}.get(item['status'], item['status'])
            lines += [f"{item['index'] + 1:03d} · {label} · {item['source'].get('artist', '')} - {item['source'].get('title', '')}",
                      item['url'], item.get('export_path') or item.get('error') or item.get('search_error', ''), '']
        target = Path(batch['folder']) / 'Bilan Sillage.txt'
        temp = target.with_suffix('.tmp')
        temp.write_text('\n'.join(lines))
        temp.replace(target)

    def cancel(self, batch_id, reason=''):
        with self.lock:
            batch = self.get(batch_id)
            if self.active_id != batch_id:
                return
            self.stop.set()
            batch['status'] = 'stopping'
            if reason:
                batch['notice'] = reason
            process = self.process
            ids = [j['id'] for j in self.library.jobs.values() if j.get('batch_id') == batch_id and j['status'] in {'queued', 'analyzing', 'downloading', 'converting'}]
        if process and process.poll() is None:
            self.library.stop_process(process)
        for job_id in ids:
            self.library.cancel(job_id)

    def close(self):
        if self.active_id:
            self.cancel(self.active_id)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=8)
