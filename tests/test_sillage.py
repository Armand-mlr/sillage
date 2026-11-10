import http.client
import base64
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio import convert_audio, safe_filename, youtube_url
from server import Handler, Library, ThreadingHTTPServer


class ValidationTests(unittest.TestCase):
    def test_supported_urls_are_canonical_single_videos(self):
        for value in ('youtu.be/YE7VzlLtp-4?t=2', 'https://www.youtube.com/watch?v=YE7VzlLtp-4&list=ABC',
                      'https://music.youtube.com/watch?v=YE7VzlLtp-4', 'https://m.youtube.com/shorts/YE7VzlLtp-4',
                      'https://www.youtube.com/live/YE7VzlLtp-4'):
            with self.subTest(url=value):
                self.assertEqual(youtube_url(value), 'https://www.youtube.com/watch?v=YE7VzlLtp-4')

    def test_rejects_other_hosts_paths_and_credentials(self):
        for value in ('http://127.0.0.1:8080/', 'https://youtube.com.evil.org/watch?v=YE7VzlLtp-4',
                      'https://user:pass@youtube.com/watch?v=YE7VzlLtp-4', 'file:///etc/passwd',
                      'https://youtube.com/playlist?list=ABC', 'https://youtube.com/watch?v=short',
                      'https://youtube.com:9000/watch?v=YE7VzlLtp-4', '', None, ['url']):
            with self.subTest(url=value), self.assertRaises(ValueError):
                youtube_url(value)

    def test_safe_names_preserve_unicode_without_paths(self):
        self.assertEqual(safe_filename('../Été / test: 2026?'), 'Été  test 2026')
        self.assertEqual(safe_filename('...'), 'Audio YouTube')
        self.assertLessEqual(len(safe_filename('x' * 300)), 110)


