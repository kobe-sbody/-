from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.models import CheckItem, EvaluationResult

ROOT = Path(__file__).resolve().parent.parent
MANUAL_PATH = ROOT / "config" / "manual.json"

CHECK_DEFINITIONS = [
    ("1-a", "カウンセリング", "マニュアル通りに話せているか"),
    ("1-b", "カウンセリング", "悩みの深掘りができているか"),
    ("2-a", "姿勢改善の説明", "姿勢の状態をマニュアル順に説明できているか"),
    ("3-a", "トレーニング中", "良いところを見つけて褒められているか"),
    ("3-b", "トレーニング中", "「今後」を使って次のフォーム改善点を伝えられているか"),
]


def load_manual() -> dict:
    return json.loads(MANUAL_PATH.read_text(encoding="utf-8"))


def _staff_lines(transcript: str) -> list[str]:
    lines = []
    for line in transcript.splitlines():
        if "スタッフ:" in line or "スタッフ：" in line:
            text = re.sub(r"^\[[^\]]+\]\s*", "", line)
            text = re.split(r"スタッフ[:：]", text, maxsplit=1)[-1].strip()
            if text:
                lines.append(text)
    if lines:
        return lines
    return [line.strip() for line in transcript.splitlines() if line.strip()]


def _customer_lines(transcript: str) -> list[str]:
    lines = []
    for line in transcript.splitlines():
        if "お客様:" in line or "お客様：" in line or "顧客:" in line or "顧客：" in line:
            text = re.sub(r"^\[[^\]]+\]\s*", "", line)
            text = re.split(r"(?:お客様|顧客)[:：]", text, maxsplit=1)[-1].strip()
            if text:
                lines.append(text)
    return lines


def _extract_posture_section(staff_text: str, manual: dict) -> str:
    """姿勢診断パート以降のスタッフ発話を抽出。"""
    intro_keywords = manual["posture_intro"]["keywords"]
    start = 0
    for kw in intro_keywords:
        idx = staff_text.find(kw)
        if idx != -1:
            start = idx
            break
    return staff_text[start:] if start else staff_text


def _detect_concern_type(combined: str, manual: dict) -> str | None:
    scripts = manual.get("concern_scripts", {})
    for name, cfg in scripts.items():
        if any(kw in combined for kw in cfg["keywords"]):
            return name
    return None


def _evaluate_posture_order(staff_text: str, manual: dict, combined: str) -> tuple[bool, str]:
    posture_text = _extract_posture_section(staff_text, manual)
    steps = manual["posture_explanation_steps"]
    intro = manual["posture_intro"]

    intro_found = any(kw in posture_text for kw in intro["keywords"])
    detected_steps: list[tuple[int, str]] = []

    for idx, step in enumerate(steps):
        best_pos: int | None = None
        for kw in step["keywords"]:
            match = re.search(re.escape(kw), posture_text)
            if match and (best_pos is None or match.start() < best_pos):
                best_pos = match.start()
        if best_pos is not None:
            detected_steps.append((best_pos, step["label"]))

    detected_steps.sort(key=lambda x: x[0])
    labels_in_order = [label for _, label in detected_steps]
    step_ids = [s["label"] for s in steps]
    order_ok = labels_in_order == sorted(
        labels_in_order, key=lambda label: step_ids.index(label)
    )

    concern = _detect_concern_type(combined, manual)
    min_steps = 5 if intro_found else 4
    passed = len(detected_steps) >= min_steps and order_ok

    missing = [s["label"] for s in steps if s["label"] not in labels_in_order]
    parts = [
        f"検出ステップ: {len(detected_steps)}/{len(steps)}",
        f"確認できた順序: {' → '.join(labels_in_order) or 'なし'}",
    ]
    if intro_found:
        parts.append("導入パート: 確認")
    else:
        parts.append("導入パート: 未確認")
    if concern:
        parts.append(f"お悩みタイプ推定: {concern}")
    if missing:
        parts.append(f"不足ステップ: {', '.join(missing)}")

    return passed, "。".join(parts) + "。"


