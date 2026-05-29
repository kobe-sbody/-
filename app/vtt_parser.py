from __future__ import annotations

import re
from pathlib import Path


VTT_TS = re.compile(
    r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})"
)
VTT_TAG = re.compile(r"<[^>]+>")


def _parse_timestamp(ts: str) -> str:
    h, m, rest = ts.split(":")
    s = rest.split(".")[0]
    total_m = int(h) * 60 + int(m)
    return f"{total_m:02d}:{s}"


def parse_vtt(path: Path) -> list[tuple[str, str]]:
    """Return list of (timestamp, text) from WebVTT."""
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        match = VTT_TS.match(line)
        if match:
            start = _parse_timestamp(match.group(1))
            i += 1
            text_parts: list[str] = []
            while i < len(lines) and lines[i].strip() and not VTT_TS.match(lines[i]):
                chunk = VTT_TAG.sub("", lines[i].strip())
                if chunk and chunk not in text_parts:
                    text_parts.append(chunk)
                i += 1
            text = "".join(text_parts).strip()
            if text and not text.isspace():
                if not entries or entries[-1][1] != text:
                    entries.append((start, text))
            continue
        i += 1
    return entries


CUSTOMER_HINTS = (
    "お願いします",
    "はい",
    "そうですね",
    "なんです",
    "思います",
    "ですね",
    "大丈夫です",
    "わかりました",
)

STAFF_HINTS = (
    "させていただ",
    "ご説明",
    "見ていただ",
    "いかがでしょう",
    "トレーニング",
    "お写真",
    "当スタジオ",
    "プラン",
    "担当",
    "まず",
    "では",
    "続いて",
)


def _guess_speaker(text: str, prev: str = "スタッフ") -> str:
    stripped = text.strip()
    if len(stripped) <= 18 and any(h in stripped for h in CUSTOMER_HINTS):
        return "お客様"
    if any(h in stripped for h in STAFF_HINTS):
        return "スタッフ"
    if len(stripped) <= 12:
        return "お客様"
    return "スタッフ" if prev == "お客様" else prev


def anonymize(text: str) -> str:
    text = re.sub(r"[一-龥ぁ-んァ-ン]{1,4}さん", "〇〇さん", text)
    text = re.sub(r"池原", "〇〇", text)
    return text


def vtt_to_transcript(path: Path, *, offset_minutes: int = 0) -> str:
    entries = parse_vtt(path)
    lines: list[str] = []
    prev = "スタッフ"
    for ts, text in entries:
        clean = anonymize(text)
        speaker = _guess_speaker(clean, prev)
        prev = speaker
        mm, ss = ts.split(":")
        total_m = int(mm) + offset_minutes
        lines.append(f"[{total_m:02d}:{ss}] {speaker}: {clean}")
    return "\n".join(lines)


def combine_session_transcripts(base_dir: Path) -> str:
    parts = [
        ("# カウンセリング・トレーニング", vtt_to_transcript(base_dir / "counseling.ja.vtt")),
        ("# 姿勢診断", vtt_to_transcript(base_dir / "posture.ja.vtt", offset_minutes=30)),
        ("# クロージング", vtt_to_transcript(base_dir / "closing.ja.vtt", offset_minutes=50)),
    ]
    return "\n\n".join(header + "\n" + body for header, body in parts)
