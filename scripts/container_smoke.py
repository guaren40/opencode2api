"""Smoke-test real ARM64 containers without paid upstream API credentials."""
import http.cookiejar
import http.server
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.error
import urllib.request

IMAGE = os.getenv('TEST_IMAGE', 'opencode2api:test')
NAME = 'opencode2api-smoke'
VOLUME = 'opencode2api-smoke-data'
PASSWORD = 'ci-management-password'
STATE = '/var/lib/opencode2api'
MODEL = 'ci-deployment-model'
MARKER = 'arm64-gateway-ok'


def docker(*args, data=None, check=True):
    result = subprocess.run(['docker', *args], input=data, text=True, capture_output=True, check=check)
    return result.stdout.strip()


class Upstream(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({'object': 'list', 'data': [{'id': MODEL, 'object': 'model'}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.headers.get('Authorization') != 'Bearer ci-upstream-key' or body.get('model') != MODEL:
            self.send_error(401)
            return
        if body.get('stream'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            chunk = {'id': 'chatcmpl-ci', 'object': 'chat.completion.chunk', 'created': 1, 'model': MODEL,
                     'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': MARKER}, 'finish_reason': None}]}
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.flush()
            # A real pause makes buffering visible to the downstream test.
            time.sleep(2)
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        else:
            response = {'id': 'chatcmpl-ci', 'object': 'chat.completion', 'created': 1, 'model': MODEL,
                        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': MARKER}, 'finish_reason': 'stop'}],
                        'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3}}
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)


def start(port='8080', password=PASSWORD, extra=()):
    docker('run', '-d', '--name', NAME, '-p', '127.0.0.1::' + port,
           '-v', VOLUME + ':' + STATE, '-e', 'PORT=' + port,
           '-e', 'ZEABUR=1', '-e', 'PASSWORD=' + password, *extra, IMAGE)
    published = docker('port', NAME, port + '/tcp').rsplit(':', 1)[1]
    return 'http://127.0.0.1:' + published


def client():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                      urllib.request.ProxyHandler({}))


def request(opener, base, path, data=None, headers=None, method=None):
    headers = dict(headers or {})
    if data is not None:
        data = json.dumps(data).encode()
        headers['Content-Type'] = 'application/json'
    query = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        return opener.open(query, timeout=45)
    except urllib.error.HTTPError as error:
        return error


def ready(opener, base):
    for _ in range(150):
        try:
            with request(opener, base, '/livez') as response:
                if response.status == 200:
                    return
        except (OSError, TimeoutError):
            pass
        time.sleep(1)
    raise AssertionError('Container did not start')


def read_config():
    return json.loads(docker('exec', NAME, 'cat', STATE + '/config.json'))


def login(opener, base, password=PASSWORD):
    with request(opener, base, '/api/auth/login', {'username': 'admin', 'password': password}) as response:
        assert response.status == 200, response.read().decode()
        return json.load(response)


def main():
    assert docker('image', 'inspect', IMAGE, '--format', '{{.Os}}/{{.Architecture}}') == 'linux/arm64'
    assert docker('run', '--rm', '--entrypoint', 'uname', IMAGE, '-m') == 'aarch64'
    # The entrypoint must reject a non-ARM64 root filesystem architecture report.
    result = subprocess.run(['docker', 'run', '--rm', '--entrypoint', 'sh', IMAGE, '-c',
                             'mkdir /tmp/arch-test; printf "#!/bin/sh\\necho x86_64\\n" > /tmp/arch-test/uname; chmod +x /tmp/arch-test/uname; PATH=/tmp/arch-test:$PATH /usr/local/bin/docker-entrypoint'],
                            capture_output=True, text=True)
    assert result.returncode != 0 and 'requires an ARM64' in result.stderr
    upstream = http.server.ThreadingHTTPServer(('0.0.0.0', 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    opener = client()
    docker('rm', '-f', NAME, check=False)
    docker('volume', 'rm', '-f', VOLUME, check=False)
    base = start()
    ready(opener, base)
    assert request(opener, base, '/').status == 200
    assert request(opener, base, '/app.js').status == 200
    assert request(opener, base, '/styles.css').status == 200
    assert request(opener, base, '/api/config').status == 401
    assert request(opener, base, '/v1/models').status == 401
    assert request(opener, base, '/api/auth/login', {'username': 'admin', 'password': 'wrong-password'}).status == 401
    session = login(opener, base)
    cfg = read_config()
    assert cfg['anonymous'] and cfg['zen_keys'] == [] and cfg['go_keys'] == []
    assert cfg['webui']['listen'] == cfg['listen'] == '0.0.0.0:8080'
    assert not cfg['webui'].get('password') and cfg['webui']['password_hash'].startswith('$argon2id$')
    api_key = cfg['server_keys'][0]
    assert api_key.startswith('sk-oc2a-')
    assert docker('exec', NAME, 'stat', '-c', '%a', STATE + '/config.json') == '600'
    docker('exec', NAME, '/usr/local/bin/opencode2api', '-healthcheck')
    with request(opener, base, '/v1/models', headers={'Authorization': 'Bearer ' + api_key}) as response:
        assert response.status == 200
    print('Fresh deployment: generated credentials, shared UI/API listener and authentication passed.')
    # Configure a deterministic upstream through the authenticated management API.
    gateway_ip = docker('network', 'inspect', 'bridge', '--format', '{{(index .IPAM.Config 0).Gateway}}')
    fixture = f'http://{gateway_ip}:{upstream.server_port}'
    with request(opener, base, '/api/config') as response:
        update = json.load(response)
    update.pop('effective', None)
    update.pop('restart_required_fields', None)
    for field in ('server_keys', 'zen_keys', 'go_keys', 'proxies'):
        update[field] = [{'id': item['id']} for item in update[field]]
    update['zen_keys'] = [{'value': 'ci-upstream-key'}]
    update['go_keys'] = []
    update['anonymous'] = False
    update['upstream'] = {'zen': fixture, 'go': fixture, 'anonymous': fixture}
    update['models']['protocols'] = {MODEL: 'chat'}
    update['reasoning'] = {'effort': ''}
    headers = {'X-CSRF-Token': session['csrf_token']}
    with request(opener, base, '/api/config', update, headers, 'PUT') as response:
        assert response.status == 200, response.read().decode()
    # Persist a real catalog cache so this deployment check never relies on a public model endpoint.
    docker('stop', '-t', '20', NAME)
    cache = {'schema_version': 3, 'updated_at': '2026-10-15T00:00:00Z', 'zen': [MODEL], 'go': [],
             'native_protocols': {'zen': {MODEL: 'chat'}}, 'unsupported': {}}
    docker('run', '--rm', '-i', '-v', VOLUME + ':/state', '--entrypoint', 'sh', IMAGE,
           '-c', 'umask 077; cat > /state/config.json.models.catalog.json', data=json.dumps(cache))
    docker('rm', NAME)
    base = start('8197', 'new-env-must-not-reset-password')
    ready(opener, base)
    session = login(opener, base)
    assert read_config()['server_keys'] == [api_key]
    assert read_config()['upstream']['zen'] == fixture
    docker('exec', NAME, '/usr/local/bin/opencode2api', '-healthcheck')
    api_headers = {'Authorization': 'Bearer ' + api_key}
    with request(opener, base, '/v1/chat/completions', {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Hello'}]}, api_headers) as response:
        assert response.status == 200, response.read().decode()
        assert json.load(response)['choices'][0]['message']['content'] == MARKER
    started = time.monotonic()
    with request(opener, base, '/v1/chat/completions', {'model': MODEL, 'messages': [{'role': 'user', 'content': 'Hello'}], 'stream': True}, api_headers) as response:
        assert response.status == 200, response.read().decode()
        first = response.readline()
        assert MARKER.encode() in first, first
        assert time.monotonic() - started < 1.8, 'Streaming was buffered'
        assert b'[DONE]' in response.read()
    print('Recreated container: password/key/config persistence, custom PORT, JSON inference and unbuffered SSE passed.')
    if os.getenv('BROWSER_TEST') == '1':
        from playwright.sync_api import sync_playwright
        Path('test-results').mkdir(exist_ok=True)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.goto(base)
            page.locator('#login-user').fill('admin')
            page.locator('#login-pass').fill(PASSWORD)
            page.locator('#login-form button').click()
            page.locator('#app').wait_for(state='visible', timeout=60000)
            page.locator('[data-page="access"]').click()
            page.locator('#access-base-value').wait_for(state='visible')
            assert page.locator('#access-base-value').input_value() == base
            page.screenshot(path='test-results/management.png', full_page=True)
            browser.close()
        print('Browser: management login and public API address under port mapping passed.')
    # An entirely unset password must also bootstrap without manual configuration.
    docker('rm', '-f', NAME)
    docker('volume', 'rm', '-f', VOLUME)
    base = start(password='', extra=('-e', 'ZEABUR='))
    ready(opener, base)
    credentials = docker('exec', NAME, 'cat', STATE + '/initial-credentials.txt')
    password = credentials.split('Initial management password: ')[1].splitlines()[0]
    login(opener, base, password)
    assert 'Initial management password:' in docker('logs', NAME)
    print('No environment or config file: random password initialization and login passed.')
    upstream.shutdown()


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print(docker('logs', NAME, check=False))
        raise
    finally:
        docker('rm', '-f', NAME, check=False)
        docker('volume', 'rm', '-f', VOLUME, check=False)
