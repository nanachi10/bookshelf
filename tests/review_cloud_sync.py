from pathlib import Path
import json,base64,threading,functools,hashlib
from urllib.parse import urlsplit,parse_qs
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]
server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(SimpleHTTPRequestHandler,directory=ROOT))
threading.Thread(target=server.serve_forever,daemon=True).start()
url='http://127.0.0.1:%d/'%server.server_port
def book(work,vol):
 return dict(isbn='M'+work+str(vol),title=work+' '+str(vol),series=work,vol=vol,author='作家',added=vol)
original=[book('乙',2),book('甲',10),book('甲',2),book('甲',1)]
db=dict(data=dict(app='bookshelf',version=3,books=original,done=[]),sha='1')
def api(route):
 req=route.request
 if '/contents/' not in req.url: body={'private':True}
 elif req.method=='PUT':
  inp=req.post_data_json
  if inp['sha']!=db['sha']:return route.fulfill(status=409)
  db.update(data=json.loads(base64.b64decode(inp['content'])),sha=str(int(db['sha'])+1))
  body={'content':{'sha':db['sha']}}
 else: body=dict(sha=db['sha'],content=base64.b64encode(json.dumps(db['data']).encode()).decode())
 route.fulfill(json=body)
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path=str(Path.home()/'AppData/Local/ms-playwright/chromium-1208/chrome-win64/chrome.exe'))
 pages=[]
 for opts in ({},pw.devices['Pixel 7']):
  ctx=b.new_context(service_workers='block',**opts)
  login='https://shelf-test.invalid'
  ctx.route('**/login-service.json',lambda route:route.fulfill(json={'origin':login}))
  def handle(route):
   req=route.request
   parsed=urlsplit(req.url)
   if parsed.path=='/auth/start':
    global challenge
    challenge=parse_qs(parsed.query)['challenge'][0]
    route.fulfill(status=302,headers={'Location':url+'#shelf-login='+'t'*43})
   elif parsed.path=='/auth/exchange':
    proof=req.post_data_json['verifier'].encode()
    assert base64.urlsafe_b64encode(hashlib.sha256(proof).digest()).decode().rstrip('=')==challenge
    route.fulfill(json={'session':'s'*43})
   else:
    assert req.headers['authorization']=='Bearer '+'s'*43
    api(route)
  ctx.route(login+'/**',handle)
  p=ctx.new_page();p.goto(url,wait_until='domcontentloaded')
  p.locator('#cloudstatus').click();p.locator('#cloudgo').click()
  expect(p.locator('#cloudstatus')).to_have_text('同期済み')
  pages.append(p)
 pc,mobile=pages
 for p in pages:
  ids=p.locator('.coverwall .book').evaluate_all('(els)=>els.map(e=>e.dataset.isbn)')
  assert [v for v in ids if '甲' in v]==['M甲1','M甲2','M甲10'],ids
  p.locator('[data-layout="spine"]').click();assert p.locator('.book.spine').count()==4
  p.locator('.book.spine').first.click();assert p.locator('#sheet').is_visible();p.evaluate('closeSheet()')
  p.locator('[data-layout="series"]').click();assert p.locator('#main .series').count()==2
 def change(p,js):p.evaluate(js)
 def sync(p):p.evaluate('runShelfSync()')
 change(pc,"changeBooks(bs=>[...bs,{...bs[0],isbn:'Mnew',title:'新しい本'}])");sync(pc);sync(mobile)
 assert mobile.evaluate("books.some(b=>b.isbn==='Mnew')")
 change(pc,"changeBooks(bs=>bs.map((b,i)=>i?b:{...b,note:'PCメモ'}))")
 change(mobile,"changeBooks(bs=>bs.map((b,i)=>i?b:{...b,rate:4}))")
 sync(pc);sync(mobile);sync(pc)
 assert pc.evaluate("books[0].rate===4 && books[0].note==='PCメモ'")
 change(mobile,"changeBooks(bs=>bs.filter(b=>b.isbn!=='Mnew'))");sync(mobile);sync(pc)
 assert not pc.evaluate("books.some(b=>b.isbn==='Mnew')")
 change(mobile,"changeBooks(bs=>bs.map((b,i)=>i?b:{...b,note:'mobile'}))")
 change(pc,"changeBooks(bs=>bs.map((b,i)=>i?b:{...b,note:'desktop'}))")
 sync(pc);sync(mobile);expect(mobile.locator('#cloudstatus')).to_have_text('同期の違いを確認')
 mobile.locator('#cloudstatus').click();mobile.locator('[data-conflict]').select_option('local');mobile.locator('#cloudgo').click()
 expect(mobile.locator('#cloudstatus')).to_have_text('同期済み');sync(pc);assert pc.evaluate("books[0].note==='mobile'")
 print('Two-device sync, merge, deletion, conflict resolution, 3 layouts and ordering passed.')
