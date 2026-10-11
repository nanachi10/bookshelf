# CLAUDE.md

本棚 — 単一 index.html の蔵書管理PWA。詳細は [HANDOVER.md](HANDOVER.md) が正本。ここは入口だけ。

## 作業前に読む
- HANDOVER §2（壊してはいけない不変条件）・§9（既知の罠）は毎回。
- 見た目・DOMに触るなら §7「JSとDOMの契約」。高速化なら PLAN-SPEED.md。

## 守ること（HANDOVER §2 の要約）
- 単一 index.html に CSS/JS 全部入り。ファイル分割・ビルド・npm・ライブラリ導入は提案しない。
- データは端末内のみ。storage は `store` 抽象経由。`store.bad` 中に空で上書きしない。
- Service Worker は書誌APIをキャッシュしない。`sw.js` を変えたら `V` を上げる。
- 国会図書館へは直列・間隔250ms・429はバックオフ。並列にしない。
- 削除には「元に戻す」。設計判断（なぜ）はコード内コメントに残す。

## 確認
- ローカル: `node dev-server.js` → http://localhost:8123
- テスト（Python Playwright）: 先に `PORT=18763 node dev-server.js` を立ててから
  `cd tests && python review_browser.py --output <out.json>`（review_candidate / review_sync も同様、
  review_sw は自前でサーバーを立てる）。出力JSONの各 `result` が `pass` かを見る。
  Chromium は `BOOKSHELF_CHROMIUM` → 手元PCの固定版 → クラウド同梱版の順に探す。
- `.claude/settings.json` のフックが、index.html / sw.js の編集直後に文法と `V` の上げ忘れを確かめる。

## コミット
- 日本語1行要約。利用者は GitHub Pages 上のアプリを使うので push まで行う。
