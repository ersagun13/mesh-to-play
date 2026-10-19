"""Loopback-only, token-protected, bounded local jobs. No external resource access."""
import base64
import binascii
import concurrent.futures
import hmac
import json
import mimetypes
import re
import secrets
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from . import __version__
from .formats import MAX_BYTES, ModelError, reject_json_constant
from .pipeline import APP, PROFILES, create_demo, validate_options
from .native import capability
ROOT = Path(__file__).resolve().parents[1]
MAX_REQUEST = (MAX_BYTES + 2) // 3 * 4 + 8192
JOB_RE = re.compile('^[0-9a-f]{32}$')

def validate_request(headers, port, token, post=False):
    origin = f'http://127.0.0.1:{port}'
    if headers.get('Host') != f'127.0.0.1:{port}':
        raise ModelError('Invalid Host. Open the exact printed local URL.')
    if headers.get('Origin') not in (None, origin) or headers.get('Sec-Fetch-Site') == 'cross-site':
        raise ModelError('Cross-origin requests are not allowed.')
    if post:
        supplied_token = headers.get('X-Local-Token', '')
        if not isinstance(supplied_token, str) or not hmac.compare_digest(
            supplied_token.encode('utf8'), token.encode('utf8')
        ):
            raise ModelError('Invalid local session token. Reload the local page.')

def validate_submission(payload):
    if not isinstance(payload, dict) or set(payload) != {'filename', 'content_base64', 'options'}:
        raise ModelError('Expected filename, content_base64 and options only.')
    name = payload['filename']
    encoded = payload['content_base64']
    if not isinstance(name, str) or not 1 <= len(name) <= 240 or '/' in name or ('\\' in name) or any((ord(c) < 32 for c in name)) or (not name.lower().endswith(('.glb', '.obj'))):
        raise ModelError('Filename must be a plain GLB or OBJ basename.')
    if not isinstance(encoded, str) or len(encoded) > MAX_REQUEST - 8192:
        raise ModelError('Upload exceeds 64 MiB.')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as e:
        raise ModelError('Upload is not valid base64.') from e
    if not 0 < len(data) <= MAX_BYTES:
        raise ModelError('Upload must contain 1 byte to 64 MiB.')
    return (name, data, validate_options(payload['options']))

