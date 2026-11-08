# 本棚のログインサーバー

本番サイトは GitHub Pages のまま、本人確認と非公開リポジトリへのアクセスを Worker が担当する。SQLite Durable Object にセッションを置く。GitHub App は所有者のアカウントに登録し、bookshelf-private-backup だけにインストールする。

## 初回の公開

1. このディレクトリで `npx --yes wrangler@4 login`、続いて `npx --yes wrangler@4 deploy` を実行し、発行された HTTPS URL を確認する。
2. `python register.py https://nanachi-bookshelf-login.ACCOUNT.workers.dev` を実行する。開いたブラウザで GitHub App の登録と対象リポジトリへのインストールを承認する。登録スクリプトが Worker のシークレットを設定し、ルートの login-service.json に公開先を書き込む。
3. ログイン先へのリダイレクトと GitHub のコールバック設定を確認し、login-service.json を含めて Pages に反映する。本番の所有者アカウントでログインし、PC とスマホの同期を確認する。

## 検証

`node --test auth/worker.test.mjs` は別ユーザーの拒否、コールバックの照合、交換コードの一度限りの利用、読み書き先の制限とログアウトを検証する。`python tests/review_cloud_sync.py` は模擬認証先と独立したブラウザでログイン後の編集・同期を検証する。
