#!/usr/bin/env python3
"""
カウンセリング音声 → Whisper文字起こし → 評価 → レポート

使い方:
  python run_counseling.py path/to/recording.mp3 --staff 田中
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from app.counseling import evaluate_counseling
from app.report import render_report
from app.transcribe import prepare_audio, transcribe_audio

ROOT = Path(__file__).resolve().parent


def main() -> None:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="カウンセリング音声を評価してレポートを出力")
    parser.add_argument("audio", type=Path, help="録音ファイル (mp3 / m4a / wav / mp4)")
    parser.add_argument("--staff", default="（未入力）", help="スタッフ名")
    parser.add_argument("--date", default="（未入力）", help="セッション日")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "counseling-report.html",
        help="レポート出力先",
    )
    args = parser.parse_args()

    if not args.audio.exists():
        print(f"エラー: ファイルが見つかりません: {args.audio}", file=sys.stderr)
        sys.exit(1)

    print(f"1/3 文字起こし中... ({args.audio.name})")
    ready_path = prepare_audio(args.audio)
    transcript = transcribe_audio(ready_path)

    transcript_path = args.output.parent / f"{args.output.stem}-transcript.txt"
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(transcript, encoding="utf-8")
    print(f"    文字起こし保存: {transcript_path}")

    print("2/3 カウンセリング評価中...")
    result = evaluate_counseling(
        transcript,
        staff_name=args.staff,
        session_date=args.date,
        source="audio",
    )

    print("3/3 レポート生成中...")
    html = render_report(result)
    args.output.write_text(html, encoding="utf-8")
    line_path = args.output.with_suffix(".line.txt")
    line_path.write_text(result.line_text, encoding="utf-8")

    passed = sum(1 for c in result.checks if c.passed)
    total = len(result.checks)
    ok_items = sum(1 for i in result.item_evaluations if i.verdict == "できている")
    print(f"\n完了: 総合 {result.overall_score}点（{result.overall_label}）")
    print(f"  項目判定: できている {ok_items}/{len(result.item_evaluations)}")
    for s in result.scores:
        print(f"  {s.name}: {s.score}点")
    print(f"  課題: {result.overall_assessment.top_issue}")
    print(f"\nレポート: {args.output}")
    print(f"LINE用テキスト: {line_path}")


if __name__ == "__main__":
    main()
