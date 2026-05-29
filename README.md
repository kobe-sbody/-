# カウンセリング育成サポート（デモ版）

ピラティススタジオ向け。カウンセリング録音から、マネージャーがスタッフへ共有できる育成フィードバックを自動生成します。

## デモの起動

```bash
cd pilates-session-review
./start_demo.sh
```

- **Mac:** http://127.0.0.1:8765
- **iPhone（同一Wi-Fi）:** 起動時に表示される `http://192.168.x.x:8765` を Safari で開く

手動起動する場合:

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8765 --reload
```

### iPhoneからアクセスできない場合

1. MacとiPhoneが**同じWi-Fi**か確認
2. Macのファイアウォールで Python / uvicorn の受信を許可
3. 代替: ngrok で外部URLを発行

```bash
./start_demo_ngrok.sh
```

表示された `https://xxxx.ngrok-free.app` を iPhone で開く

### iPhoneでのアップロード

- **ボイスメモ（m4a）** から選択可能
- aac / mp3 も対応

## Render にデプロイ（Mac なしで iPhone からアクセス）

詳細は [DEPLOY.md](DEPLOY.md) を参照。

**Start Command:**

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

**Environment Variables:** `OPENAI_API_KEY`（必須）

公開後は `https://your-app.onrender.com` を iPhone の Safari で開く。

## デモの流れ（店長向け）

1. スタッフ名・日付を入力
2. カウンセリング録音（m4a/mp3/wav/mp4/aac）をアップロード
3. 1〜5分待つ（処理中画面が表示）
4. 育成レポートが表示される
5. 「LINE用テキストをコピー」→ スタッフへ送信

## 店長向けデモのポイント

- **改善ポイントが上部に表示** → 1on1の準備が短縮
- **スタッフ向けメッセージ** → LINEにそのまま貼り付け可
- **育成トーン** → 「要サポート」も前向きな表現
- **スキルスコア** → ヒアリング力/共感力/クロージング力/進行力

## コマンドライン（開発用）

```bash
python run_counseling.py input/スタッフ名/録音.m4a \
  --staff 田中 --date 2025-05-27 \
  --output reports/田中/2025-05-27-report.html
```

## テスト推奨

- **上手いスタッフ** → スコア・良い点の確認
- **普通〜弱いスタッフ** → 改善ポイント・教育導線の確認

## 設定

`.env` に `OPENAI_API_KEY=sk-...` を設定
