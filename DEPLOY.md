# Render デプロイ手順

Mac が閉じていても iPhone からデモを触れるようにする手順です（無料プラン想定）。

## 前提

- GitHub アカウント
- [Render](https://render.com) アカウント（GitHub 連携）
- OpenAI API キー

## 1. GitHub にリポジトリを作成

```bash
cd pilates-session-review
git init
git add .
git commit -m "Initial commit: Studio Coach demo"
```

GitHub で新規リポジトリ（例: `pilates-session-review`）を作成し、push:

```bash
git remote add origin git@github.com:YOUR_USER/pilates-session-review.git
git branch -M main
git push -u origin main
```

**含めないもの（.gitignore 済み）:** `.env`, `.venv/`, `uploads/`, 生成レポート, 音声ファイル

## 2. Render で Web Service を作成

1. [Render Dashboard](https://dashboard.render.com) → **New +** → **Web Service**
2. GitHub リポジトリ `pilates-session-review` を選択
3. 以下を設定:

| 項目 | 値 |
|------|-----|
| **Name** | `pilates-session-review`（任意） |
| **Region** | Singapore など（日本に近いリージョン） |
| **Branch** | `main` |
| **Runtime** | Python 3 |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| **Plan** | Free |

4. **Environment Variables** を追加:

| Key | Value |
|-----|-------|
| `OPENAI_API_KEY` | `sk-...`（あなたのキー） |

任意: `OPENAI_MODEL` = `gpt-4o-mini`

5. **Create Web Service** → ビルド完了まで 2〜5 分

公開 URL 例: `https://pilates-session-review.onrender.com`

## 3. iPhone から確認

1. Safari で Render の URL を開く
2. スタッフ名・日付を入力
3. ボイスメモ（m4a）をアップロード
4. レポート表示 → LINE 用テキストをコピー

## 注意（無料プラン）

- **スリープ:** 15 分アクセスがないと停止。初回アクセスに 30 秒〜1 分かかることがあります
- **ストレージ:** 音声・レポートは `/tmp` に保存（**再起動・再デプロイで消えます**）
- **ジョブ状態:** メモリ内管理のため、再起動中は処理が中断されることがあります
- デモ用途では問題ありません。本番運用には永続ストレージ（S3 等）が必要です

## ローカルで Render 同等のパスを試す

```bash
export RENDER=true
export OPENAI_API_KEY=sk-...
uvicorn app.main:app --host 0.0.0.0 --port 8765
# → /tmp/pilates-session-review/ に保存される
```

## トラブルシュート

| 症状 | 対処 |
|------|------|
| Build failed | Render ログで `pip install` エラーを確認 |
| API キー未設定 | Environment Variables に `OPENAI_API_KEY` |
| アップロード 400 | 形式が m4a/aac/mp3 等か確認 |
| 処理が途中で止まる | 無料プランのタイムアウト・スリープ。再試行 |

## Blueprint（任意）

`render.yaml` があるため、Render の **Blueprint** から一括作成も可能です。
