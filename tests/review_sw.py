"""PWA tests on a fixture-only localhost server and fresh browser context."""
import argparse
import functools
import json
import re
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
VERSION=1
SOURCE={}


class Handler(SimpleHTTPRequestHandler):
    def log_message(self,*args):
        pass

    def do_GET(self):
        name=self.path.split('?')[0].lstrip('/') or 'index.html'
        if name in ('index.html','sw.js'):
            raw=SOURCE.get(name) or (ROOT/name).read_bytes()
            if name=='index.html':
                # SW-owned requests bypass Playwright routing. Remove external font/
                # preconnect links from this empty-shelf fixture to keep it local.
                raw=re.sub(rb'<link\b[^>]*https?://[^>]*>',b'',raw)
                # Registration is explicit below so foreign cache creation always precedes activation.
                raw=raw.replace(b"navigator.serviceWorker.register('./sw.js')",b"Promise.resolve()")
                raw=raw.replace(b'</body>',('<!--fixture-version:%d--></body>'%VERSION).encode())
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8' if name.endswith('html') else 'application/javascript')
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            self.wfile.write(raw)
        else:
            super().do_GET()


def main():
    global VERSION, SOURCE
    parser=argparse.ArgumentParser()
    parser.add_argument('--revision')
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.revision:
        SOURCE={name:subprocess.check_output(['git','show',args.revision+':'+name],cwd=ROOT)
                for name in ('index.html','sw.js')}
    server=ThreadingHTTPServer(('127.0.0.1',18764),functools.partial(Handler,directory=str(ROOT)))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    results=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,executable_path=str(
            Path.home()/'AppData/Local/ms-playwright/chromium-1208/chrome-win64/chrome.exe'),args=['--disable-gpu'])
        context=browser.new_context()
        # Avoid routing SW navigations: Chromium offline emulation can otherwise
        # leave a continued route pending. The served fixture has no external assets.
        page=context.new_page()
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://localhost:18764/',wait_until='domcontentloaded')
        page.evaluate('''async()=>{
          await caches.open('other-app-fixture');await caches.open('bookshelf-v2');
          await navigator.serviceWorker.register('./sw.js');await navigator.serviceWorker.ready;
        }''')
        page.wait_for_function('navigator.serviceWorker.controller!==null')
        keys=page.evaluate('caches.keys()')
        results.append(dict(name='activation_preserves_other_app_cache',
                            result='pass' if 'other-app-fixture' in keys and 'bookshelf-v2' not in keys else 'fail',evidence=keys))
        VERSION=2
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function('async()=>{const r=await caches.match("./index.html");return r&&(await r.text()).includes("fixture-version:2")}')
        results.append(dict(name='network_update_reaches_page',result='pass' if 'fixture-version:2' in page.content() else 'fail'))
        context.set_offline(True)
        try:
            page.reload(wait_until='domcontentloaded')
            page.wait_for_function('document.getElementById("stat").textContent.length>0')
            results.append(dict(name='offline_reload_keeps_latest_shell',result='pass' if 'fixture-version:2' in page.content() else 'fail'))
        except Exception as e:
            results.append(dict(name='offline_reload_keeps_latest_shell',result='fail',error=str(e)))
        results.append(dict(name='page_errors',result='pass' if not errors else 'fail',evidence=errors))
        context.close();browser.close()
    server.shutdown();server.server_close()
    Path(args.output).write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=True))
    return 1 if any(r['result']=='fail' for r in results) else 0


if __name__=='__main__':
    raise SystemExit(main())
