/* 本棚 Service Worker
   狙いは「機内モードでも棚と表紙が見えること」だけ。
   守っている約束が2つある。

   1. ページ本体（index.html）はネットワーク優先にする。
      cache-first にすると、直しても更新が永久に届かなくなる。
   2. 書誌API（国会図書館・openBD）の応答は絶対にキャッシュしない。
      固定化すると新刊が出てこなくなり、抜け巻検出そのものが嘘になる。 */

const V = 'bookshelf-v3';
const SHELL = ['./', './index.html', './manifest.json', './icon-192.png', './icon-512.png'];

const NEVER = [/ndlsearch\.ndl\.go\.jp/, /api\.openbd\.jp/];        // 触らない
const COVERS= [/images-na\.ssl-images-amazon\.com/,                 // 表紙は貯めてよい
               /books\.google\.com/];
const KEEP  = [...COVERS,
               /fonts\.googleapis\.com/, /fonts\.gstatic\.com/];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(V)
      .then(c => c.addAll(SHELL))
      .catch(() => {})            // 1つ落ちても初期化は続ける
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

/* 表紙キャッシュは放っておくと増える一方なので、古いものから間引く。
   keys() は入れた順に返るため、先頭から消せばおおむね古い順になる */
const LIMIT = 600;
const trim = () => caches.open(V).then(async c => {
  const ks = await c.keys();
  const covers = ks.filter(r => COVERS.some(re => re.test(r.url)));
  for (let i = 0; i < covers.length - LIMIT; i++) await c.delete(covers[i]);
}).catch(() => {});

const put = (req, res) => {
  // 不透明な応答（CORSなしの画像）は ok が false になるが、表示には使えるので残す
  if (res && (res.ok || res.type === 'opaque')) {
    const copy = res.clone();
    const url = typeof req === 'string' ? req : req.url;
    caches.open(V)
      .then(c => c.put(req, copy))
      .then(() => { if (COVERS.some(re => re.test(url))) trim(); })
      .catch(() => {});
  }
  return res;
};

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                 // 書き込み系は素通し

  const url = new URL(req.url);
  if (NEVER.some(r => r.test(url.href))) return;    // 書誌APIはそのまま通す

  // ページ本体：ネットワーク優先。取れなければ最後に取れた版を出す
  const isPage = req.mode === 'navigate' ||
                 (url.origin === location.origin &&
                  (url.pathname.endsWith('/') || url.pathname.endsWith('index.html')));
  if (isPage) {
    /* ネットワーク優先にするだけでは足りない。素の fetch はブラウザのHTTPキャッシュを
       使うので、サーバ上のファイルを直しても古い版が返ってくることがある。
       cache:'reload' で必ず取りに行かせる。ここを緩めると更新が届かなくなる。 */
    e.respondWith(
      fetch(req.url, { cache: 'reload', credentials: 'same-origin' })
        .then(res => put('./index.html', res))
        .catch(() => caches.match('./index.html').then(hit => hit || caches.match('./')))
    );
    return;
  }

  // 表紙・フォント・自分のファイル：あるものを使い、無ければ取りに行って貯める
  if (url.origin === location.origin || KEEP.some(r => r.test(url.href))) {
    e.respondWith(
      caches.match(req).then(hit => hit || fetch(req).then(res => put(req, res)))
    );
  }
});
