"""Verify a compiled gateway locally; --runner supports ARM64 under QEMU."""
import argparse
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import threading
import time
import http.server
from container_smoke import Upstream, MODEL, MARKER, client, request, ready, login


def run_test(binary, runner):
    command = ([runner] if runner else []) + [str(Path(binary).resolve())]
    if runner:
        header = Path(binary).read_bytes()[:20]
        assert header[:4] == b'\x7fELF' and struct.unpack('<H', header[18:20])[0] == 183
    upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='opencode-process-') as directory:
        state = Path(directory)
        path = state / 'config.json'
        log_path = state / 'server.log'
        env = os.environ.copy()
        for key in ('ADMIN_PASSWORD', 'ADMIN_USERNAME', 'ZEN_API_KEY', 'ZEN_API_KEYS', 'GO_API_KEY', 'GO_API_KEYS',
                    'SERVER_API_KEY', 'ANONYMOUS', 'LISTEN_ADDRESS', 'WEBUI_LISTEN_ADDRESS'):
            env.pop(key, None)
        password = 'process-test-admin-password'
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            port = reserved.getsockname()[1]
        env.update(ZEABUR='1', PASSWORD=password, PORT=str(port))
        seed = str(Path(__file__).resolve().parent.parent / 'config.example.json')
        subprocess.run(command + ['-init-container-config', '-config', str(path), '-config-seed', seed], env=env, check=True, capture_output=True)
        cfg = json.loads(path.read_text())
        key = cfg['server_keys'][0]
        assert cfg['anonymous'] and cfg['zen_keys'] == [] and cfg['go_keys'] == []
        assert cfg['webui']['password_hash'].startswith('$argon2id$') and not cfg['webui'].get('password')
        cfg.update(zen_keys=['ci-upstream-key'], anonymous=False)
        cfg['upstream'] = {tier: 'http://127.0.0.1:' + str(upstream.server_port) for tier in ('zen', 'go', 'anonymous')}
        cfg['models']['protocols'] = {MODEL: 'chat'}
        cfg['reasoning'] = {'effort': ''}
        path.write_text(json.dumps(cfg))
        cache = {'schema_version': 3, 'updated_at': '2026-10-15T00:00:00Z', 'zen': [MODEL], 'go': [],
                 'native_protocols': {'zen': {MODEL: 'chat'}}, 'unsupported': {}}
        Path(str(path) + '.models.catalog.json').write_text(json.dumps(cache))
        before = path.read_bytes()
        changed = dict(env, PASSWORD='must-not-reset-existing-password')
        subprocess.run(command + ['-init-container-config', '-config', str(path), '-config-seed', 'missing.json'], env=changed, check=True, capture_output=True)
        assert path.read_bytes() == before
        address = '127.0.0.1:' + str(port)
        with log_path.open('w') as logs:
            process = subprocess.Popen(command + ['-config', str(path), '-listen', address, '-web-listen', address],
                                       env=env, stdout=logs, stderr=logs)
            try:
                opener = client()
                base = 'http://' + address
                ready(opener, base)
                assert request(opener, base, '/').status == 200
                assert request(opener, base, '/app.js').status == 200
                assert request(opener, base, '/api/config').status == 401
                assert request(opener, base, '/v1/models').status == 401
                login(opener, base, password)
                with request(opener, base, '/api/config') as response:
                    view = json.load(response)
                    assert view['effective']['listen'] == view['effective']['webui_listen'] == address
                subprocess.run(command + ['-healthcheck'], env=env, check=True, capture_output=True)
                headers = {'Authorization': 'Bearer ' + key}
                with request(opener, base, '/v1/models', headers=headers) as response:
                    assert MODEL in [model['id'] for model in json.load(response)['data']]
                payload = {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Hello'}]}
                with request(opener, base, '/v1/chat/completions', payload, headers) as response:
                    assert response.status == 200, response.read().decode()
                    assert json.load(response)['choices'][0]['message']['content'] == MARKER
                payload['stream'] = True
                started = time.monotonic()
                with request(opener, base, '/v1/chat/completions', payload, headers) as response:
                    assert response.status == 200, response.read().decode()
                    assert MARKER.encode() in response.readline()
                    assert time.monotonic() - started < 1.8
                    assert b'[DONE]' in response.read()
                print('ARM64 executable: initialization, persistence, shared listener, management login, API authentication, JSON forwarding and unbuffered SSE passed.')
            except BaseException:
                print(log_path.read_text())
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    upstream.shutdown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--runner')
    arguments = parser.parse_args()
    run_test(arguments.binary, arguments.runner)
