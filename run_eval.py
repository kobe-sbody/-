#!/usr/bin/env python3
"""CLI entry point for batch evaluation without web UI."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.evaluate import evaluate_transcript
from app.report import render_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a Pilates session transcript")
    parser.add_argument("transcript", type=Path, help="Path to transcript text file")
    parser.add_argument("--staff", default="（未入力）")
    parser.add_argument("--date", default="（未入力）")
    parser.add_argument("--output", type=Path, help="Write HTML report to this path")
    args = parser.parse_args()

    text = args.transcript.read_text(encoding="utf-8")
    result = evaluate_transcript(
        text,
        staff_name=args.staff,
        session_date=args.date,
        source="transcript",
        use_llm=False,
    )
    html = render_report(result)
    if args.output:
        args.output.write_text(html, encoding="utf-8")
        print(f"Report written to {args.output}")
    else:
        print(html)


if __name__ == "__main__":
    main()