class JobManager:

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        marker = self.workspace / '.meshplay-workspace'
        if marker.exists() and (not marker.is_file() or marker.read_text() != 'Mesh to Play jobs v1\n'):
            raise ModelError('Invalid workspace marker.')
        marker.write_text('Mesh to Play jobs v1\n')
        self.jobs = {}
        self.lock = threading.Lock()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='meshplay')

    def submit(self, name, data, options):
        with self.lock:
            if len(self.jobs) >= 8:
                raise ModelError('This session has reached eight jobs. Use a new workspace or explicit cleanup after stopping the server.')
            if sum((j['status'] in ('queued', 'running') for j in self.jobs.values())) >= 3:
                raise ModelError('Job queue is full; wait for a running job to finish.')
            jid = uuid.uuid4().hex
            folder = self.workspace / jid
            folder.mkdir()
            extension = '.glb' if name.lower().endswith('.glb') else '.obj'
            (folder / ('input' + extension)).write_bytes(data)
            (folder / 'request.json').write_text(
                json.dumps({'filename': name, 'options': options, 'input': 'input' + extension}, allow_nan=False),
                encoding='utf8',
            )
            self.jobs[jid] = {
                'job_id': jid,
                'status': 'queued',
                'progress': 0,
                'message': 'Queued for local processing.',
                'error': None,
                'report': None,
                'artifacts': [],
                'previews': {},
            }
        self.pool.submit(self._run, jid)
        return self.get(jid)

    def _run(self, jid):
        with self.lock:
            self.jobs[jid].update(
                status='running',
                progress=0.1,
                message='Validating and processing geometry locally.',
            )
        folder = self.workspace / jid
        try:
            result = subprocess.run(
                [sys.executable, str(ROOT / 'run.py'), '_worker', str(folder)],
                cwd=str(ROOT),
                capture_output=True,
                timeout=180,
                check=False,
            )
            if result.returncode:
                msg = result.stderr.decode('utf8', errors='replace').strip()[-2000:] or 'Worker stopped before completing. The input may exceed resource limits.'
                raise ModelError(msg)
            report = json.loads((folder / 'result' / 'report.json').read_text(encoding='utf8'))
            prefix = f'/api/jobs/{jid}/files/'
            names = [
                'report.json',
                'report.html',
                'checksums.json',
                'bundle.zip',
                'bundle.zip.sha256',
                'source.glb' if report['source']['format'] == 'GLB' else 'source.obj',
            ] + [r['name'] for r in report['results']]
            artifacts = [{'name': n, 'url': prefix + n, 'label': n} for n in names]
            for r in report['results']:
                r['url'] = prefix + r['name']
            previews = {'before': prefix + 'before.msh'}
            if (folder / 'result' / 'after.msh').is_file():
                previews['after'] = prefix + 'after.msh'
            with self.lock:
                self.jobs[jid].update(
                    status='completed',
                    progress=1,
                    message='Completed. Review measured outputs and limitations.',
                    report=report,
                    artifacts=artifacts,
                    previews=previews,
                )
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            with self.lock:
                self.jobs[jid].update(
                    status='failed',
                    progress=1,
                    message='Processing failed.',
                    error=str(e)[:2000],
                )

    def get(self, jid):
        if not isinstance(jid, str) or not JOB_RE.fullmatch(jid):
            return None
        with self.lock:
            j = self.jobs.get(jid)
            return json.loads(json.dumps(j, allow_nan=False)) if j else None

    def file(self, jid, name):
        j = self.get(jid)
        if not j or j['status'] != 'completed' or Path(name).name != name or ('/' in name) or ('\\' in name):
            return None
        allowed = {a['name'] for a in j['artifacts']} | {'before.msh', 'after.msh'}
        if name not in allowed:
            return None
        p = self.workspace / jid / 'result' / name
        return p if p.is_file() and (not p.is_symlink()) else None

    def close(self, cancel_pending=False):
        # Normal callers can finish queued work deterministically. The HTTP server
        # cancels pending tasks on shutdown; its running child remains wall-bounded.
        self.pool.shutdown(wait=True, cancel_futures=cancel_pending)

