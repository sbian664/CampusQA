"""Isolated image acceptance: synthetic data, internal network, no cloud credentials."""
import argparse
import base64
import secrets
import http.cookiejar
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request


def run(*command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def capture(*command):
    return subprocess.check_output(command, text=True, stderr=subprocess.PIPE).strip()


def request_headers(content_type='application/json', csrf='', token=None):
    headers = {'Content-Type': content_type}
    if csrf:
        headers['X-CSRF-Token'] = csrf
    if token:
        headers['Authorization'] = 'Bearer ' + token
    return headers


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18080)
    args = parser.parse_args()
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    data = work / 'data'
    (data / 'documents').mkdir(parents=True)
    data.chmod(0o777)
    (data / 'documents').chmod(0o777)
    (data / 'documents/offline-guide.txt').write_text('OFFLINE VALIDATION GUIDE\nThe fictional library test room opens at 09:00 and closes at 18:00.\n离线验收测试图书馆开放时间为09:00至18:00。\n')
    name = 'campusqa-offline-' + hashlib.sha256(str(work).encode()).hexdigest()[:10]
    network = name + '-network'
    volume = name + '-data'
    records = []
    def passed(test, **details):
        record = {'test': test, 'passed': True, **details}
        records.append(record)
        (work / 'results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(record, ensure_ascii=False), flush=True)
    base = f'http://127.0.0.1:{args.port}'
    csrf = ''
    users = {}
    def request(path, method='GET', body=None, content_type='application/json', identity='a'):
        headers = request_headers(content_type, csrf, users.get(identity, {}).get('token'))
        raw = json.dumps(body).encode() if isinstance(body, dict) else body
        (work / 'request-body').write_bytes(raw or b'')
        result = capture('docker', 'exec', name, 'python', '/app/data/tmp/http_client.py',
                         path, method, json.dumps(headers), '1' if raw is not None else '0')
        return json.loads(result)
    def ready():
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            try:
                if request('/api/health')[0] == 200:
                    return
            except (OSError, TimeoutError, subprocess.SubprocessError):
                pass
            time.sleep(2)
        raise TimeoutError('Application did not become healthy')
    def job(kind):
        status, value = request('/api/admin/kb/jobs/' + kind, 'POST')
        assert status == 202, (status, value)
        job_id = value['job_id']
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status, value = request('/api/admin/kb/jobs/' + job_id)
            if value['status'] not in {'queued', 'running'}:
                return value
            time.sleep(1)
        raise TimeoutError('Index job timed out')
    def multipart(filename, content):
        boundary = 'campusqa-acceptance-boundary'
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode() + content + f'\r\n--{boundary}--\r\n'.encode()
        return body, 'multipart/form-data; boundary=' + boundary
    def tool(command, *extra, target=None):
        return capture('docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none', '--user', '0',
                       '-v', f'{target or volume}:/app/data', '-v', f'{work}:/verification', args.image, command, *extra)
    try:
        run('docker', 'network', 'create', '--internal', network)
        run('docker', 'volume', 'create', volume)
        (data / 'tmp').mkdir(exist_ok=True)
        (data / 'tmp/http_client.py').write_text('''import sys,json,urllib.request,urllib.error,http.cookiejar
from pathlib import Path
jar=http.cookiejar.LWPCookieJar('/app/data/tmp/http-cookies.txt')
try: jar.load(ignore_discard=True)
except FileNotFoundError: pass
client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
raw=Path('/verification/request-body').read_bytes() if sys.argv[4]=='1' else None
request=urllib.request.Request('http://127.0.0.1:8000'+sys.argv[1],data=raw,method=sys.argv[2],headers=json.loads(sys.argv[3]))
try: response=client.open(request,timeout=240)
except urllib.error.HTTPError as error: response=error
content=response.read()
try: value=json.loads(content)
except (ValueError,UnicodeDecodeError): value=content.decode(errors='replace')
jar.save(ignore_discard=True)
print(json.dumps([response.status,value]))
''')
        # This is a synthetic fixture only; make its temporary request directory writable.
        (data / 'tmp').chmod(0o777)
        run('docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none', '--user', '0', '--entrypoint', 'python',
            '-v', f'{volume}:/target', '-v', f'{data}:/source:ro', args.image, '-c',
            'import os,shutil; shutil.copytree("/source","/target",dirs_exist_ok=True); [(os.chown(r,10001,10001), [os.chown(os.path.join(r,n),10001,10001) for n in fs]) for r,ds,fs in os.walk("/target")]')
        started = time.monotonic()
        run('docker', 'run', '-d', '--name', name, '--platform', 'linux/amd64', '--network', network,
            '-v', f'{volume}:/app/data', '-v', f'{work}:/verification:ro', '-v', f'{args.models.resolve()}:/models:ro',
            '-e', 'DEEPSEEK_API_KEY=offline-validation-placeholder', '-e', 'ADMIN_INITIAL_PASSWORD=offline-validation-only',
            '-e', 'ADMIN_COOKIE_SECURE=false', '-e', 'OMP_NUM_THREADS=2',
            '-e', 'WECHAT_APP_ID=offline-fixture-app', '-e', 'WECHAT_APP_SECRET=offline-fixture-only',
            '-e', 'USER_LLM_ENCRYPTION_KEY=' + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(), args.image)
        ready()
        # Seed only this disposable database via an internal tool, not a mock-login API.
        # Real WeChat login needs network and must be verified separately before release.
        users.update(json.loads(capture('docker', 'exec', name, 'python', '-c',
            "import json; from src.identity import UserStore; s=UserStore('/app/data/admin_control.db'); "
            "print(json.dumps({k:s.login('offline-fixture-app','offline-'+k) for k in ('a','b')}))")))
        for path in ('/api/kb/stats', '/api/sessions', '/api/llm-config'):
            assert request(path, identity=None)[0] == 401, path
        assert request('/api/chat', 'POST', {'message': 'anonymous'}, identity=None)[0] == 401
        assert request('/api/admin/auth/me')[0] == 401  # A user token is not an admin session.
        passed('anonymous_business_routes_and_user_to_admin_boundary_rejected')
        assert request('/api/kb/stats')[1]['embeddings_dim'] == json.loads((args.models / 'manifest.json').read_text()).get('embedding_dimension', 384)
        passed('offline_start_and_real_embedding_index', startup_seconds=round(time.monotonic()-started, 2), stats=request('/api/kb/stats')[1])
        assert request('/admin/')[0] == 200
        status, login = request('/api/admin/auth/login', 'POST', {'username': 'admin', 'password': 'offline-validation-only'})
        assert status == 200, login
        csrf = login['csrf_token']
        passed('static_console_admin_login_and_csrf')
        status, settings = request('/api/admin/runtime-config', 'PUT', {'max_tokens': 512, 'bm25_weight': 0.4, 'agent_mode': False})
        assert status == 200 and settings['max_tokens'] == 512 and settings['bm25_weight'] == 0.4
        passed('runtime_settings_persist')
        status, search = request('/api/admin/kb/search', 'POST', {'query': 'library opens', 'top_k': 2, 'rerank_enabled': True})
        assert status == 200 and search['channels']['reranked'], (status, search)
        passed('real_crossencoder_inference', reranked_hits=len(search['channels']['reranked']))
        payload, content_type = multipart('uploaded.txt', b'An offline upload validation document.')
        status, doc = request('/api/admin/kb/documents/upload', 'POST', payload, content_type)
        assert status == 201, (status, doc)
        doc_id = doc['document_id']
        payload, content_type = multipart('uploaded.txt', b'Replacement text for the offline test.')
        assert request(f'/api/admin/kb/documents/{doc_id}/replace', 'POST', payload, content_type)[0] == 202
        payload, content_type = multipart('too-large.txt', b'x' * (20 * 1024 * 1024 + 1))
        assert request('/api/attachments/parse', 'POST', payload, content_type)[0] == 413
        passed('upload_replace_and_20mb_boundary', document_id=doc_id)
        # A loopback-only deterministic LLM verifies HTTP chat plumbing, not model answer quality.
        stub = work / 'stub.py'
        stub.write_text('''from http.server import BaseHTTPRequestHandler,HTTPServer
import json
class Handler(BaseHTTPRequestHandler):
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert any('09:00' in str(m.get('content','')) for m in body['messages'])
  result=json.dumps({'choices':[{'message':{'content':'Offline fixture: library opens at 09:00.'},'finish_reason':'stop'}],'usage':{'prompt_tokens':20,'completion_tokens':10}}).encode()
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(result)
HTTPServer(('127.0.0.1',9999),Handler).serve_forever()
''')
        run('docker', 'exec', '-d', name, 'python', '/verification/stub.py')
        status, _ = request('/api/admin/llm-config', 'PUT', {'provider': 'local', 'model': 'offline-fixture', 'base_url': 'http://127.0.0.1:9999/v1', 'api_key': ''})
        assert status == 200
        status, chat = request('/api/chat', 'POST', {'message': 'What time does the test library open?', 'rerank_enabled': True})
        assert status == 200 and chat['effective_mode'] == 'rag' and '09:00' in chat['response'], (status, chat)
        session_id = chat['session_id']
        passed('http_rag_with_real_retrieval_and_loopback_llm_fixture', session_id=session_id)
        assert request('/api/session/' + session_id, identity='b')[0] == 404
        assert request('/api/session/' + session_id, identity=None)[0] == 401
        assert request('/api/sessions', identity='b')[1]['sessions'] == []
        assert request('/api/llm-config', 'PUT', {'provider': 'local', 'model': 'forbidden',
            'base_url': 'http://127.0.0.1:9999/v1', 'api_key': ''})[0] == 400
        passed('authenticated_sessions_remain_isolated_and_personal_local_is_rejected')
        # No gateway exists on an internal Docker network; test an explicit external connection too.
        no_network = capture('docker', 'exec', name, 'python', '-c',
            "import socket; s=socket.socket(); s.settimeout(2);\ntry: s.connect(('1.1.1.1',443)); print('UNEXPECTED_EXTERNAL_ACCESS')\nexcept OSError: print('EXTERNAL_ACCESS_BLOCKED')")
        assert 'EXTERNAL_ACCESS_BLOCKED' in no_network
        passed('external_network_blocked')
        # A second runtime must reject shared state before importing the server.
        duplicate = subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
            '-v', f'{volume}:/app/data', '-v', f'{args.models.resolve()}:/models:ro', args.image, 'serve'], capture_output=True, text=True, timeout=30)
        assert duplicate.returncode != 0 and 'Data is in use' in duplicate.stderr
        passed('duplicate_instance_rejected')
        # Healthy active generations remain searchable after a failed shadow rebuild.
        assert job('rebuild')['status'] == 'succeeded'
        active = request('/api/admin/kb/versions')[1]['active']
        assert active != 'legacy'
        run('docker', 'exec', name, 'python', '-c', "from pathlib import Path; Path('/app/data/documents/broken.pdf').write_bytes(b'%PDF-broken offline validation fixture')")
        failed_job = job('rebuild')
        assert failed_job['status'] in {'partial_failed', 'failed'} and failed_job['result']['failed'] >= 1, failed_job
        assert request('/api/admin/kb/versions')[1]['active'] == active
        assert request('/api/kb/search', 'POST', {'query': 'library'})[0] == 200
        assert request('/api/health')[0] == 200
        passed('failed_shadow_rebuild_preserves_healthy_active_generation', status=failed_job['status'])
        run('docker', 'exec', name, 'python', '-c', "from pathlib import Path; Path('/app/data/documents/broken.pdf').unlink()")
        run('docker', 'restart', '--time', '180', name)
        ready()
        assert request('/api/admin/kb/versions')[1]['active'] == active
        assert request('/api/kb/search', 'POST', {'query': 'library'})[0] == 200
        assert request('/api/session/' + session_id)[0] == 200
        assert request('/api/session/' + session_id, identity='b')[0] == 404
        assert request('/api/admin/runtime-config')[1]['max_tokens'] == 512
        assert request(f'/api/admin/kb/documents/{doc_id}')[0] == 200
        passed('restart_preserves_active_generation_user_isolation_admin_settings_and_document_id')
        assert job('rebuild')['status'] == 'succeeded'
        assert request('/api/kb/search', 'POST', {'query': 'library'})[0] == 200
        passed('repaired_source_allows_full_rebuild')
        # Retained recovery files use the persistent TMPDIR, not the container layer.
        run('docker', 'exec', name, 'python', '-c', "from pathlib import Path; p=Path('/app/data/tmp/campusqa_rollback_acceptance'); p.mkdir(); (p/'backup').write_text('synthetic recovery fixture')")
        run('docker', 'stop', '--time', '180', name)
        tool('backup', '--destination', '/verification/backup')
        restored = work / 'restored'
        restored.mkdir()
        tool('restore', '--source', '/verification/backup', target=restored)
        assert (restored / 'tmp/campusqa_rollback_acceptance/backup').read_text() == 'synthetic recovery fixture'
        assert (restored / 'cache' / (session_id + '.json')).exists()
        passed('stopped_backup_and_restore_preserve_sessions_and_recovery_files')
        guarded = subprocess.run(['docker', 'run', '--rm', '--platform', 'linux/amd64',
            '--network', 'none', '--user', '0', '-v', f'{restored}:/app/data', args.image,
            'migrate', '--old-root', '/app/data/documents', '--apply'], capture_output=True, text=True)
        assert guarded.returncode != 0 and 'Managed index migration is unsupported' in guarded.stderr
        passed('managed_generation_migration_is_rejected')
        # Migration runs on another synthetic data copy and never touches the active service data.
        migration = work / 'migration'
        (migration / 'documents').mkdir(parents=True)
        (migration / 'cache').mkdir()
        (migration / 'documents/guide.txt').write_text('Migrated offline guide')
        (migration / 'cache/kb_metadata.json').write_text(json.dumps({'/old/documents/guide.txt': {'document_id': 'a' * 24, 'chunk_ids': []}}))
        tool('migrate', '--old-root', '/old/documents', target=migration)
        tool('migrate', '--old-root', '/old/documents', '--apply', target=migration)
        mapped = json.loads((migration / 'cache/kb_metadata.json').read_text())
        assert mapped['/app/data/documents/guide.txt']['document_id'] == 'a' * 24
        assert json.loads((migration / 'cache/index_state.json').read_text())['status'] == 'repair_required'
        passed('stopped_path_migration_preserves_identity_and_closes_gate')
    finally:
        logs = subprocess.run(['docker', 'logs', name], capture_output=True, text=True)
        (work / 'container.log').write_text(logs.stdout + logs.stderr)
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True)
        subprocess.run(['docker', 'volume', 'rm', volume], capture_output=True)


if __name__ == '__main__':
    main()
