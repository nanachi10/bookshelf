"""One-time setup, after wrangler login and the first Worker deploy.
Usage: python register.py https://nanachi-bookshelf-login.ACCOUNT.workers.dev
The owner approves the GitHub App in their browser. Secrets go to Wrangler via stdin.
"""
import html
import json
import secrets
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
origin = sys.argv[1].rstrip('/')
url = urllib.parse.urlparse(origin)
if url.scheme != 'https' or not url.hostname or not url.hostname.endswith('.workers.dev') or origin != 'https://' + url.hostname:
    raise SystemExit('Pass the deployed HTTPS workers.dev origin')
state = secrets.token_urlsafe(32)

class Register(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # The callback has a single-use registration code.

    def page(self, body):
        data = ('<meta charset="utf-8"><meta name="referrer" content="no-referrer">' + body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlparse(self.path)
        if self.headers.get('Host') != '127.0.0.1:' + str(self.server.server_port):
            return self.send_error(403)
        if path.path == '/' and path.query == 'start=' + state:
            manifest = dict(name='nanachi10-bookshelf-' + state[:6], url='https://nanachi10.github.io/bookshelf/',
                            public=False, callback_urls=[origin + '/auth/callback'], hook_attributes=dict(active=False),
                            redirect_url=local + '/complete', default_permissions=dict(contents='write'))
            target = 'https://github.com/settings/apps/new?state=' + state
            field = html.escape(json.dumps(manifest), quote=True)
            self.page(f'<form action="{target}" method="post"><input type="hidden" name="manifest" value="{field}"><button>GitHubで本棚アプリを登録</button></form>')
        elif path.path == '/complete':
            query = urllib.parse.parse_qs(path.query)
            if query.get('state') != [state] or not query.get('code'):
                return self.send_error(403)
            code = urllib.parse.quote(query['code'][0], safe='')
            request = urllib.request.Request('https://api.github.com/app-manifests/' + code + '/conversions', method='POST',
                                             headers={'Accept':'application/vnd.github+json','User-Agent':'bookshelf-setup'})
            with urllib.request.urlopen(request, timeout=30) as response:
                app = json.load(response)
            if app['owner']['id'] != 128808398:
                return self.send_error(403)
            cli = [shutil.which('npx.cmd') or shutil.which('npx'), '--yes', 'wrangler@4']
            for name in ['CLIENT_SECRET','CLIENT_ID']:
                subprocess.run(cli + ['secret','put',name],input=app[name.lower()].encode(),check=True,cwd=ROOT)
            (ROOT.parent / 'login-service.json').write_text(json.dumps(dict(origin=origin))+'\n',encoding='utf8')
            install = html.escape(app['html_url'] + '/installations/new', quote=True)
            self.page(f'<a href="{install}">本棚アプリをインストール</a><p>Only select repositories で bookshelf-private-backup だけを選んでください。</p>')
            self.server.finished = True
        else:
            self.send_error(404)

with HTTPServer(('127.0.0.1', 0), Register) as server:
    local = 'http://127.0.0.1:' + str(server.server_port)
    server.finished = False
    webbrowser.open(local + '/?start=' + state)
    while not server.finished:
        server.handle_request()
