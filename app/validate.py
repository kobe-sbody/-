from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = {".m4a", ".aac", ".mp3", ".mp4", ".wav", ".webm", ".mpeg", ".mpga", ".ogg"}
MAX_FILE_MB = 100


class AudioValidationError(Exception):
    """音声ファイルのバリデーションエラー。"""


def validate_audio_file(filename: str | None, size_bytes: int) -> str:
    if not filename:
        raise AudioValidationError("ファイルが選択されていません。")

    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        supported = " / ".join(sorted(SUPPORTED_EXTENSIONS))
        raise AudioValidationError(
            f"非対応のファイル形式です（{suffix or '拡張子なし'}）。"
            f"対応形式: {supported}"
        )

    size_mb = size_bytes / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        raise AudioValidationError(
            f"ファイルサイズが大きすぎます（{size_mb:.1f}MB）。{MAX_FILE_MB}MB以下にしてください。"
        )

    if size_bytes == 0:
        raise AudioValidationError("ファイルが空です。別の録音ファイルを選んでください。")

    return suffix
