"""添削履歴の永続化（Supabase）。

将来の成長分析・ランキング等はこのモジュールを拡張する。
"""
from __future__ import annotations

import os

from app.logger import logger
from app.models import FeedbackHistoryDetail, FeedbackHistoryItem

_client = None
_client_checked = False


def is_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and _get_supabase_key())


def _get_supabase_key() -> str:
    return os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_KEY") or ""


def _get_client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    url = os.getenv("SUPABASE_URL")
    key = _get_supabase_key()
    if not url or not key:
        logger.info("Supabase未設定 — 添削履歴の保存・閲覧は無効")
        return None
    try:
        from supabase import create_client

        _client = create_client(url, key)
        logger.info("Supabase接続OK — feedback_history テーブルを使用")
    except Exception as exc:
        logger.error("Supabase初期化失敗: %s", exc)
        _client = None
    return _client


def save_feedback_history(
    *,
    staff_name: str,
    audio_file_name: str,
    transcript: str,
    feedback: str,
) -> str | None:
    """添削結果を保存する。失敗してもパイプラインは止めない。"""
    client = _get_client()
    if not client:
        return None
    payload = {
        "staff_name": staff_name or "（未入力）",
        "audio_file_name": audio_file_name or "recording.m4a",
        "transcript": transcript,
        "feedback": feedback,
    }
    try:
        response = client.table("feedback_history").insert(payload).execute()
        rows = response.data or []
        record_id = rows[0]["id"] if rows else None
        if record_id:
            logger.info("添削履歴を保存 id=%s staff=%s", record_id, staff_name)
        return record_id
    except Exception as exc:
        logger.error("添削履歴の保存に失敗: %s", exc)
        return None


def list_feedback_history(*, limit: int = 100, offset: int = 0) -> list[FeedbackHistoryItem]:
    client = _get_client()
    if not client:
        return []
    try:
        end = max(offset, offset + limit - 1)
        response = (
            client.table("feedback_history")
            .select("id, created_at, staff_name")
            .order("created_at", desc=True)
            .range(offset, end)
            .execute()
        )
        return [FeedbackHistoryItem.model_validate(row) for row in (response.data or [])]
    except Exception as exc:
        logger.error("添削履歴の取得に失敗: %s", exc)
        return []


def get_feedback_history(record_id: str) -> FeedbackHistoryDetail | None:
    client = _get_client()
    if not client:
        return None
    try:
        response = (
            client.table("feedback_history")
            .select("id, created_at, staff_name, audio_file_name, transcript, feedback")
            .eq("id", record_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return FeedbackHistoryDetail.model_validate(rows[0])
    except Exception as exc:
        logger.error("添削履歴の詳細取得に失敗 id=%s: %s", record_id, exc)
        return None


def format_created_date(created_at: str) -> str:
    """ISO日時を一覧表示用 YYYY-MM-DD に整形。"""
    return str(created_at)[:10]
