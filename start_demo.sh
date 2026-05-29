#!/usr/bin/env bash
# デモサーバー起動（同一Wi-Fi内のiPhoneからもアクセス可）
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -d .venv ]]; then
  echo "エラー: .venv がありません。先に python -m venv .venv && pip install -r requirements.txt"
  exit 1
fi

source .venv/bin/activate

PORT="${PORT:-8765}"
HOST="${HOST:-0.0.0.0}"

LOCAL_IP=""
for iface in en0 en1; do
  ip=$(ipconfig getifaddr "$iface" 2>/dev/null || true)
  if [[ -n "$ip" ]]; then
    LOCAL_IP="$ip"
    break
  fi
done

echo ""
echo "=========================================="
echo " Studio Coach デモサーバー"
echo "=========================================="
echo " Mac:     http://127.0.0.1:${PORT}"
if [[ -n "$LOCAL_IP" ]]; then
  echo " iPhone:  http://${LOCAL_IP}:${PORT}"
  echo "          （Macと同一Wi-Fiに接続してください）"
else
  echo " iPhone:  （Wi-Fi IPを取得できませんでした）"
  echo "          ifconfig で IP を確認してください"
fi
echo " 停止: Ctrl+C"
echo "=========================================="
echo ""

exec uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
