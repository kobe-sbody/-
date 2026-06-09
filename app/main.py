from __future__ import annotations

import os
import socket
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.requests import Request as StarletteRequest
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.feedback_history import (
    format_created_date,
    get_feedback_history,
    is_configured as supabase_configured,
    list_feedback_history,
    log_env_diagnostics,
)
from app.jobs import JobStep, jobs, run_pipeline
from app.logger import logger
from app.paths import REPORT_DIR, ROOT, UPLOAD_DIR, ensure_data_dirs
from app.validate import AudioValidationError, validate_audio_file

load_dotenv()

ensure_data_dirs()

app = FastAPI(title="カウンセリング育成サポート")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))


@app.middleware("http")
async def log_upload_requests(request: StarletteRequest, call_next):
    if request.method == "POST" and request.url.path == "/api/evaluate":
        content_length = request.headers.get("content-length", "不明")
        logger.info(
            "=== アップロード開始（HTTP受信） === content-length=%s bytes",
            content_length,
        )
    response = await call_next(request)
    if request.method == "POST" and request.url.path == "/api/evaluate":
        logger.info("=== アップロードHTTP応答 === status=%s", response.status_code)
    return response


@app.on_event("startup")
async def startup() -> None:
    has_key = bool(os.getenv("OPENAI_API_KEY"))
    port = os.getenv("PORT", "8765")
    env = "Render" if os.getenv("RENDER") else "local"
    logger.info(
        "=== サーバー起動 [%s] === upload=%s reports=%s API_KEY=%s PORT=%s",
        env,
        UPLOAD_DIR,
        REPORT_DIR,
        "OK" if has_key else "未設定",
        port,
    )
    if env == "local":
        local_ip = _get_local_ip()
        if local_ip:
            logger.info("=== iPhone/同一Wi-Fi === http://%s:%s", local_ip, port)
        logger.info("=== Mac === http://127.0.0.1:%s", port)
    if env == "Render":
        logger.info("=== 注意 === 音声・レポートは /tmp 保存（再起動で消えます）")
    logger.info("=== Supabase 環境変数 ===")
    log_env_diagnostics()
    logger.info(
        "=== Supabase === %s",
        "OK（添削履歴あり）" if supabase_configured() else "未設定（履歴保存なし）",
    )


def _get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return ""


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    has_api_key = bool(os.getenv("OPENAI_API_KEY"))
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "today": date.today().isoformat(),
            "has_api_key": has_api_key,
            "active_nav": "upload",
            "history_enabled": supabase_configured(),
        },
    )


@app.get("/history", response_class=HTMLResponse)
async def history_list(request: Request):
    items = list_feedback_history()
    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "items": items,
            "active_nav": "history",
            "history_enabled": supabase_configured(),
            "format_date": format_created_date,
        },
    )


@app.get("/history/{record_id}", response_class=HTMLResponse)
async def history_detail(request: Request, record_id: str):
    record = get_feedback_history(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="履歴が見つかりません")
    return templates.TemplateResponse(
        "history_detail.html",
        {
            "request": request,
            "record": record,
            "active_nav": "history",
            "history_enabled": supabase_configured(),
            "format_date": format_created_date,
        },
    )


@app.post("/api/evaluate")
async def api_evaluate(
    background_tasks: BackgroundTasks,
    staff_name: str = Form(""),
    session_date: str = Form(default_factory=lambda: date.today().isoformat()),
    audio: UploadFile = File(...),
):
    """非同期ジョブを開始。フロントは /api/jobs/{id} をポーリングする。"""
    filename = audio.filename or "recording.m4a"
    logger.info(
        "=== ファイル受信開始 === file=%s content_type=%s staff=%s",
        filename,
        audio.content_type or "不明",
        staff_name,
    )

    chunks: list[bytes] = []
    total = 0
    last_logged_mb = 0
    while True:
        chunk = await audio.read(512 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        current_mb = total // (1024 * 1024)
        if current_mb > last_logged_mb:
            last_logged_mb = current_mb
            logger.info("[upload] 受信中 file=%s %.1fMB", filename, total / (1024 * 1024))

    content = b"".join(chunks)
    size_kb = total / 1024
    logger.info(
        "=== アップロード完了 === file=%s size=%.1fKB (%.2fMB) staff=%s",
        filename,
        size_kb,
        total / (1024 * 1024),
        staff_name,
    )

    try:
        validate_audio_file(filename, total)
    except AudioValidationError as exc:
        logger.warning("バリデーション失敗: %s", exc)
        return JSONResponse(status_code=400, content={"error": str(exc), "step": "failed"})

    if not os.getenv("OPENAI_API_KEY"):
        msg = "OPENAI_API_KEY が設定されていません。Render の Environment Variables または .env を確認してください。"
        logger.error(msg)
        return JSONResponse(status_code=400, content={"error": msg, "step": "failed"})

    job = jobs.create(staff_name or "（未入力）", session_date, filename)
    suffix = Path(filename).suffix.lower() or ".m4a"
    saved_path = UPLOAD_DIR / f"{job.id}{suffix}"
    saved_path.write_bytes(content)
    logger.info("[job:%s] ファイル保存完了 path=%s", job.id, saved_path)

    transcribe_msg = "アップロード完了。文字起こしを開始します…"
    jobs.update(job.id, step=JobStep.TRANSCRIBING, message=transcribe_msg)
    logger.info("[job:%s] 文字起こし開始", job.id)

    background_tasks.add_task(
        run_pipeline,
        job.id,
        saved_path,
        job.staff_name,
        session_date,
        REPORT_DIR,
        filename,
    )

    return {
        "job_id": job.id,
        "step": JobStep.TRANSCRIBING.value,
        "message": transcribe_msg,
    }


@app.get("/api/jobs/{job_id}")
async def api_job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません")
    return job.to_dict()


@app.get("/reports/{report_id}", response_class=HTMLResponse)
async def show_report(report_id: str):
    path = REPORT_DIR / f"{report_id}.html"
    if not path.exists():
        return HTMLResponse("レポートが見つかりません", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@app.get("/reports/{report_id}/line.txt", response_class=PlainTextResponse)
async def download_line_text(report_id: str):
    path = REPORT_DIR / f"{report_id}.line.txt"
    if not path.exists():
        return PlainTextResponse("見つかりません", status_code=404)
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/plain; charset=utf-8")