class Handler(BaseHTTPRequestHandler):
    server_version = 'MeshToPlay/0.1'

    def log_message(self, *args):
        pass

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def response_headers(self, status, length, mime):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; object-src 'none'; frame-ancestors 'none'",
        )
        self.end_headers()

    def respond(self, status, data, mime='application/json'):
        if mime == 'application/json':
            data = json.dumps(data, allow_nan=False).encode('utf8')
        self.response_headers(status, len(data), mime)
        self.wfile.write(data)

    def respond_file(self, path, mime):
        # Stream large previews and ZIPs instead of copying them into each HTTP thread.
        with path.open('rb') as stream:
            self.response_headers(200, path.stat().st_size, mime)
            shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)

    def policy(self, post=False):
        validate_request(self.headers, self.server.server_port, self.server.token, post)

    def do_GET(self):
        try:
            self.policy()
            path = unquote(urlsplit(self.path).path)
            if path == '/api/config':
                self.respond(
                    200,
                    {
                        'app': APP,
                        'version': __version__,
                        'token': self.server.token,
                        'max_upload_bytes': MAX_BYTES,
                        'profiles': PROFILES,
                        'native': capability(),
                    },
                )
                return
            if path == '/api/demo':
                self.respond(
                    200,
                    {
                        'filename': 'original-reactor.glb',
                        'content_base64': base64.b64encode(create_demo()).decode('ascii'),
                        'options': validate_options({}),
                    },
                )
                return
            parts = path.split('/')
            if len(parts) == 4 and parts[1:3] == ['api', 'jobs']:
                j = self.server.jobs.get(parts[3])
                if j:
                    self.respond(200, j)
                else:
                    self.respond(404, {'error': 'Job not found.'})
                return
            if len(parts) == 6 and parts[1:3] == ['api', 'jobs'] and (parts[4] == 'files'):
                p = self.server.jobs.file(parts[3], parts[5])
                if not p:
                    self.respond(404, {'error': 'Artifact not found.'})
                    return
                mime = 'application/octet-stream' if p.suffix in ('.msh', '.glb', '.obj') else mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
                self.respond_file(p, mime)
                return
            names = {
                '/': 'index.html',
                '/index.html': 'index.html',
                '/app.js': 'app.js',
                '/viewer.js': 'viewer.js',
                '/lib.js': 'lib.js',
                '/styles.css': 'styles.css',
            }
            if path in names:
                p = ROOT / 'web' / names[path]
                if p.is_file():
                    self.respond(200, p.read_bytes(), mimetypes.guess_type(p.name)[0] or 'text/plain')
                    return
            self.respond(404, {'error': 'Not found.'})
        except ModelError as e:
            self.respond(403, {'error': str(e)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def do_POST(self):
        acquired_upload_slot = False
        try:
            self.policy(True)
            if urlsplit(self.path).path != '/api/jobs':
                self.respond(404, {'error': 'Not found.'})
                return
            if self.headers.get('Transfer-Encoding') is not None or self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise ModelError('Use a fixed-length application/json request.')
            length = self.headers.get('Content-Length', '')
            if not length.isascii() or not length.isdigit() or len(length)>10 or not 1 <= int(length) <= MAX_REQUEST:
                raise ModelError('Request length is missing or exceeds the 64 MiB upload limit.')
            acquired_upload_slot = self.server.upload_slots.acquire(blocking=False)
            if not acquired_upload_slot:
                self.respond(429, {'error': 'Another upload is being read; try again shortly.'})
                return
            raw_bytes = self.rfile.read(int(length))
            if len(raw_bytes) != int(length):
                raise ModelError('Incomplete upload.')
            try:
                payload = json.loads(
                    raw_bytes,
                    parse_constant=reject_json_constant,
                )
            except (ValueError, UnicodeError, RecursionError) as e:
                raise ModelError('Invalid request JSON.') from e
            name, data, options = validate_submission(payload)
            j = self.server.jobs.submit(name, data, options)
            self.respond(202, {'job_id': j['job_id'], 'status': j['status']})
        except ModelError as e:
            self.respond(400, {'error': str(e)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        finally:
            if acquired_upload_slot:
                self.server.upload_slots.release()

def cleanup(workspace):
    folder = Path(workspace).resolve()
    marker = folder / '.meshplay-workspace'
    if not marker.is_file() or marker.read_text() != 'Mesh to Play jobs v1\n':
        raise ModelError('Cleanup requires a Mesh to Play job workspace marker.')
    removed = 0
    for p in folder.iterdir():
        if JOB_RE.fullmatch(p.name) and p.is_dir() and (not p.is_symlink()):
            shutil.rmtree(p)
            removed += 1
    return removed

def serve(port=0, no_browser=False, workspace=None):

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 8

        def __init__(self, *args):
            self.request_slots = threading.BoundedSemaphore(8)
            self.upload_slots = threading.BoundedSemaphore(1)
            super().__init__(*args)

        def process_request(self, request, client_address):
            if not self.request_slots.acquire(blocking=False):
                request.close()
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.request_slots.release()
                raise

        def process_request_thread(self, *args):
            try:
                super().process_request_thread(*args)
            finally:
                self.request_slots.release()
    jobs = JobManager(workspace or Path.cwd() / '.meshplay-jobs')
    try:
        server = Server(('127.0.0.1', port), Handler)
    except OSError as e:
        jobs.close(cancel_pending=True)
        raise ModelError('Cannot bind local HTTP server: ' + str(e)) from e
    server.token = secrets.token_urlsafe(32)
    server.jobs = jobs
    url = f'http://127.0.0.1:{server.server_port}'
    print('Mesh to Play: ' + url, flush=True)
    if not no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        jobs.close(cancel_pending=True)
