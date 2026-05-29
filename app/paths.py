from __future__ import annotations

import os
from pathlib import Path

# アプリ本体（コード・static・templates）
ROOT = Path(__file__).resolve().parent.parent


def get_data_root() -> Path:
    """アップロード・レポートの保存先。Render では /tmp（再起動で消える）。"""
    if data_dir := os.getenv("DATA_DIR"):
        return Path(data_dir)
    if os.getenv("RENDER"):
        return Path("/tmp/pilates-session-review")
    return ROOT


DATA_ROOT = get_data_root()
UPLOAD_DIR = DATA_ROOT / "uploads"
REPORT_DIR = DATA_ROOT / "reports"


def ensure_data_dirs() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
