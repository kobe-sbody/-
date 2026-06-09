"""添削履歴の永続化（Supabase）。

将来の成長分析・ランキング等はこのモジュールを拡張する。
"""
from __future__ import annotations

import os

from app.logger import logger
from app.models import FeedbackHistoryDetail, FeedbackHistoryItem

_client = None
_client_checked = False

ENV_SUPABASE_URL = "SUPABASE_URL"
ENV_SUPABASE_SERVICE_ROLE_KEY = "SUPABASE_SERVICE_ROLE_KEY"
ENV_SUPABASE_KEY_FALLBACK = "SUPABASE_KEY"


def _read_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _env_status(name: str) -> str:
    """環境変数の有無を OK / 空 / 未設定 で返す（値は出さない）。"""
    if name not in os.environ:
        return "未設定"
    if not _read_env(name):
        return "空"
    return "OK"


def get_env_diagnostics() -> dict[str, str]:
    """起動ログ用。キー値そのものは含めない。"""
    service_key_status = _env_status(ENV_SUPABASE_SERVICE_ROLE_KEY)
    if service_key_status == "未設定" and _env_status(ENV_SUPABASE_KEY_FALLBACK) == "OK":
        service_key_status = f"未設定（{ENV_SUPABASE_KEY_FALLBACK} は OK）"
    return {
        ENV_SUPABASE_URL: _env_status(ENV_SUPABASE_URL),
        ENV_SUPABASE_SERVICE_ROLE_KEY: service_key_status,
    }


def log_env_diagnostics() -> None:
    diag = get_env_diagnostics()
    logger.info("%s: %s", ENV_SUPABASE_URL, diag[ENV_SUPABASE_URL])
    logger.info("%s: %s", ENV_SUPABASE_SERVICE_ROLE_KEY, diag[ENV_SUPABASE_SERVICE_ROLE_KEY])
    related = sorted(k for k in os.environ if "SUPABASE" in k.upper())
    if related:
        logger.info("SUPABASE関連の環境変数名: %s", ", ".join(related))
    else:
        logger.info("SUPABASE関連の環境変数名: なし")


def is_configured() -> bool:
    return bool(_read_env(ENV_SUPABASE_URL) and _get_supabase_key())


def _get_supabase_key() -> str:
    return _read_env(ENV_SUPABASE_SERVICE_ROLE_KEY) or _read_env(ENV_SUPABASE_KEY_FALLBACK)


def _get_client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    url = _read_env(ENV_SUPABASE_URL)
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
