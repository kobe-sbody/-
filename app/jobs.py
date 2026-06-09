from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any

from app.counseling import evaluate_counseling
from app.feedback_history import save_feedback_history
from app.logger import logger
from app.report import render_report
from app.transcribe import prepare_audio, transcribe_audio


class JobStep(str, Enum):
    UPLOADING = "uploading"
    TRANSCRIBING = "transcribing"
    EVALUATING = "evaluating"
    REPORTING = "reporting"
    COMPLETED = "completed"
    FAILED = "failed"


STEP_LABELS = {
    JobStep.UPLOADING: "アップロード中",
    JobStep.TRANSCRIBING: "文字起こし中",
    JobStep.EVALUATING: "評価中",
    JobStep.REPORTING: "レポート生成中",
    JobStep.COMPLETED: "完了",
    JobStep.FAILED: "失敗",
}

STEP_HINTS = {
    JobStep.UPLOADING: "ファイルをサーバーに送信中です…",
    JobStep.TRANSCRIBING: "文字起こし中です。音声の長さによっては数分かかる場合があります。",
    JobStep.EVALUATING: "カウンセリング内容を評価しています…",
    JobStep.REPORTING: "レポートとLINE用テキストを作成しています…",
    JobStep.COMPLETED: "完了しました。レポート画面へ移動します。",
    JobStep.FAILED: "処理に失敗しました。",
}


@dataclass
class Job:
    id: str
    staff_name: str
    session_date: str
    filename: str
    step: JobStep = JobStep.UPLOADING
    message: str = ""
    error: str = ""
    report_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "step": self.step.value,
            "step_label": STEP_LABELS.get(self.step, self.step.value),
            "message": self.message or STEP_HINTS.get(self.step, ""),
            "error": self.error,
            "report_id": self.report_id,
            "report_url": f"/reports/{self.report_id}" if self.report_id else "",
            "staff_name": self.staff_name,
            "filename": self.filename,
            "updated_at": self.updated_at,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()

    def create(self, staff_name: str, session_date: str, filename: str) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:12],
            staff_name=staff_name,
            session_date=session_date,
            filename=filename,
            message=STEP_HINTS[JobStep.UPLOADING],
        )
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in kwargs.items():
                setattr(job, key, value)
            job.updated_at = datetime.now().isoformat(timespec="seconds")


jobs = JobStore()


def _set_step(job_id: str, step: JobStep, message: str = "") -> None:
    msg = message or STEP_HINTS.get(step, "")
    jobs.update(job_id, step=step, message=msg, error="")
    logger.info("[job:%s] %s — %s", job_id, STEP_LABELS[step], msg)


def run_pipeline(
    job_id: str,
    audio_path: Path,
    staff_name: str,
    session_date: str,
    report_dir: Path,
    audio_file_name: str = "",
) -> None:
    temp_path = audio_path
    ready_path = audio_path
    try:
        logger.info("[job:%s] パイプライン開始 file=%s staff=%s", job_id, audio_path.name, staff_name)

        _set_step(job_id, JobStep.TRANSCRIBING)
        logger.info("[job:%s] 音声前処理開始 path=%s", job_id, audio_path)
        ready_path = prepare_audio(audio_path)
        if ready_path != audio_path:
            logger.info("[job:%s] 形式変換完了 %s -> %s", job_id, audio_path.suffix, ready_path.suffix)

        logger.info("[job:%s] Whisper文字起こし開始（ここで数分かかることがあります）", job_id)
        transcript = transcribe_audio(ready_path)
        logger.info("[job:%s] 文字起こし完了 chars=%d lines=%d", job_id, len(transcript), transcript.count("\n") + 1)

        _set_step(job_id, JobStep.EVALUATING)
        logger.info("[job:%s] カウンセリング評価開始", job_id)
        result = evaluate_counseling(
            transcript,
            staff_name=staff_name,
            session_date=session_date,
            source="audio",
        )
        logger.info("[job:%s] 評価完了 score=%d", job_id, result.overall_score)

        _set_step(job_id, JobStep.REPORTING)
        logger.info("[job:%s] レポート生成開始", job_id)
        report_id = uuid.uuid4().hex[:12]
        html = render_report(result, report_id=report_id)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{report_id}.html").write_text(html, encoding="utf-8")
        (report_dir / f"{report_id}-transcript.txt").write_text(transcript, encoding="utf-8")
        (report_dir / f"{report_id}.line.txt").write_text(result.line_text, encoding="utf-8")
        logger.info("[job:%s] レポート保存完了 report_id=%s", job_id, report_id)

        try:
            save_feedback_history(
                staff_name=staff_name,
                audio_file_name=audio_file_name or audio_path.name,
                transcript=transcript,
                feedback=result.staff_feedback,
            )
        except Exception as exc:
            logger.error("添削履歴保存失敗 error=%s", exc)

        jobs.update(
            job_id,
            step=JobStep.COMPLETED,
            message=STEP_HINTS[JobStep.COMPLETED],
            report_id=report_id,
        )
        logger.info("[job:%s] ===== 完了 =====", job_id)

    except Exception as exc:
        err_msg = str(exc) or exc.__class__.__name__
        logger.error("[job:%s] ===== 失敗 ===== 原因: %s", job_id, err_msg)
        logger.error("[job:%s] %s", job_id, traceback.format_exc())
        jobs.update(
            job_id,
            step=JobStep.FAILED,
            error=err_msg,
            message=f"失敗しました。原因: {err_msg}",
        )
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        if ready_path != temp_path and ready_path.exists():
            ready_path.unlink(missing_ok=True)
