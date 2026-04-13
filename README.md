# Twitter Auto Poster — デプロイガイド

## 構成図

```
[ブラウザ] ──HTTP──▶ [Render: FastAPI + APScheduler]
                              │
                    ┌─────────┴─────────┐
                    │                   │
              [SQLite DB]       [Twitter API v2]
            (スケジュール保存)   (自動投稿実行)
```

## ファイル構成

```
twitter-scheduler/
├── render.yaml                  # Renderデプロイ設定
├── backend/
│   ├── main.py                  # FastAPI アプリ本体
│   ├── database.py              # SQLite DB操作
│   ├── twitter_client.py        # Twitter API (tweepy)
│   ├── requirements.txt         # Python依存パッケージ
│   └── .env.example             # 環境変数テンプレート
└── frontend/
    └── public/
        └── index.html           # フロントエンド (単一HTML)
```

---

## ステップ1: Twitter Developer Portal でAPIキー取得

1. https://developer.twitter.com にアクセス
2. プロジェクトを作成 → App を作成
3. **User authentication settings** を設定
   - App permissions: `Read and write`
   - Type of App: `Web App`
4. 以下の4つをコピーしておく
   - API Key
   - API Key Secret
   - Access Token
   - Access Token Secret

---

## ステップ2: GitHubにプッシュ

```bash
cd twitter-scheduler
git init
git add .
git commit -m "initial commit"
# GitHubで新しいリポジトリを作成してpush
git remote add origin https://github.com/YOUR_NAME/twitter-scheduler.git
git push -u origin main
```

---

## ステップ3: Renderにデプロイ

### 3-1. Renderアカウント作成
https://render.com でサインアップ（GitHubアカウントで連携可）

### 3-2. New Web Service
1. ダッシュボード → **New +** → **Web Service**
2. GitHubリポジトリを選択
3. 設定を入力:

| 項目 | 値 |
|---|---|
| Name | `twitter-scheduler` |
| Region | `Singapore` (日本に最も近い) |
| Branch | `main` |
| Root Directory | `backend` |
| Runtime | `Python 3` |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Plan | **Starter ($7/月)** ← 永続ディスク必須 |

### 3-3. 環境変数を設定
「Environment」タブで以下を追加:

| Key | Value |
|---|---|
| `DATABASE_URL` | `sqlite:////opt/render/project/src/scheduler.db` |
| `ANTHROPIC_API_KEY` | `sk-ant-xxxx` |

### 3-4. Disk（永続ストレージ）を追加
「Disks」タブ:
- Name: `uploads`
- Mount Path: `/opt/render/project/src/uploads`
- Size: `1 GB`

### 3-5. Deploy
「Create Web Service」でデプロイ開始。
数分後に `https://twitter-scheduler-xxxx.onrender.com` でアクセス可能になります。

---

## ステップ4: フロントエンドをバックエンドに含める

`frontend/public/index.html` の先頭付近の以下の行を確認:
```js
const API = ''; // 同一オリジンなのでそのままでOK
```

バックエンドの `main.py` 末尾でフロントエンドの静的ファイルを配信しています。
デプロイ時に `frontend/public/` フォルダごとバックエンドに含めてください。

```bash
# ディレクトリ構造を確認
backend/
  main.py
  ...
  frontend/       ← backendフォルダ内に配置
    public/
      index.html
```

---

## ステップ5: アカウントを登録して使い始める

1. デプロイされたURLにアクセス
2. 左メニュー「アカウント」→ Twitter APIキーを入力して追加
3. 「投稿作成」で主題・目的を入力してAI生成
4. 投稿日時を設定して「スケジュール登録」

---

## ローカルでの動作確認

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
# .envを編集してANTHROPIC_API_KEYを設定

uvicorn main:app --reload --port 8000
# → http://localhost:8000 でアクセス
```

---

## 注意事項

- **Twitter Free Tierの制限**: 月1,500ツイートまで（有料プランで増加）
- **Render Free Plan**: 15分アイドルでスリープするためスケジュール投稿が失敗する場合あり → **Starter Plan ($7/月) を推奨**
- **画像投稿**: Twitter API v1.1 (media upload) と v2 を併用しています
- **タイムゾーン**: APSchedulerは `Asia/Tokyo` で動作します。フロントエンドのdatetime-localもJSTで入力してください
