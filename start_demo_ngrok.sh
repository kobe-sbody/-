#!/usr/bin/env bash
# ngrok で外部公開（同一Wi-Fiで繋がらない場合の代替）
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8765}"

if ! command -v ngrok >/dev/null 2>&1; then
  echo "ngrok がインストールされていません。"
  echo "  brew install ngrok/ngrok/ngrok"
  echo "  ngrok config add-authtoken <YOUR_TOKEN>"
  exit 1
fi

echo "サーバーを起動します（バックグラウンド）..."
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port "$PORT" &
UVICORN_PID=$!

cleanup() {
  kill "$UVICORN_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

sleep 2
echo ""
echo "ngrok トンネルを開始します..."
echo "表示される Forwarding URL を iPhone の Safari で開いてください"
echo ""
ngrok http "$PORT"