def _rule_based_evaluate(transcript: str, manual: dict) -> list[CheckItem]:
    staff_text = "\n".join(_staff_lines(transcript))
    customer_text = "\n".join(_customer_lines(transcript))
    combined = transcript

    # 1-a: counseling flow topics
    flow = manual["counseling_flow"]
    covered = sum(
        1 for step in flow if any(kw in combined for kw in step["keywords"])
    )
    flow_ratio = covered / len(flow) if flow else 0
    missing_topics = [
        step["topic"]
        for step in flow
        if not any(kw in combined for kw in step["keywords"])
    ]
    passed_1a = flow_ratio >= 0.75
    evidence_1a = (
        f"マニュアル項目 {covered}/{len(flow)} を確認。"
        + (f" 不足: {', '.join(missing_topics)}" if missing_topics else "")
    )

    # 1-b: deep dive after concern
    concerns = manual["deep_dive_patterns"]["concern_indicators"]
    follow_ups = manual["deep_dive_patterns"]["follow_up_indicators"]
    concern_mentioned = any(c in customer_text for c in concerns)
    deep_dive_found = any(f in staff_text for f in follow_ups)
    passed_1b = (not concern_mentioned) or deep_dive_found
    evidence_1b = (
        "お客様の悩みに対し、具体化・背景を問う深掘り質問を確認。"
        if passed_1b and concern_mentioned
        else "お客様が悩みを述べた後、深掘り質問（「具体的に」「いつ頃から」等）が見当たりません。"
        if concern_mentioned
        else "お客様の具体的な悩みの発言が少なく、深掘り判定は限定的です。"
    )

    # 2-a: posture explanation order (⓪〜⑥)
    passed_2a, evidence_2a = _evaluate_posture_order(staff_text, manual, combined)

    # 3-a: praise
    praise_words = manual["praise_patterns"]
    praise_hits = [w for w in praise_words if w in staff_text]
    passed_3a = len(praise_hits) >= 1
    evidence_3a = (
        f"褒めの言葉を確認: {', '.join(praise_hits)}"
        if praise_hits
        else "トレーニング中の褒めの言葉が見当たりません。"
    )

    # 3-b: 今後 + improvement (training section only)
    training_markers = ["マット", "エクササイズ", "トレーニング", "フォーム", "足を", "上げて"]
    training_start = len(staff_text)
    for marker in training_markers:
        idx = staff_text.find(marker)
        if idx != -1:
            training_start = min(training_start, idx)
    training_text = staff_text[training_start:] if training_start < len(staff_text) else ""

    future_pattern = re.compile(
        r"今後.{0,30}(意識|フォーム|改善|伸ば|緩め|使って|してください|試して)"
    )
    future_lines = [
        line
        for line in _staff_lines(transcript)
        if "今後" in line
        and future_pattern.search(line)
        and not any(x in line for x in ["解決されていく", "お悩みが"])
    ]
    passed_3b = len(future_lines) >= 1
    evidence_3b = (
        f"「今後」を使った改善フィードバック: {future_lines[0]}"
        if future_lines
        else "トレーニング中に「今後は〜を意識してください」形式の改善フィードバックが見当たりません。"
    )

    raw = {
        "1-a": (passed_1a, evidence_1a),
        "1-b": (passed_1b, evidence_1b),
        "2-a": (passed_2a, evidence_2a),
        "3-a": (passed_3a, evidence_3a),
        "3-b": (passed_3b, evidence_3b),
    }

    suggestions = {
        "1-a": "カウンセリング冒頭で、挨拶→流れ説明→健康確認→悩みヒアリング→目標確認→プラン説明の順を意識してください。",
        "1-b": "お客様が悩みを述べたら「具体的にどの場面で？」「いつ頃から？」など深掘り質問を1つ以上入れてください。",
        "2-a": "姿勢診断は「導入→⓪部位→①写真で気づき→②骨格→③体型変化→④筋肉バランス→⑤使う/緩める部位→⑥今後の変化」の順で説明してください。",
        "3-a": "フォームが良い瞬間に「素晴らしいです」「上手ですね」など具体的な褒め言葉を添えてください。",
        "3-b": "改善点は「今後は〇〇を意識してみてください」の型で伝えてください。",
    }

    checks = []
    for check_id, category, label in CHECK_DEFINITIONS:
        passed, evidence = raw[check_id]
        checks.append(
            CheckItem(
                id=check_id,
                category=category,
                label=label,
                passed=passed,
                evidence=evidence,
                suggestion="" if passed else suggestions[check_id],
            )
        )
    return checks


def _build_summary(checks: list[CheckItem]) -> str:
    failed = [c for c in checks if not c.passed]
    if not failed:
        return "全チェック項目を達成しています。マニュアル通りの丁寧なセッションです。"
    parts = []
    for c in failed:
        parts.append(f"・{c.id} {c.label}: {c.evidence}")
    return "\n".join(parts)


def _build_action_plan(checks: list[CheckItem]) -> list[str]:
    failed = [c for c in checks if not c.passed]
    if not failed:
        return ["現状の進め方を維持し、次回もお客様の悩み深掘りと具体的な褒め言葉を継続してください。"]
    plan = []
    for i, c in enumerate(failed, 1):
        plan.append(f"{i}. {c.suggestion}")
    plan.append(f"{len(plan) + 1}. 次回セッション前に、未達項目をロールプレイで3回練習する")
    return plan


def _llm_enhance(
    transcript: str, checks: list[CheckItem], staff_name: str
) -> tuple[str, list[str]] | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    failed = [c for c in checks if not c.passed]
    check_json = json.dumps(
        [{"id": c.id, "label": c.label, "passed": c.passed, "evidence": c.evidence} for c in checks],
        ensure_ascii=False,
        indent=2,
    )

    prompt = f"""あなたはピラティススタジオの教育担当です。
以下のセッション文字起こしとチェック結果をもとに、
1) できていない点の要約（3〜5文、箇条書き）
2) 次へのアクションプラン（3〜5項目、実行可能な文）

をJSON形式で出力してください。

スタッフ名: {staff_name}

【チェック結果】
{check_json}

【文字起こし】
{transcript[:8000]}

出力形式:
{{"summary": "...", "action_plan": ["...", "..."]}}
"""

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": "日本語で簡潔に。JSONのみ返してください。"},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    return data.get("summary", ""), data.get("action_plan", [])


def evaluate_transcript(
    transcript: str,
    *,
    staff_name: str = "（未入力）",
    session_date: str = "（未入力）",
    source: str = "demo",
    use_llm: bool = True,
) -> EvaluationResult:
    manual = load_manual()
    checks = _rule_based_evaluate(transcript, manual)
    summary = _build_summary(checks)
    action_plan = _build_action_plan(checks)

    if use_llm:
        enhanced = _llm_enhance(transcript, checks, staff_name)
        if enhanced:
            summary, action_plan = enhanced

    return EvaluationResult(
        staff_name=staff_name,
        session_date=session_date,
        source=source,  # type: ignore[arg-type]
        transcript=transcript,
        checks=checks,
        summary=summary,
        action_plan=action_plan,
    )
