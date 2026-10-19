import base64, tempfile, unittest, threading
from pathlib import Path
from meshplay.formats import ModelError
from meshplay.server import validate_request, validate_submission, JobManager

class PolicyTest(unittest.TestCase):

    def test_host_origin_token(self):
        headers = {'Host': '127.0.0.1:8123', 'Origin': 'http://127.0.0.1:8123', 'X-Local-Token': 'secret'}
        validate_request(headers, 8123, 'secret', True)
        for key, value in [('Host', 'evil.test:8123'), ('Origin', 'https://evil.test'), ('X-Local-Token', 'wrong'), ('X-Local-Token', 'é')]:
            with self.assertRaises(ModelError):
                validate_request(dict(headers, **{key: value}), 8123, 'secret', True)

    def test_invalid_json_and_options(self):
        with self.assertRaises(ModelError):
            validate_submission({'filename': 'x.obj', 'content_base64': '!!!', 'options': {}})
        with self.assertRaises(ModelError):
            validate_submission({'filename': 'x.obj', 'content_base64': 'YQ==', 'options': {'error': float('inf')}})
        with self.assertRaises(ModelError):
            validate_submission({'filename': '../x.obj', 'content_base64': 'YQ==', 'options': {}})

    def test_job_artifact_traversal(self):
        with tempfile.TemporaryDirectory() as d:
            m = JobManager(Path(d))
            self.assertIsNone(m.get('..'))
            self.assertIsNone(m.file('..', 'report.json'))
            self.assertIsNone(m.file('a' * 32, '../report.json'))
if __name__ == '__main__':
    unittest.main()

class ProcessingJobTest(unittest.TestCase):

    def test_real_subprocess_job_and_registered_files(self):
        with tempfile.TemporaryDirectory() as d:
            manager = JobManager(Path(d))
            data = b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
            job = manager.submit('triangle.obj', data, {'mode': 'inspect'})
            manager.close()
            result = manager.get(job['job_id'])
            self.assertEqual(result['status'], 'completed', result['error'])
            self.assertEqual(result['report']['summary']['triangles'], 1)
            self.assertIsNotNone(manager.file(job['job_id'], 'bundle.zip'))
            self.assertIsNone(manager.file(job['job_id'], 'request.json'))
            self.assertIsNone(manager.file(job['job_id'], '../../request.json'))

    def test_worker_bad_geometry_is_plain_failure(self):
        with tempfile.TemporaryDirectory() as d:
            manager = JobManager(Path(d))
            job = manager.submit('bad.glb', b'bad', {'mode': 'inspect'})
            manager.close()
            result = manager.get(job['job_id'])
            self.assertEqual(result['status'], 'failed')
            self.assertIn('Truncated GLB', result['error'])
            self.assertEqual(result['artifacts'], [])

    def test_http_route_without_network_bind(self):
        import io, json
        from types import SimpleNamespace
        from meshplay.server import Handler
        with tempfile.TemporaryDirectory() as d:
            manager = JobManager(Path(d))
            responses = []
            payload = {'filename': 'x.obj', 'content_base64': base64.b64encode(b'v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n').decode(), 'options': {'mode': 'inspect'}}
            raw = json.dumps(payload).encode()
            handler = SimpleNamespace(server=SimpleNamespace(server_port=8123, token='token', jobs=manager, upload_slots=threading.BoundedSemaphore(1)), path='/api/jobs', headers={'Host': '127.0.0.1:8123', 'X-Local-Token': 'token', 'Content-Type': 'application/json', 'Content-Length': str(len(raw))}, rfile=io.BytesIO(raw), respond=lambda *args: responses.append(args))
            handler.policy = lambda post=False: validate_request(handler.headers, 8123, 'token', post)
            handler.respond_file = lambda path, mime: responses.append((200, path.read_bytes(), mime))
            Handler.do_POST(handler)
            self.assertEqual(responses[-1][0], 202)
            jid = responses[-1][1]['job_id']
            manager.close()
            handler.path = f'/api/jobs/{jid}'
            Handler.do_GET(handler)
            self.assertEqual(responses[-1][1]['status'], 'completed')
            handler.path = f'/api/jobs/{jid}/files/report.json'
            Handler.do_GET(handler)
            self.assertEqual(responses[-1][0], 200)
            handler.path = f'/api/jobs/{jid}/files/%2e%2e%2frequest.json'
            Handler.do_GET(handler)
            self.assertEqual(responses[-1][0], 404)
