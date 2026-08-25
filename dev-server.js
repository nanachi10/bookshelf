/* ローカル確認用の静的サーバー（開発時のみ・アプリ本体とは無関係）。
   カメラは https か localhost でしか使えないので、file:// ではなくこれで開く。
     node dev-server.js  →  http://localhost:8123
   .claude/launch.json からも同じものを起動する。 */
const http = require('http'), fs = require('fs'), path = require('path');

const ROOT = __dirname;
const PORT = +process.env.PORT || 8123;
const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml',
  '.md': 'text/plain; charset=utf-8'
};

http.createServer((req, res) => {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/index.html';
  const f = path.join(ROOT, p);
  // リポジトリの外は出さない
  if (!f.startsWith(ROOT)) { res.writeHead(403); res.end(); return; }
  fs.readFile(f, (e, d) => {
    if (e) { res.writeHead(404); res.end('not found'); return; }
    res.writeHead(200, {
      'Content-Type': MIME[path.extname(f).toLowerCase()] || 'application/octet-stream',
      // 直したものがすぐ反映されるように（Service Worker の検証で紛れないよう）
      'Cache-Control': 'no-store'
    });
    res.end(d);
  });
}).listen(PORT, () => console.log('bookshelf: http://localhost:' + PORT));
