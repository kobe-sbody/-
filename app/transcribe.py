from __future__ import annotations

import os
import shutil
from pathlib import Path


def prepare_audio(audio_path: Path) -> Path:
    """Whisper非対応形式（.aac等）を処理可能な形式に整える。"""
    suffix = audio_path.suffix.lower()
    if suffix in {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".mpeg", ".mpga", ".ogg"}:
        return audio_path
    if suffix == ".aac":
        converted = audio_path.with_suffix(".m4a")
        shutil.copy2(audio_path, converted)
        return converted
    return audio_path


def transcribe_audio(audio_path: Path) -> str:
    """Transcribe audio using OpenAI Whisper API."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY が設定されていません。\n"
            ".env ファイルにキーを設定してください。"
        )

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    with audio_path.open("rb") as audio_file:
        response = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            language="ja",
            response_format="verbose_json",
        )

    segments = getattr(response, "segments", None) or []
    if segments:
        lines = []
        for seg in segments:
            start = int(seg.start)
            mm, ss = divmod(start, 60)
            text = seg.text.strip()
            if text:
                lines.append(f"[{mm:02d}:{ss:02d}] {text}")
        return "\n".join(lines)

    return response.text.strip()
