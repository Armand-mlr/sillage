import base64
import json
from pathlib import Path
import plistlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from imports import parse_music_file, playlist_url, rank_candidates, candidate
from server import Library

URL = 'https://www.youtube.com/watch?v=YE7VzlLtp-4'


def wait_idle(manager, timeout=8):
    end = time.monotonic() + timeout
    while manager.active_id and time.monotonic() < end:
        time.sleep(.02)
    if manager.active_id:
        raise AssertionError('Queue did not finish')


class ImportParsingTests(unittest.TestCase):
    def test_playlist_url_preserves_only_playlist(self):
        self.assertEqual(playlist_url('https://music.youtube.com/watch?v=YE7VzlLtp-4&list=PL0123456789'), 'https://www.youtube.com/playlist?list=PL0123456789')
        for url in ('https://youtube.com.evil.test/playlist?list=PL0123456789', 'https://a@youtube.com/playlist?list=PL0123456789', URL, 'https://youtube.com/playlist?list=RD0123456789'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                playlist_url(url)

    def test_apple_xml_order_duplicates_unicode_missing_track(self):
        raw = plistlib.dumps({'Tracks': {'1': {'Name': 'Été', 'Artist': 'Artiste', 'Total Time': 203000}, '2': {'Name': 'Deux'}},
                             'Playlists': [{'Name': 'Mes sons', 'Playlist Items': [{'Track ID': 2}, {'Track ID': 1}, {'Track ID': 1}, {'Track ID': 3}]}]})
        title, tracks = parse_music_file(raw, 'export.xml')
        self.assertEqual(title, 'Mes sons')
        self.assertEqual([t['title'] for t in tracks], ['Deux', 'Été', 'Été', ''])
        self.assertEqual(tracks[1]['duration'], 203)

    def test_music_utf16_tsv_and_manual_list(self):
        raw = 'Name\tArtist\tAlbum\tTotal Time\nÉté\tAuteur\tAlbum\t180123\n'.encode('utf-16')
        _, tracks = parse_music_file(raw)
        self.assertEqual(tracks[0]['title'], 'Été')
        self.assertEqual(tracks[0]['duration'], 180.123)
        _, tracks = parse_music_file('Artiste | Titre | Extended Mix\nAutre | Autre titre'.encode())
        self.assertEqual(tracks[0]['version'], 'Extended Mix')
        _, tracks = parse_music_file('Nom\tArtiste\tDurée\nMorceau\tAuteur\t3:25'.encode())
        self.assertEqual(tracks[0]['duration'], 205)

    def test_invalid_files_limits_and_entities(self):
        for raw, name in [(b'', 'x.txt'), (b'Not an export', 'x.txt'), (b'<arbitrary/>', 'x.xml'), (b'a' * (2 * 1024 * 1024 + 1), 'x.txt'),
                          (b'<!DOCTYPE plist [<!ENTITY x "x">]><plist/>', 'x.xml'), (('A|B\n' * 501).encode(), 'x.txt')]:
            with self.subTest(name=name, length=len(raw)), self.assertRaises(ValueError):
                parse_music_file(raw, name)

    def test_match_does_not_choose_first_live_or_cover(self):
        source = {'title': 'Teardrop', 'artist': 'Massive Attack', 'duration': 331}
        records = [dict(id='aaaaaaaaaaa', title='Massive Attack - Teardrop (Live)', channel='Music', duration=400),
                   dict(id='bbbbbbbbbbb', title='Teardrop', channel='Massive Attack - Topic', duration=331),
                   dict(id='ccccccccccc', title='Teardrop (cover)', channel='Someone else', duration=331)]
        ranked = rank_candidates(source, [candidate(r) for r in records])
        self.assertEqual(ranked[0]['video_id'], 'bbbbbbbbbbb')
        self.assertEqual(ranked[0]['confidence'], 'probable')
        self.assertTrue(all(c['confidence'] == 'review' for c in ranked[1:]))
        self.assertEqual(rank_candidates({'title': 'Teardrop', 'artist': '', 'duration': 0}, [candidate(records[1])])[0]['confidence'], 'review')


class BatchFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = Library(self.root / 'data')
        self.manager = self.library.batches
        self.tools = self.root / 'tools'
        self.tools.mkdir()
        self.importer = self.tools / 'import_worker.py'
        self.importer.write_text('''import json,sys
p=json.load(sys.stdin)
for t in p['tracks']:
 print(json.dumps({'type':'match','index':t['index'],'candidates':[{'url':'https://www.youtube.com/watch?v=YE7VzlLtp-4','video_id':'YE7VzlLtp-4','title':'Test','author':'Test','duration':1,'confidence':'probable'}]}), flush=True)
print(json.dumps({'type':'complete'}), flush=True)
''')
        self.worker = self.tools / 'worker.py'
        self.worker.write_text('''import json,sys
from pathlib import Path
url,fmt,directory=sys.argv[1:]
p=Path(directory)/('audio.'+fmt)
p.write_bytes(b'RIFF'+b'x'*100)
print(json.dumps({'status':'done','filename':p.name,'size':p.stat().st_size,'title':'Test'}),flush=True)
''')

    def tearDown(self):
        self.library.shutdown()
        self.temp.cleanup()

    def prepare(self):
        with patch('batches.ROOT', self.tools):
            batch = self.manager.create({'kind': 'music', 'content': base64.b64encode('Auteur | Un\nAuteur | Deux'.encode()).decode(), 'filename': 'Liste.txt'})
            wait_idle(self.manager)
        return self.manager.get(batch['id'])

    def test_queue_exports_and_restart_without_overwriting(self):
        batch = self.prepare()
        self.assertEqual(batch['status'], 'review')
        with patch('server.ROOT', self.tools):
            self.manager.start(batch['id'], {'format': 'wav'})
            wait_idle(self.manager)
        self.assertEqual(batch['status'], 'done')
        outputs = list(Path(batch['folder']).glob('*.wav'))
        self.assertEqual(len(outputs), 2)
        self.assertTrue((Path(batch['folder']) / 'Bilan Sillage.txt').is_file())
        loaded = Library(self.root / 'data')
        self.assertEqual(loaded.batches.get(batch['id'])['status'], 'done')
        self.assertEqual(len(loaded.jobs), 2)
        self.assertEqual(outputs[0].read_bytes(), b'RIFF' + b'x' * 100)
        item = batch['items'][0]
        original = Path(item['export_path'])
        original.write_bytes(b'USER CONTENT')
        other = self.manager.export_audio(self.library.jobs[item['job_id']], batch, item)
        self.assertNotEqual(original, other)
        self.assertEqual(original.read_bytes(), b'USER CONTENT')
        loaded.shutdown()

    def test_cancellation_blocks_other_jobs_and_can_resume(self):
        batch = self.prepare()
        self.worker.write_text('import time; print(\'{"status":"downloading","progress":10}\',flush=True); time.sleep(100)')
        with patch('server.ROOT', self.tools):
            self.manager.start(batch['id'], {'format': 'mp3'})
            end = time.monotonic() + 5
            while not self.library.processes and time.monotonic() < end:
                time.sleep(.02)
            with self.assertRaises(RuntimeError):
                self.library.start(URL, 'wav')
            self.manager.cancel(batch['id'])
            wait_idle(self.manager)
        self.assertEqual(batch['status'], 'cancelled')
        self.assertEqual(batch['items'][0]['status'], 'pending')
        self.assertFalse(self.library.processes)
        loaded = Library(self.root / 'data')
        self.assertEqual(loaded.batches.get(batch['id'])['items'][0]['status'], 'pending')
        loaded.shutdown()

    def test_one_failed_video_does_not_block_later_items(self):
        batch = self.prepare()
        batch['items'][0]['url'] = 'https://www.youtube.com/watch?v=aaaaaaaaaaa'
        code = self.worker.read_text()
        self.worker.write_text(code.replace("p=Path(directory)", "\nif 'aaaaaaaaaaa' in url:\n print(json.dumps({'status':'error','error':'Indisponible'}),flush=True); sys.exit(1)\np=Path(directory)"))
        with patch('server.ROOT', self.tools):
            self.manager.start(batch['id'], {'format': 'wav'})
            wait_idle(self.manager)
        self.assertEqual(batch['status'], 'partial')
        self.assertEqual([i['status'] for i in batch['items']], ['error', 'done'])
        done_path = batch['items'][1]['export_path']
        batch['items'][0]['url'] = URL
        with patch('server.ROOT', self.tools):
            self.manager.start(batch['id'], {})
            wait_idle(self.manager)
        self.assertEqual(batch['status'], 'done')
        self.assertEqual(batch['items'][1]['export_path'], done_path)
        self.assertEqual(len(list(Path(batch['folder']).glob('*.wav'))), 2)

    def test_failed_export_retries_cached_audio(self):
        batch = self.prepare()
        batch['items'][1]['selected'] = False
        with patch('server.ROOT', self.tools), patch.object(self.manager, 'export_audio', side_effect=OSError('Disk full')):
            self.manager.start(batch['id'], {})
            wait_idle(self.manager)
        self.assertEqual(batch['status'], 'partial')
        before = len(self.library.jobs)
        with patch.object(self.library, 'start', side_effect=AssertionError('Must not download twice')):
            self.manager.start(batch['id'], {})
            wait_idle(self.manager)
        self.assertEqual(batch['status'], 'done')
        self.assertEqual(len(self.library.jobs), before)

    def test_bad_selection_does_not_mutate_saved_items(self):
        batch = self.prepare()
        original = json.dumps(batch)
        with self.assertRaises(ValueError):
            self.manager.start(batch['id'], {'items': [{'index': 0, 'selected': True, 'url': URL}, {'index': 1, 'selected': True, 'url': 'file:///etc/passwd'}]})
        self.assertEqual(json.dumps(batch), original)

    def test_selection_draft_survives_restart_and_validates_atomically(self):
        batch = self.prepare()
        choices = [{'index': 0, 'selected': False, 'url': URL},
                   {'index': 1, 'selected': True, 'url': 'https://youtu.be/TLkA0RELQ1g'}]
        self.manager.save_selection(batch['id'], {'items': choices, 'format': 'mp3', 'destination': str(self.root)})
        loaded = Library(self.root / 'data')
        try:
            restored = loaded.batches.get(batch['id'])
            self.assertFalse(restored['items'][0]['selected'])
            self.assertEqual(restored['items'][1]['url'], choices[1]['url'])
            self.assertEqual(restored['format'], 'mp3')
            self.assertEqual(restored['destination'], str(self.root))
        finally:
            loaded.shutdown()
        original = json.dumps(batch)
        choices[0]['selected'] = True
        choices[1]['index'] = 999
        with self.assertRaises(ValueError):
            self.manager.save_selection(batch['id'], {'items': choices})
        self.assertEqual(json.dumps(batch), original)

    def test_missing_export_is_recreated_without_downloading(self):
        batch = self.prepare()
        with patch('server.ROOT', self.tools):
            self.manager.start(batch['id'], {})
            wait_idle(self.manager)
        missing = Path(batch['items'][0]['export_path'])
        missing.unlink()
        loaded = Library(self.root / 'data')
        try:
            restored = loaded.batches.get(batch['id'])
            self.assertEqual(restored['status'], 'partial')
            self.assertEqual([i['status'] for i in restored['items']], ['error', 'done'])
            with patch.object(loaded, 'start', side_effect=AssertionError('Already converted')):
                loaded.batches.start(batch['id'], {})
                wait_idle(loaded.batches)
            self.assertEqual(restored['status'], 'done')
            self.assertTrue(missing.is_file())
        finally:
            loaded.shutdown()


if __name__ == '__main__':
    unittest.main(verbosity=2)
