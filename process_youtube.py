#!/usr/bin/env python3
"""Process YouTube session videos and generate evaluation report."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluate import evaluate_transcript
from app.report import render_report
from app.vtt_parser import combine_session_transcripts, vtt_to_transcript

ROOT = Path(__file__).resolve().parent
DEFAULT_TRANSCRIPT_DIR = ROOT / "sample" / "transcripts"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transcript-dir",
        type=Path,
        default=DEFAULT_TRANSCRIPT_DIR,
    )
    parser.add_argument("--staff", default="担当スタッフ")
    parser.add_argument("--date", default="2025-05-26")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "youtube-session.html",
    )
    parser.add_argument(
        "--combined-only",
        action="store_true",
        help="Write combined transcript text and exit",
    )
    args = parser.parse_args()

    combined = combine_session_transcripts(args.transcript_dir)
    combined_path = args.transcript_dir / "combined_session.txt"
    combined_path.write_text(combined, encoding="utf-8")
    print(f"Combined transcript: {combined_path} ({len(combined.splitlines())} lines)")

    if args.combined_only:
        return

    result = evaluate_transcript(
        combined,
        staff_name=args.staff,
        session_date=args.date,
        source="transcript",
        use_llm=False,
    )
    html = render_report(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"Report: {args.output}")
    print("--- Check results ---")
    for check in result.checks:
        mark = "OK" if check.passed else "NG"
        print(f"{check.id}: {mark}  {check.evidence[:100]}")

    # Per-segment reports
    segments = {
        "counseling": vtt_to_transcript(args.transcript_dir / "counseling.ja.vtt"),
        "posture": vtt_to_transcript(args.transcript_dir / "posture.ja.vtt"),
        "closing": vtt_to_transcript(args.transcript_dir / "closing.ja.vtt"),
    }
    for name, text in segments.items():
        seg_result = evaluate_transcript(
            text,
            staff_name=args.staff,
            session_date=args.date,
            source="transcript",
            use_llm=False,
        )
        seg_path = args.output.parent / f"youtube-{name}.html"
        seg_path.write_text(render_report(seg_result), encoding="utf-8")
        passed = sum(c.passed for c in seg_result.checks)
        print(f"Segment {name}: {passed}/5 -> {seg_path}")


if __name__ == "__main__":
    main()