class EncodingTests(unittest.TestCase):
    def test_real_wav_and_mp3_encoders(self):
        from mutagen.mp3 import MP3
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / 'source.wav'
            with wave.open(str(source), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
                frames = b''.join(struct.pack('<hh', int(10000 * math.sin(2 * math.pi * 440 * n / 48000)),
                                             int(8000 * math.sin(2 * math.pi * 880 * n / 48000))) for n in range(24000))
                stream.writeframes(frames)
            wav = convert_audio(source, root / 'résultat.wav', 'wav', .5)
            with wave.open(str(wav), 'rb') as result:
                self.assertEqual((result.getnchannels(), result.getsampwidth(), result.getframerate(), result.getnframes()), (2, 2, 48000, 24000))
                self.assertEqual(result.readframes(24000), frames)
            mp3 = convert_audio(source, root / 'résultat.mp3', 'mp3', .5)
            info = MP3(mp3).info
            self.assertEqual(info.bitrate, 320000)
            self.assertEqual(info.channels, 2)
            self.assertAlmostEqual(info.length, .5, delta=.1)

    def test_invalid_input_fails_without_success(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):
                convert_audio(Path(folder) / 'missing', Path(folder) / 'out.wav', 'wav')


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.library = Library(self.temp.name)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.library = self.library
        self.library.origin = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.job_id = 'a' * 32
        folder = Path(self.temp.name) / self.job_id
        folder.mkdir()
        self.payload = b'RIFF' + bytes(range(256)) * 4
        (folder / 'Été.wav').write_bytes(self.payload)
        self.library.jobs[self.job_id] = {'id': self.job_id, 'status': 'done', 'filename': 'Été.wav', 'format': 'wav', 'created': '2026-11-10', 'size': len(self.payload)}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, method='GET', body=None, headers=None, auth=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        request_headers = {'Cookie': 'sillage=' + self.library.token} if auth else {}
        request_headers.update(headers or {})
        conn.request(method, path, json.dumps(body) if body is not None else None, request_headers)
        response = conn.getresponse()
        result = (response.status, dict(response.getheaders()), response.read())
        conn.close()
        return result

    def test_bootstrap_auth_and_private_data(self):
        self.assertEqual(self.request('/api/state', auth=False)[0], 401)
        status, headers, _ = self.request('/?key=' + self.library.token, auth=False)
        self.assertEqual(status, 303)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        status, _, html = self.request('/')
        self.assertEqual(status, 200)
        self.assertNotIn(b'__SILLAGE_TOKEN__', html)

    def test_cross_origin_host_and_csrf_are_denied(self):
        self.assertEqual(self.request('/api/state', headers={'Host': 'evil.test'})[0], 403)
        self.assertEqual(self.request('/api/jobs', 'POST', {'url': 'x'})[0], 403)
        self.assertEqual(self.request('/api/jobs', 'POST', {}, {'X-Sillage-Token': self.library.token, 'Origin': 'https://evil.test'})[0], 403)

    def test_validation_and_no_path_traversal(self):
        self.assertEqual(self.request('/api/jobs', 'POST', {'url': 'https://example.com'}, {'X-Sillage-Token': self.library.token})[0], 400)
        self.assertEqual(self.request('/api/jobs', 'POST', [], {'X-Sillage-Token': self.library.token})[0], 400)
        for path in ('/../../server.py', '/data/history.json', '/api/download/../../etc/passwd'):
            self.assertEqual(self.request(path)[0], 404)

    def test_download_has_real_bytes_and_unicode_filename(self):
        status, headers, content = self.request('/api/download/' + self.job_id)
        self.assertEqual(status, 200)
        self.assertEqual(content, self.payload)
        self.assertEqual(headers['Content-Type'], 'audio/wav')
        self.assertIn("filename*=UTF-8''%C3%89t%C3%A9.wav", headers['Content-Disposition'])

    def test_audio_byte_ranges_and_invalid_ranges(self):
        path = '/api/audio/' + self.job_id
        status, headers, body = self.request(path, headers={'Range': 'bytes=4-15'})
        self.assertEqual(status, 206)
        self.assertEqual(body, self.payload[4:16])
        self.assertEqual(headers['Content-Range'], f'bytes 4-15/{len(self.payload)}')
        self.assertEqual(self.request(path, headers={'Range': 'bytes=-8'})[2], self.payload[-8:])
        for value in ('bytes=99999-', 'bytes=50-3', 'bytes=-', 'bytes=0-5,8-10', 'bytes=-0'):
            self.assertEqual(self.request(path, headers={'Range': value})[0], 416)

    def test_completed_library_survives_restart(self):
        self.library.persist()
        loaded = Library(self.temp.name)
        self.assertEqual(loaded.jobs[self.job_id]['filename'], 'Été.wav')
        self.assertEqual(loaded.file_for(loaded.jobs[self.job_id]).read_bytes(), self.payload)

    def test_import_api_auth_validation_and_retrieval(self):
        headers = {'X-Sillage-Token': self.library.token}
        payload = {'kind': 'music', 'filename': 'Playlist.txt', 'content': base64.b64encode('Auteur | Titre'.encode()).decode()}
        self.assertEqual(self.request('/api/imports', 'POST', payload, auth=False)[0], 403)
        with patch.object(self.library.batches, 'launch'):
            status, _, raw = self.request('/api/imports', 'POST', payload, headers)
        self.assertEqual(status, 202)
        batch = json.loads(raw)
        self.assertEqual(batch['items'][0]['source']['artist'], 'Auteur')
        self.assertEqual(self.request('/api/imports/' + batch['id'])[0], 200)
        self.assertEqual(self.request('/api/imports/' + 'b' * 32)[0], 404)
        self.assertEqual(self.request('/api/imports', 'POST', {'kind': 'music', 'content': '!bad'}, headers)[0], 400)
        self.assertEqual(self.request('/api/imports/' + batch['id'] + '/start', 'POST', {'items': []}, headers)[0], 400)
        self.library.batches.get(batch['id'])['status'] = 'review'
        choices = {'items': [{'index': 0, 'selected': False, 'url': ''}], 'format': 'mp3'}
        self.assertEqual(self.request('/api/imports/' + batch['id'] + '/selection', 'POST', choices, headers)[0], 200)
        self.assertEqual(self.library.batches.get(batch['id'])['format'], 'mp3')

    def test_import_body_limit_and_new_asset(self):
        self.assertEqual(self.request('/api/imports', 'POST', {}, {'X-Sillage-Token': self.library.token, 'Content-Length': str(3 * 1024 * 1024 + 1)})[0], 413)
        status, _, script = self.request('/batch.js')
        self.assertEqual(status, 200)
        self.assertIn(b'window.Batches', script)


class CancellationTests(unittest.TestCase):
    def test_real_worker_process_is_stopped_and_temp_files_removed(self):
        real_popen = subprocess.Popen
        def launch_stub(*args, **kwargs):
            return real_popen([sys.executable, '-c', 'import time; print(\'{"status":"downloading","progress":10}\', flush=True); time.sleep(120)'], **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            library = Library(directory)
            with patch('server.subprocess.Popen', side_effect=launch_stub):
                job = library.start('https://youtu.be/YE7VzlLtp-4', 'wav')
                limit = time.monotonic() + 5
                while not library.processes and time.monotonic() < limit:
                    time.sleep(.02)
                self.assertIn(job['id'], library.processes)
                process = library.processes[job['id']]
                with self.assertRaises(RuntimeError):
                    library.start('https://youtu.be/YE7VzlLtp-4', 'mp3')
                library.cancel(job['id'])
                while job['id'] in library.processes and time.monotonic() < limit:
                    time.sleep(.02)
                self.assertIsNotNone(process.poll())
                self.assertEqual(library.jobs[job['id']]['status'], 'cancelled')
                self.assertFalse((Path(directory) / job['id']).exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
