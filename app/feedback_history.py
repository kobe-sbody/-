"""添削履歴の永続化（Supabase）。

将来の成長分析・ランキング等はこのモジュールを拡張する。
"""
from __future__ import annotations

import base64
import json
import os
from collections import Counter
from urllib.parse import quote

from app.logger import logger
from app.models import (
    FeedbackHistoryDetail,
    FeedbackHistoryItem,
    FeedbackHistoryStats,
    StaffHistoryCount,
)

_client = None
_client_checked = False

ENV_SUPABASE_URL = "SUPABASE_URL"
ENV_SUPABASE_SERVICE_ROLE_KEY = "SUPABASE_SERVICE_ROLE_KEY"
ENV_SUPABASE_KEY_FALLBACK = "SUPABASE_KEY"


def _read_env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _normalize_supabase_url(url: str) -> str:
    """supabase-py 用に URL を正規化する。

    末尾スラッシュや /rest/v1 付きだと PGRST125 になる（GitHub supabase-py#959）。
    """
    normalized = url.strip().rstrip("/")
    if normalized.endswith("/rest/v1"):
        normalized = normalized[: -len("/rest/v1")].rstrip("/")
    return normalized


def _supabase_url() -> str:
    raw = _read_env(ENV_SUPABASE_URL)
    return _normalize_supabase_url(raw) if raw else ""


def _supabase_url_host() -> str:
    url = _supabase_url()
    if not url:
        return ""
    return url.replace("https://", "").replace("http://", "").split("/")[0]


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


def _jwt_role(key: str) -> str:
    """JWTの role クレームを返す（キー本体はログに出さない）。"""
    try:
        payload = key.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded))
        return str(data.get("role", "unknown"))
    except Exception:
        return "unknown"


def log_env_diagnostics() -> None:
    diag = get_env_diagnostics()
    logger.info("%s: %s", ENV_SUPABASE_URL, diag[ENV_SUPABASE_URL])
    logger.info("%s: %s", ENV_SUPABASE_SERVICE_ROLE_KEY, diag[ENV_SUPABASE_SERVICE_ROLE_KEY])
    raw_url = _read_env(ENV_SUPABASE_URL)
    normalized_url = _supabase_url()
    if raw_url and normalized_url != raw_url.rstrip("/"):
        logger.warning(
            "SUPABASE_URLの形式を自動補正しました（末尾の/ や /rest/v1 は不要です）"
        )
    host = _supabase_url_host()
    if host:
        logger.info("SUPABASE_URL_HOST: %s", host)
        if ".supabase.co" not in host:
            logger.warning(
                "SUPABASE_URL_HOST が想定外です。Project URL は https://xxxx.supabase.co 形式にしてください"
            )
    key = _get_supabase_key()
    if key:
        role = _jwt_role(key)
        logger.info("SUPABASE_KEY_ROLE: %s", role)
        if role == "anon":
            logger.warning(
                "SUPABASE_SERVICE_ROLE_KEY に anon キーが設定されています。"
                "service_role キーに差し替えてください。"
            )
    related = sorted(k for k in os.environ if "SUPABASE" in k.upper())
    if related:
        logger.info("SUPABASE関連の環境変数名: %s", ", ".join(related))
    else:
        logger.info("SUPABASE関連の環境変数名: なし")


def is_configured() -> bool:
    return bool(_supabase_url() and _get_supabase_key())


def _get_supabase_key() -> str:
    return _read_env(ENV_SUPABASE_SERVICE_ROLE_KEY) or _read_env(ENV_SUPABASE_KEY_FALLBACK)


def _get_client():
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True
    url = _supabase_url()
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
    file_name = audio_file_name or "recording.m4a"
    logger.info("添削履歴保存開始 staff=%s file=%s", staff_name, file_name)

    if not is_configured():
        logger.error("添削履歴保存失敗 error=Supabase環境変数未設定")
        return None

    client = _get_client()
    if not client:
        logger.error("添削履歴保存失敗 error=Supabaseクライアント未初期化")
        return None

    payload = {
        "staff_name": staff_name or "（未入力）",
        "audio_file_name": file_name,
        "transcript": transcript,
        "feedback": feedback,
    }
    try:
        response = (
            client.table("feedback_history")
            .insert(payload)
            .select("id")
            .execute()
        )
        rows = response.data or []
        if not rows or not rows[0].get("id"):
            logger.error(
                "添削履歴保存失敗 error=insert応答にidなし（RLSまたはAPIキー種別を確認）"
            )
            return None
        record_id = str(rows[0]["id"])
        logger.info("添削履歴保存成功 id=%s", record_id)
        return record_id
    except Exception as exc:
        logger.error("添削履歴保存失敗 error=%s", exc)
        return None


def history_filter_href(staff_name: str | None = None) -> str:
    if not staff_name:
        return "/history"
    return f"/history?staff={quote(staff_name)}"


def get_feedback_history_stats() -> FeedbackHistoryStats:
    """全体件数とスタッフ別件数を返す。"""
    client = _get_client()
    if not client:
        return FeedbackHistoryStats(total=0, staff_counts=[])
    try:
        response = (
            client.table("feedback_history")
            .select("staff_name")
            .execute()
        )
        rows = response.data or []
        counter = Counter(row.get("staff_name") or "（未入力）" for row in rows)
        staff_counts = [
            StaffHistoryCount(staff_name=name, count=count)
            for name, count in sorted(counter.items(), key=lambda x: (-x[1], x[0]))
        ]
        return FeedbackHistoryStats(total=len(rows), staff_counts=staff_counts)
    except Exception as exc:
        logger.error("添削履歴集計取得失敗 error=%s", exc)
        return FeedbackHistoryStats(total=0, staff_counts=[])


def list_distinct_staff_names() -> list[str]:
    stats = get_feedback_history_stats()
    return [item.staff_name for item in stats.staff_counts]


def list_feedback_history(
    *,
    staff_name: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[FeedbackHistoryItem]:
    client = _get_client()
    if not client:
        return []
    try:
        end = max(offset, offset + limit - 1)
        query = (
            client.table("feedback_history")
            .select("id, created_at, staff_name")
            .order("created_at", desc=True)
        )
        if staff_name:
            query = query.eq("staff_name", staff_name)
        response = query.range(offset, end).execute()
        return [FeedbackHistoryItem.model_validate(row) for row in (response.data or [])]
    except Exception as exc:
        logger.error("添削履歴一覧取得失敗 error=%s", exc)
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
