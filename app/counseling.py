from __future__ import annotations

import json
import os
import re
from pathlib import Path

from app.models import (
    CheckItem,
    EvaluationResult,
    FeedbackSection,
    ItemEvaluation,
    OverallAssessment,
    QuoteItem,
    ScoreItem,
    Verdict,
)
from app.logger import logger
from app.pm_review import review_staff_feedback

ROOT = Path(__file__).resolve().parent.parent
CRITERIA_PATH = ROOT / "config" / "evaluation_criteria.json"
MANUAL_PATH = ROOT / "config" / "manual.json"
FEEDBACK_RULES_PATH = ROOT / "config" / "feedback_rules.json"

VERDICT_SCORE = {
    "できている": 100,
    "一部できている": 70,
    "できていない": 35,
    "確認できない": 25,
}

ITEM_SCORE_WEIGHTS = {
    "S-01": 4.0,
    "S-02": 3.0,
    "S-03": 3.0,
    "S-04": 3.0,
    "S-05": 3.0,
    "A-01": 2.0,
    "A-02": 2.0,
    "A-03": 2.0,
    "B-01": 1.0,
    "B-02": 1.0,
    "B-03": 1.0,
    "B-04": 1.0,
    "B-05": 1.0,
    "Q-01": 1.0,
    "Q-02": 1.0,
    "Q-03": 1.0,
}

STRICT_ITEM_IDS = {
    "S-01",
    "S-02",
    "S-03",
    "S-04",
    "S-05",
    "A-01",
    "A-03",
    "B-01",
    "B-02",
    "B-03",
    "B-05",
}
ABSENCE_OK_ITEM_IDS = {"Q-02", "Q-03", "3-3", "3-4"}
TONE_ITEM_ID = "3-5"
TONE_PROVISIONAL_NOTE = (
    "※暫定評価：文字起こしからの推測です。"
    "抑揚・明るさは音声解析で評価予定（現時点では参考程度）。"
)

NEXT_ACTION_EXAMPLES = """
next_action は「状況→引き出したい心理→質問の流れ→具体セリフ」の形式で書くこと。
NG: 悩みを深掘りしましょう
NG: 「姿勢が原因です」と断定する
OK: 主訴を確認してから「体重以外にも関係している可能性があるので、後ほど姿勢を詳しく見させていただきながら一緒に確認してみましょう。」と伝える
"""

FEEDBACK_TONE_RULES = """
【スタッフ向けLINE — 文章スタイル（必ず守る）】

■形式
・冒頭・締めは固定テンプレ（後処理で付与。本文に含めない）
・名前は記載しない
・「今日のカウンセリング確認しました」等は使わない
・良かった点を先に、自然な文章で伝える
・改善点は「改善点は2つです。」のように自然な一文で導入（件数に合わせる）
・番号見出し（①改善点 など）は禁止
・改善点→重要性→お客様心理→質問の流れと狙い→会話例を1つの自然な段落にまとめる
・箇条書き・絵文字・👍🌱などの見出しは禁止

■トーン
・上司がスタッフへ送る社内LINEの添削
・丁寧だが堅すぎず、ラフすぎず
・コンサル風・先生っぽい表現はNG

■禁止
・〜になりますよ / 〜できますよ / 〜変わりますよ / 〜すると良いですよ
・一緒にやっていきましょう / いつでも声をかけてください
・次に伸ばすポイント / 本日のフィードバック

■推奨
・〜だと思います / 〜がおすすめです / 〜と感じました
・〜が気になりました / 〜の方が伝わりやすいと思います

■重要 — 改善点の中身（テクニックより心理と意図）
各改善点は次の5要素を自然な文章で含める（番号見出しは本文に書かない）:
1. 改善点（何を変えるか）
2. なぜ重要なのか（カウンセリングの意図）
3. お客様がどう感じているか（心理・不安・思い込み）
4. どのような流れで質問すべきか（各質問で何を引き出したいかまで）
5. 実際の会話例（セリフを「」で。流れ全体を示す）

NG: 「○○と言いましょう」だけで終わるテクニック指示
OK: なぜその質問か・何を引き出すか・会話の流れまで説明する

良い例の型:
「お客様に〇〇と気づいていただくことが重要です。そのために、まず…を確認し、次に…を聞き、最後に「…」と伝える流れがおすすめです。」
"""

IMPROVEMENT_PSYCHOLOGY_EXAMPLE = """
【改善点の書き方 — 必ず参考にする具体例】

悪い例:
「姿勢が原因かもしれませんね」と伝えましょう

良い例:
お客様に「今までの考え方だけでは改善しないかもしれない」と気づいていただくことが重要です。
お客様は自分なりの原因や解決策を持っており、否定されると不安や抵抗を感じやすいと思います。
そのために、主訴ごとにいつから気になるか、どんな場面で気になるか、過去の取り組みで変わった/変わらなかったかを確認した上で、
「体重以外にも関係している可能性があるので、後ほど姿勢を詳しく見させていただきながら一緒に確認してみましょう。」
と伝える流れがおすすめです。各質問で引き出したいのは、本人の思い込みと不安の根っこです。
"""


def load_feedback_rules() -> dict:
    return json.loads(FEEDBACK_RULES_PATH.read_text(encoding="utf-8"))


def load_criteria() -> dict:
    return json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))


def load_manual() -> dict:
    return json.loads(MANUAL_PATH.read_text(encoding="utf-8"))


def _parse_lines(transcript: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[(\d{2}:\d{2})\]\s*(.*)$", line)
        if m:
            rows.append((m.group(1), m.group(2)))
        else:
            rows.append(("", line))
    return rows


def _find_quotes(
    transcript: str,
    keywords: list[str],
    *,
    limit: int = 2,
    exclude: list[str] | None = None,
) -> list[QuoteItem]:
    quotes: list[QuoteItem] = []
    exclude = exclude or []
    for ts, text in _parse_lines(transcript):
        if exclude and any(ex in text for ex in exclude):
            continue
        if any(kw in text for kw in keywords):
            clean = text[:140] + ("…" if len(text) > 140 else "")
            quotes.append(QuoteItem(timestamp=ts, text=clean))
            if len(quotes) >= limit:
                break
    return quotes


def _is_low_quality_transcript(transcript: str) -> bool:
    lines = [text for _, text in _parse_lines(transcript) if len(text) > 8]
    if len(lines) < 5:
        return True
    normalized = [re.sub(r"\s+", "", line) for line in lines]
    if not normalized:
        return True
    from collections import Counter
    counts = Counter(normalized)
    most_common_count = counts.most_common(1)[0][1]
    if most_common_count / len(normalized) >= 0.35:
        return True
    unique_ratio = len(counts) / len(normalized)
    return unique_ratio < 0.25


def _transcript_quality(transcript: str) -> str:
    """good / medium / low — 文字起こし品質。"""
    if _is_low_quality_transcript(transcript):
        return "low"
    text = transcript
    if text.count("キラティ") >= 3 or text.count("メンバーと話") >= 2:
        return "medium"
    lines = [t for _, t in _parse_lines(transcript) if len(t) > 6]
    if lines:
        from collections import Counter
        uniq = len(Counter(re.sub(r"\s+", "", l) for l in lines))
        if uniq / len(lines) < 0.45:
            return "medium"
    return "good"


def _quote_exists_in_transcript(quote_text: str, transcript: str) -> bool:
    q = quote_text.replace("…", "").strip()
    if len(q) < 4:
        return False
    compact_t = re.sub(r"\s+", "", transcript)
    compact_q = re.sub(r"\s+", "", q)
    if compact_q in compact_t:
        return True
    return compact_q[: min(20, len(compact_q))] in compact_t


def _sanitize_quotes(quotes: list[QuoteItem], transcript: str) -> list[QuoteItem]:
    valid: list[QuoteItem] = []
    for q in quotes:
        if _quote_exists_in_transcript(q.text, transcript):
            valid.append(q)
    return valid[:2]


def _apply_tone_provisional(item: ItemEvaluation) -> None:
    if TONE_PROVISIONAL_NOTE not in item.comment:
        item.comment = f"{item.comment} {TONE_PROVISIONAL_NOTE}".strip()
    if item.verdict == "できている":
        item.verdict = "一部できている"
        item.comment = (
            f"文字起こし上は丁寧な表現が見られますが、声のトーンは未確認のため最高評価は付けません。"
            f" {TONE_PROVISIONAL_NOTE}"
        )


def _strict_adjust_item(item: ItemEvaluation, transcript: str) -> None:
    quotes_text = " ".join(q.text for q in item.quotes)

    if item.id == "S-01":
        profile = _non_weight_insight_profile(transcript)
        has_complete_flow = (
            profile["weight_relation"]
            and profile["past_effort"]
            and profile["result"]
            and profile["non_weight"]
            and profile["posture_link"]
        )
        if item.verdict == "できている" and not has_complete_flow:
            item.verdict = "一部できている" if item.quotes else "確認できない"
            item.comment = "体重以外の原因には触れていますが、体重変化・過去の取り組み・結果から自然に気づく流れが不足しています。"

    elif item.id == "S-02":
        if item.verdict == "できている" and not any(k in quotes_text for k in ["ジム", "ダイエット", "運動", "ピラティス", "ヨガ", "取り組", "これまで", "以前"]):
            item.verdict = "一部できている" if item.quotes else "確認できない"

    elif item.id == "S-03":
        has_result_question = _has_effort_result_check(transcript)
        has_result_answer = _has_effort_result_answer(transcript)
        if item.verdict == "できている" and not (has_result_question and has_result_answer):
            item.verdict = "一部できている" if item.quotes else "確認できない"
            item.comment = "取り組み結果には触れていますが、結果・変化・続かなかった理由の確認がまだ浅い可能性があります。"

    elif item.id == "S-04":
        if item.verdict == "できている" and not any(k in quotes_text for k in ["理想", "ゴール", "目標", "どうなって", "なりたい", "嬉しい", "引き締", "すっきり"]):
            item.verdict = "一部できている" if item.quotes else "確認できない"

    elif item.id == "S-05":
        if item.verdict == "できている" and not any(k in quotes_text for k in ["いつまで", "いつ頃", "期限", "何ヶ月", "イベント", "なるべく早く", "希望"]):
            item.verdict = "一部できている" if item.quotes else "確認できない"

    elif item.id == "A-01":
        weight_questions = _find_question_quotes(transcript, ["体重", "少なかった", "増え", "減", "妊娠", "変化", "変わる前"], limit=2)
        weight_question_text = " ".join(q.text for q in weight_questions)
        has_specific_concern = _has_any(weight_question_text, ["体型", "部位", "二の腕", "ウエスト", "下っ腹", "ヒップ", "ふくらはぎ", "前もも", "内もも"])
        if item.verdict == "できている" and not (weight_questions and has_specific_concern):
            item.verdict = "一部できている" if item.quotes else "確認できない"
            item.comment = "体重に関する話題はありますが、体重変化と悩みの関係確認はまだ限定的です。"

    elif item.id == "A-03":
        if item.verdict == "できている" and not any(k in quotes_text for k in ["選ん", "理由", "きっかけ", "なぜ", "通いやす", "紹介"]):
            item.verdict = "一部できている" if item.quotes else "確認できない"

    elif item.id == "B-01":
        has_time_question = bool(_find_question_quotes(transcript, ["時間帯", "何時", "曜日", "通うとしたら", "通いやす", "通え", "来れ"], limit=1))
        if item.verdict in ("できている", "一部できている") and not has_time_question:
            item.verdict = "確認できない"
            item.comment = "通う時間帯の希望を明確に確認する質問が見当たりません。"
            item.quotes = []

    elif item.id == "B-02":
        has_work_question = bool(_find_question_quotes(transcript, ["仕事", "お仕事", "業務", "立ち仕事", "座り仕事", "デスクワーク", "勤務"], limit=1))
        if item.verdict in ("できている", "一部できている") and not has_work_question:
            item.verdict = "確認できない"
            item.comment = "仕事や業務内容を明確に確認する質問が見当たりません。"
            item.quotes = []

    elif item.id == "B-03":
        has_sleep_question = bool(_find_question_quotes(transcript, ["睡眠", "眠", "寝", "浅い", "深い", "眠れて"], limit=1))
        if item.verdict in ("できている", "一部できている") and not has_sleep_question:
            item.verdict = "確認できない"
            item.comment = "睡眠状態を明確に確認する質問が見当たりません。"
            item.quotes = []

    elif item.id == "B-05":
        has_health = any(k in quotes_text for k in ["怪我", "けが", "痛", "既往", "病気", "手術", "通院", "医者", "お医者様", "止められて", "制限", "禁忌"])
        if item.verdict == "できている" and not has_health:
            item.verdict = "確認できない"

    elif item.id == "1-2":
        if item.verdict == "できている" and not _is_deep_dive_done(transcript):
            item.verdict = "一部できている" if item.quotes else "確認できない"
            item.comment = "悩みの部位や一部背景は確認できていますが、生活場面・困る瞬間・心理的背景の深掘りがもう一歩です。"

    elif item.id == "1-3":
        has_goal = any(k in quotes_text for k in ["目標", "理想", "なりたい", "いつまで", "いつ頃"])
        if item.verdict == "できている" and not has_goal:
            item.verdict = "一部できている" if item.quotes else "確認できない"

    elif item.id == "1-4":
        has_health = any(k in quotes_text for k in ["怪我", "痛", "既往", "病気", "手術", "通院", "健康", "医者", "お医者様", "止められて", "ドクターストップ", "制限", "禁忌"])
        if item.verdict == "できている" and not has_health:
            item.verdict = "確認できない"

    elif item.id == "2-1":
        has_topic = any(k in quotes_text for k in ["体重", "痩", "筋", "脂肪", "運動", "姿勢", "原因"])
        if item.verdict == "できている" and not has_topic:
            item.verdict = "一部できている" if item.quotes else "確認できない"

    elif item.id == "2-2":
        has_neg = any(k in transcript for k in ["違います", "間違", "それはダメ", "できません"])
        if has_neg and item.verdict in ("できている", "一部できている"):
            item.verdict = "できていない"

    elif item.id == "3-2":
        mono = _count_phrase(transcript, ["なるほどですね", "はい、はい", "はいはい", "はーい", "はぁい", "なるほど、なるほど"])
        if mono >= 3 and item.verdict == "できている":
            item.verdict = "一部できている"
            item.comment = "相槌が単調または軽く聞こえる可能性があります。"
            item.quotes = _find_quotes(transcript, ["なるほどですね", "はい、はい", "はいはい", "はーい", "はぁい"], limit=2) or item.quotes

    elif item.id == "3-3":
        praise = _find_quotes(transcript, ["いいですね", "いいじゃない", "素晴らしい", "すごいですね"], limit=2)
        if praise and item.verdict in ("できている", "一部できている", "確認できない"):
            item.verdict = "できていない"
            item.quotes = praise
            item.comment = "お客様の発言に対する余計な個人的感想が確認されました。"


def _enforce_evidence_strictness(
    items: list[ItemEvaluation],
    transcript: str,
    *,
    low_quality: bool = False,
) -> list[ItemEvaluation]:
    for item in items:
        if low_quality and item.id != TONE_ITEM_ID:
            item.verdict = "確認できない"
            item.quotes = []
            item.comment = (
                "文字起こしの品質が低く（繰り返し・内容不足）、"
                "カウンセリング内容を十分に確認できません。録音または文字起こしを再確認してください。"
            )
            item.next_action = "音声が正しく録音・文字起こしされているか確認し、再度評価してください。"
            continue

        item.quotes = _sanitize_quotes(item.quotes, transcript)

        if item.id == TONE_ITEM_ID:
            _apply_tone_provisional(item)
            continue

        if item.verdict == "できている" and not item.quotes and item.id not in ABSENCE_OK_ITEM_IDS:
            item.verdict = "確認できない"
            item.comment = (
                f"{item.comment} "
                "文字起こし上で根拠となる発言を確認できなかったため、「確認できない」としました。"
            ).strip()

        if item.verdict == "できていない" and not item.quotes:
            item.verdict = "確認できない"
            item.comment = (
                f"{item.comment} "
                "根拠となる発言引用がないため「確認できない」としました。"
            ).strip()

        if item.verdict == "一部できている" and not item.quotes and item.id in STRICT_ITEM_IDS:
            item.verdict = "確認できない"
            item.comment = "厳密確認が必要な項目ですが、根拠となる発言引用がありません。"

        if item.id in STRICT_ITEM_IDS:
            _strict_adjust_item(item, transcript)

        if item.verdict == "できている" and not item.quotes and item.id not in ABSENCE_OK_ITEM_IDS:
            item.verdict = "確認できない"

    return items


def _supplement_quotes_from_transcript(
    items: list[ItemEvaluation],
    rule_items: list[ItemEvaluation],
    transcript: str,
) -> list[ItemEvaluation]:
    """LLMが引用を返さなかった場合、ルールベース結果とキーワード検索で根拠を補完。"""
    rule_map = {i.id: i for i in rule_items}
    criteria = load_criteria()
    item_rules = {
        item["id"]: item.get("rule", {})
        for cat in criteria["categories"]
        for item in cat["items"]
    }

    for item in items:
        rule_item = rule_map.get(item.id)
        if rule_item and rule_item.verdict == "できている" and item.verdict != "できている":
            item.verdict = "できている"
            item.comment = rule_item.comment
            if not item.next_action:
                item.next_action = rule_item.next_action
            if rule_item.quotes:
                item.quotes = _sanitize_quotes(rule_item.quotes, transcript)
        elif rule_item and rule_item.verdict == "一部できている" and item.verdict in ("確認できない", "できていない"):
            item.verdict = "一部できている"
            item.comment = rule_item.comment
            if not item.next_action:
                item.next_action = rule_item.next_action
            if rule_item.quotes:
                item.quotes = _sanitize_quotes(rule_item.quotes, transcript)

        if item.quotes:
            continue
        if rule_item and rule_item.quotes:
            item.quotes = _sanitize_quotes(rule_item.quotes, transcript)
        if item.quotes:
            continue

        rule = item_rules.get(item.id, {})
        keywords: list[str] = []
        for key in (
            "positive",
            "concern",
            "deep_dive",
            "follow_up",
            "clarify",
            "monotone",
            "negative",
            "varied",
            "weight_relation",
            "past_effort",
            "result",
            "non_weight",
            "posture_link",
        ):
            keywords.extend(rule.get(key, []))
        if keywords:
            item.quotes = _find_quotes(transcript, keywords, limit=2)

    return items


def _upgrade_verdict_from_evidence(item: ItemEvaluation, transcript: str) -> None:
    """引用テキスト内の根拠がある場合のみ、過小評価を是正。"""
    if item.id == TONE_ITEM_ID or not item.quotes:
        return
    if item.id.startswith(("S-", "A-", "B-")):
        return

    quotes_text = " ".join(q.text for q in item.quotes)

    if item.id == "1-1":
        if re.search(r"[ぁ-んァ-ン一-龥]{1,8}さん", quotes_text):
            item.verdict = "できている"
            item.comment = "お客様のお名前で呼びかけが確認できました。"

    elif item.id == "1-2":
        if _is_deep_dive_done(transcript) and item.verdict in ("確認できない", "一部できている", "できていない"):
            item.verdict = "できている"
            item.comment = "悩みの部位だけでなく、背景・場面・理想まで複数観点で深掘りできています。"

    elif item.id == "1-3":
        has_deadline = "いつまで" in quotes_text or "いつ頃" in quotes_text
        has_ideal = any(k in quotes_text for k in ["理想", "なりたい", "目標"])
        if has_deadline and has_ideal:
            item.verdict = "できている"
        elif has_deadline or has_ideal:
            item.verdict = "一部できている"

    elif item.id == "1-4":
        if _has_comprehensive_health_check(transcript) or any(k in quotes_text for k in ["怪我", "既往", "病気", "痛", "健康", "お身体", "医者", "お医者様", "止められて", "ドクターストップ", "制限", "禁忌"]):
            item.verdict = "できている"

    elif item.id == "1-5":
        if any(k in quotes_text for k in ["ジム", "運動", "ダイエット", "歩く", "取り組", "経験"]):
            item.verdict = "できている"

    elif item.id == "3-3":
        praise = _find_quotes(
            transcript,
            ["いいですね", "いいじゃない", "素晴らしい", "すごいですね"],
            limit=2,
        )
        if praise:
            item.verdict = "できていない"
            item.quotes = praise
            item.comment = "お客様の発言に対する余計な個人的感想（「いいですね！」等）が確認されました。"
            if not item.next_action:
                item.next_action = (
                    "「ジムに通っています」と言われたら「以前からですか？」と事実確認に留め、"
                    "「いいですね！」等の感想は挟まない。"
                )


def _reconcile_verdicts(items: list[ItemEvaluation], transcript: str) -> list[ItemEvaluation]:
    """引用が補完されたあと、確認できない/過剰な否定を再調整。"""
    for item in items:
        if item.id == TONE_ITEM_ID:
            continue

        if not item.quotes and item.verdict == "できている" and item.id not in ABSENCE_OK_ITEM_IDS:
            item.verdict = "確認できない"

        if item.quotes and item.verdict == "確認できない":
            item.verdict = "一部できている"

        _upgrade_verdict_from_evidence(item, transcript)

        if item.id in STRICT_ITEM_IDS:
            _strict_adjust_item(item, transcript)

        if item.verdict == "できている" and not item.quotes and item.id not in ABSENCE_OK_ITEM_IDS:
            item.verdict = "確認できない"

    return items


def _count_phrase(text: str, phrases: list[str]) -> int:
    return sum(text.count(p) for p in phrases)


def _verdict_from_ratio(pos: int, neg: int = 0) -> Verdict:
    if neg >= 3 and pos == 0:
        return "できていない"
    if pos >= 2 and neg <= 1:
        return "できている"
    if pos >= 1 or (pos > 0 and neg <= 2):
        return "一部できている"
    return "できていない"


def _line_windows(transcript: str, size: int = 4) -> list[str]:
    lines = [text for _, text in _parse_lines(transcript)]
    if not lines:
        return []
    return [" ".join(lines[i : i + size]) for i in range(len(lines))]


def _has_any(text: str, keywords: list[str]) -> bool:
    return any(k in text for k in keywords)


QUESTION_MARKERS = [
    "ですか",
    "ますか",
    "でしょうか",
    "ありますか",
    "ございますか",
    "教えて",
    "伺",
    "聞かせ",
    "どの",
    "何",
    "いつ",
]


def _is_question_like(text: str) -> bool:
    return "？" in text or "?" in text or _has_any(text, QUESTION_MARKERS)


def _find_question_quotes(
    transcript: str,
    keywords: list[str],
    *,
    limit: int = 2,
) -> list[QuoteItem]:
    quotes: list[QuoteItem] = []
    for ts, text in _parse_lines(transcript):
        if _has_any(text, keywords) and _is_question_like(text):
            clean = text[:140] + ("…" if len(text) > 140 else "")
            quotes.append(QuoteItem(timestamp=ts, text=clean))
            if len(quotes) >= limit:
                break
    return quotes


def _deep_dive_profile(transcript: str) -> dict[str, bool]:
    """悩み深掘りの質を、単なるキーワード数ではなく確認観点で見る。"""
    body_part = _has_any(
        transcript,
        ["ウエスト", "下腹", "内もも", "外もも", "前もも", "太もも", "腰痛", "腰", "二の腕", "たるみ", "体型"],
    )
    since = _has_any(transcript, ["いつ頃", "いつから", "以前から", "最近", "10年以上", "ずっと", "慢性的"])
    situation = _has_any(
        transcript,
        ["どんな時", "どういう時", "場面", "生活", "日常", "仕事", "座", "立", "歩", "動く", "朝", "夜", "困る", "支障"],
    )
    dislike = _has_any(transcript, ["何が嫌", "嫌だ", "困", "不安", "気持ち", "ストレス"])
    ideal = _has_any(transcript, ["理想", "ゴール", "目標", "どうなって", "引き締", "すっきり", "変え"])
    has_pain = _has_any(transcript, ["腰痛", "痛"])
    pain_context = False
    if has_pain:
        for window in _line_windows(transcript, size=5):
            if _has_any(window, ["腰痛", "痛"]) and _has_any(
                window,
                ["どんな時", "どういう時", "場面", "生活", "日常", "仕事", "座", "立", "歩", "動く", "朝", "夜", "困る", "支障"],
            ):
                pain_context = True
                break
    return {
        "body_part": body_part,
        "since": since,
        "situation": situation,
        "dislike": dislike,
        "ideal": ideal,
        "has_pain": has_pain,
        "pain_context": pain_context,
    }


def _is_deep_dive_done(transcript: str) -> bool:
    profile = _deep_dive_profile(transcript)
    if not profile["body_part"]:
        return False
    depth_count = sum(
        1
        for key in ("since", "situation", "dislike", "ideal")
        if profile[key]
    )
    if profile["has_pain"] and not profile["pain_context"]:
        return False
    return depth_count >= 3


def _has_partial_deep_dive(transcript: str) -> bool:
    profile = _deep_dive_profile(transcript)
    if not profile["body_part"]:
        return False
    return any(profile[key] for key in ("since", "situation", "dislike", "ideal"))


def _has_comprehensive_health_check(transcript: str) -> bool:
    """怪我・病気・医師からの制限を1つの包括質問で確認できているか。"""
    injury = ["怪我", "けが", "痛み", "既往"]
    illness = ["病気", "ご病気", "手術", "通院"]
    restriction = ["医者", "お医者様", "止められて", "ドクターストップ", "制限", "禁忌"]
    for window in _line_windows(transcript, size=5):
        if _has_any(window, injury) and _has_any(window, illness) and _has_any(window, restriction):
            return True
    return False


def _non_weight_insight_profile(transcript: str) -> dict[str, bool]:
    weight_relation = bool(_find_question_quotes(transcript, ["体重", "少なかった", "増え", "減", "妊娠", "昔"], limit=1))
    past_effort = bool(_find_question_quotes(transcript, ["ジム", "ダイエット", "運動", "ピラティス", "ヨガ", "取り組", "運動習慣", "何かされ"], limit=1))
    result = _has_effort_result_check(transcript)
    non_weight = _has_any(transcript, ["体重以外", "姿勢", "身体の使い方", "体の使い方", "癖", "原因", "骨格"])
    posture_link = _has_any(transcript, ["姿勢診断", "後ほど", "詳しく見", "確認させて"])
    return {
        "weight_relation": weight_relation,
        "past_effort": past_effort,
        "result": result,
        "non_weight": non_weight,
        "posture_link": posture_link,
    }


def _has_effort_result_check(transcript: str) -> bool:
    effort_keywords = ["ジム", "ダイエット", "運動", "ピラティス", "ヨガ", "取り組", "歩く", "食事制限"]
    result_keywords = ["結果", "効果", "変化", "変わ", "続か", "どうでした", "どうだった", "やめて", "退会"]
    for window in _line_windows(transcript, size=4):
        if _has_any(window, effort_keywords) and _has_any(window, result_keywords) and _is_question_like(window):
            return True
    return False


def _has_effort_result_answer(transcript: str) -> bool:
    effort_keywords = ["ジム", "ダイエット", "運動", "ピラティス", "ヨガ", "取り組", "歩く", "食事制限"]
    result_keywords = ["効果", "変化", "変わ", "続か", "あんま", "あまり", "やめて", "退会", "目に見えて"]
    for window in _line_windows(transcript, size=5):
        if _has_any(window, effort_keywords) and _has_any(window, result_keywords):
            return True
    return False


def _find_rule_quotes(transcript: str, rule: dict, keys: list[str], limit: int = 2) -> list[QuoteItem]:
    keywords: list[str] = []
    for key in keys:
        keywords.extend(rule.get(key, []))
    return _find_quotes(transcript, keywords, limit=limit) if keywords else []


def _rule_evaluate_item(transcript: str, category: str, item: dict) -> ItemEvaluation:
    item_id = item["id"]
    label = item["label"]
    rule = item.get("rule", {})
    quotes: list[QuoteItem] = []
    comment = ""
    next_action = ""
    verdict: Verdict = "できていない"

    if item_id == "S-01":
        profile = _non_weight_insight_profile(transcript)
        score = sum(1 for value in profile.values() if value)
        quotes = _find_rule_quotes(
            transcript,
            rule,
            ["weight_relation", "past_effort", "result", "non_weight", "posture_link"],
            limit=2,
        )
        if all(profile.values()):
            verdict = "できている"
            comment = "体重変化・過去の取り組み・結果を確認したうえで、体重以外の原因と姿勢診断へ自然につなげられています。"
        elif profile["non_weight"] or profile["posture_link"] or score >= 2:
            verdict = "一部できている"
            comment = "体重以外の原因には触れていますが、過去の取り組みや結果から自然に気づく流れがもう一歩です。"
            next_action = "体重変化・過去の取り組み・結果を確認してから、姿勢や身体の使い方に原因がある可能性へつなげましょう。"
        else:
            verdict = "確認できない"
            comment = "体重以外の原因に気づいてもらう流れが確認できませんでした。"
            next_action = "「体重が少なかった時も気になっていましたか？」「運動でその部位は変わりましたか？」を確認しましょう。"

    elif item_id == "S-02":
        hits = _find_question_quotes(transcript, rule.get("positive", []), limit=2)
        if hits:
            verdict = "できている"
            comment = "過去または現在の取り組みを確認できています。"
            quotes = hits
        else:
            verdict = "確認できない"
            comment = "過去の取り組みが確認できませんでした。"
            next_action = "「これまで運動やダイエットなど、何か試されたことはありますか？」を確認しましょう。"

    elif item_id == "S-03":
        hits = _find_question_quotes(transcript, ["結果", "効果", "変化", "続か", "どうでした", "どうだった"], limit=2)
        result_answer = _find_quotes(transcript, ["効果", "変化", "変わ", "続か", "あんま", "あまり", "やめて", "退会", "目に見えて"], limit=2)
        if _has_effort_result_check(transcript) and _has_effort_result_answer(transcript):
            verdict = "できている"
            comment = "過去の取り組み結果や続かなかった背景を確認できています。"
            quotes = (hits + result_answer)[:2]
        elif _has_effort_result_answer(transcript) or _has_any(transcript, ["ジム", "ダイエット", "運動", "取り組", "歩く", "食事制限"]):
            verdict = "一部できている"
            comment = "過去の取り組み結果に触れていますが、結果・変化・続かなかった理由の確認がまだ浅い可能性があります。"
            quotes = (hits + result_answer)[:2]
            next_action = "「その時、気になる部位に変化はありましたか？」「続かなかった理由はありますか？」まで確認しましょう。"
        else:
            verdict = "確認できない"
            comment = "取り組み結果が確認できませんでした。"
            next_action = "過去の取り組みを聞いた後に、結果・変化・続かなかった理由まで確認しましょう。"

    elif item_id == "S-04":
        hits = _find_question_quotes(transcript, rule.get("positive", []), limit=2)
        answer_hits = _find_quotes(transcript, ["なりたい", "引き締", "すっきり", "体型", "嬉しい"], limit=2)
        if hits or answer_hits:
            verdict = "できている"
            comment = "理想体型や目指したい状態を確認できています。"
            quotes = (hits + answer_hits)[:2]
        else:
            verdict = "確認できない"
            comment = "理想体型の確認が不足しています。"
            next_action = "「どんな体型になったら嬉しいですか？」を確認しましょう。"

    elif item_id == "S-05":
        hits = _find_question_quotes(transcript, rule.get("positive", []), limit=2)
        answer_hits = _find_quotes(transcript, ["ヶ月", "週間", "年内", "旅行", "イベント", "までに", "なるべく早く"], limit=2)
        if hits or answer_hits:
            verdict = "できている"
            comment = "いつまでに変えたいか、期限や希望時期を確認できています。"
            quotes = (hits + answer_hits)[:2]
        else:
            verdict = "確認できない"
            comment = "期限や希望時期が確認できませんでした。"
            next_action = "「いつまでに変えておきたいなどの希望はありますか？」を確認しましょう。"

    elif item_id == "A-01":
        hits = _find_question_quotes(transcript, rule.get("positive", []), limit=2)
        hit_text = " ".join(q.text for q in hits)
        has_weight_change = _has_any(hit_text, ["少なかった", "増え", "減", "妊娠", "変化", "変わる前"])
        has_specific_concern = _has_any(hit_text, ["体型", "部位", "二の腕", "ウエスト", "下っ腹", "ヒップ", "ふくらはぎ", "前もも", "内もも"])
        if hits and has_weight_change and has_specific_concern:
            verdict = "できている"
            comment = "体重変化と悩みの関係を明確に確認できています。"
            quotes = hits
        elif hits or _has_any(transcript, ["体重", "増え", "減", "少なかった"]):
            verdict = "一部できている"
            comment = "体重に関する話題はありますが、体重変化と悩みの関係確認はまだ限定的です。"
            quotes = hits or _find_quotes(transcript, rule.get("positive", []), limit=1)
            next_action = "「体重が少なかった時も、その部位は気になっていましたか？」まで確認しましょう。"
        else:
            verdict = "確認できない"
            comment = "体重変化と悩みの関係が確認できませんでした。"
            next_action = "「体重が少なかった時も、その部位は気になっていましたか？」を確認しましょう。"

    elif item_id == "A-02":
        hits = _find_question_quotes(transcript, rule.get("positive", []), limit=2) or _find_quotes(transcript, ["Google", "グーグル", "SNS", "インスタ", "広告", "紹介", "検索"], limit=2)
        if hits:
            verdict = "できている"
            comment = "アイユーを知ったきっかけを確認できています。"
            quotes = hits
        else:
            verdict = "確認できない"
            comment = "認知経路が確認できませんでした。"
            next_action = "「アイユーをどのように知っていただいたんですか？」を確認しましょう。"

    elif item_id == "A-03":
        hits = _find_question_quotes(transcript, rule.get("positive", []), limit=2) or _find_quotes(transcript, ["選びました", "選んだ", "理由"], limit=2)
        if hits:
            verdict = "できている"
            comment = "アイユーを選んだ理由や来店動機を確認できています。"
            quotes = hits
        else:
            verdict = "確認できない"
            comment = "アイユーを選んだ理由が確認できませんでした。"
            next_action = "「他にもスタジオがある中で、アイユーを選んだ理由はありますか？」を確認しましょう。"

    elif item_id == "B-01":
        hits = _find_question_quotes(transcript, ["時間帯", "何時", "曜日", "通うとしたら", "通いやす", "通え", "来れ"], limit=2)
        if hits:
            verdict = "できている"
            comment = "通う時間帯の希望を確認できています。"
            quotes = hits
        else:
            verdict = "確認できない"
            comment = "通う時間帯の希望が確認できませんでした。"
            next_action = "「通うとしたら、どの時間帯が通いやすそうですか？」を確認しましょう。"

    elif item_id == "B-02":
        hits = _find_question_quotes(transcript, ["仕事", "お仕事", "業務", "立ち仕事", "座り仕事", "デスクワーク", "勤務"], limit=2)
        if hits:
            verdict = "できている"
            comment = "仕事や業務内容を確認できています。"
            quotes = hits
        else:
            verdict = "確認できない"
            comment = "仕事や業務内容が確認できませんでした。"
            next_action = "「お仕事では立っている時間と座っている時間、どちらが多いですか？」を確認しましょう。"

    elif item_id == "B-03":
        hits = _find_question_quotes(transcript, ["睡眠", "眠", "寝", "浅い", "深い", "眠れて"], limit=2)
        if hits:
            verdict = "できている"
            comment = "睡眠状態を確認できています。"
            quotes = hits
        else:
            verdict = "確認できない"
            comment = "睡眠状態が確認できませんでした。"
            next_action = "「眠りは浅い方ですか？深い方ですか？」を確認しましょう。"

    elif item_id == "B-04":
        hits = _find_quotes(transcript, rule.get("positive", []), limit=2)
        if hits:
            verdict = "できている"
            comment = "質問や不明点がないか確認できています。"
            quotes = hits
        else:
            verdict = "確認できない"
            comment = "質問・不明点の確認ができていません。"
            next_action = "最後に「今の時点で聞いておきたいことはありますか？」と確認しましょう。"

    elif item_id == "B-05":
        hits = _find_quotes(transcript, rule.get("positive", []), limit=2)
        if _has_comprehensive_health_check(transcript) or hits:
            verdict = "できている"
            comment = "怪我・病気・医師からの制限を含めて安全面を確認できています。"
            quotes = hits
        else:
            verdict = "確認できない"
            comment = "既往歴・禁忌の確認ができていません。"
            next_action = "「過去に大きな怪我や病気、お医者様から止められていることはありませんか？」を確認しましょう。"

    elif item_id == "Q-01":
        has_ask = any(k in transcript for k in rule.get("follow_up", []))
        name_hits = _find_quotes(transcript, rule.get("positive", []), limit=2)
        name_calls = re.findall(r"[ぁ-んァ-ン一-龥]{1,8}さん", transcript)
        if name_calls or name_hits:
            verdict = "できている"
            comment = "名前呼びや個別対応が確認できています。"
            quotes = name_hits or ([QuoteItem(timestamp="", text=name_calls[0])] if name_calls else [])
        elif has_ask:
            verdict = "一部できている"
            comment = "名前確認の流れはありますが、呼びかけは少ない可能性があります。"
        else:
            comment = "名前呼びや個別対応は確認できませんでした。"

    elif item_id == "Q-02":
        mono = rule.get("monotone", [])
        varied = rule.get("varied", [])
        mono_count = _count_phrase(transcript, mono)
        light_count = _count_phrase(transcript, ["はーい", "はぁい", "はいはい", "なるほど、なるほど"])
        thanks_count = transcript.count("ありがとうございます")
        varied_count = _count_phrase(transcript, varied)
        if mono_count >= 5 and varied_count < 3:
            verdict = "できていない"
            comment = "相槌が単調に聞こえる可能性があります。"
            quotes = _find_quotes(transcript, mono, limit=2)
            next_action = "相槌の後に一言要約を入れ、会話の受け止めを言葉にしましょう。"
        elif light_count >= 2 or thanks_count >= 8 or mono_count >= 3:
            verdict = "一部できている"
            comment = "相槌や感謝表現がやや繰り返され、接客が軽く聞こえる可能性があります。"
            quotes = _find_quotes(transcript, mono + ["ありがとうございます"], limit=2)
            next_action = "「はい」は短く言い切り、相槌の後に一言要約を入れると丁寧な印象になります。"
        else:
            verdict = "できている"
            comment = "相槌の単調さは大きく目立ちません。"
            quotes = _find_quotes(transcript, varied or mono, limit=1)

    elif item_id == "Q-03":
        neg = rule.get("negative", [])
        hits = _find_quotes(transcript, neg, limit=2)
        if hits:
            verdict = "一部できている" if len(hits) == 1 else "できていない"
            comment = "一部、不要な個人的感想や評価が入っている可能性があります。"
            quotes = hits
            next_action = "感想より「それで、今どんな状態ですか？」とお客様の話に戻しましょう。"
        else:
            verdict = "できている"
            comment = "余計な個人的感想は確認されませんでした。"

    elif item_id == "1-1":
        has_ask = any(k in transcript for k in rule.get("follow_up", []))
        name_hits = _find_quotes(transcript, rule.get("positive", []), limit=2)
        name_calls = re.findall(r"[ぁ-んァ-ン一-龥]{1,8}さん", transcript)
        if name_calls and name_hits:
            verdict = "できている"
            comment = "お客様のお名前を確認し、会話の中で呼べています。"
            quotes = name_hits[:1]
            quotes.append(QuoteItem(timestamp="", text=name_calls[0]))
        elif name_calls:
            verdict = "一部できている"
            comment = "お客様の名前で呼べていますが、確認のやり取りが弱い可能性があります。"
            quotes = [QuoteItem(timestamp="", text=name_calls[0])]
            next_action = "冒頭で「お名前をお伺いしてもよろしいですか？」と確認したことを言葉にしましょう。"
        elif has_ask or name_hits:
            verdict = "一部できている"
            comment = "名前の確認はありますが、会話中の呼びかけが少ない可能性があります。"
            quotes = name_hits
            next_action = "お名前を確認したら、2〜3回は自然なタイミングでお呼びしてみましょう。"
        else:
            comment = "お名前の確認・呼びかけが文字起こしから確認できませんでした。"
            next_action = "冒頭でお名前を確認し、「〇〇さん」と呼びかける習慣をつけましょう。"

    elif item_id == "1-2":
        concerns = rule.get("concern", [])
        deep = rule.get("deep_dive", [])
        c_hit = any(k in transcript for k in concerns)
        d_quotes = _find_quotes(transcript, deep, limit=2)
        if c_hit and _is_deep_dive_done(transcript):
            verdict = "できている"
            comment = "悩みの部位だけでなく、背景・場面・理想まで複数観点で深掘りできています。"
            quotes = _find_quotes(transcript, concerns, limit=1) + d_quotes[:2]
        elif c_hit and (d_quotes or _has_partial_deep_dive(transcript)):
            verdict = "一部できている"
            comment = "悩みの部位や一部背景は確認できていますが、生活場面・困る瞬間・心理的背景の深掘りがもう一歩です。"
            quotes = _find_quotes(transcript, concerns, limit=1) + d_quotes[:1]
            next_action = "部位確認の後に「どんな時に一番気になりますか？」「日常で困る瞬間はありますか？」を追加しましょう。"
        elif c_hit:
            verdict = "一部できている"
            comment = "悩みのヒアリングはありますが、深掘り質問が不足しています。"
            quotes = _find_quotes(transcript, concerns, limit=2)
            next_action = "部位名だけで終わらず、「いつから」「どんな場面で」「何が嫌か」まで確認しましょう。"
        else:
            comment = "悩みのヒアリング・深掘りが確認できませんでした。"
            next_action = "お悩み→具体部位→きっかけの順で必ず深掘りしましょう。"

    elif item_id == "1-3":
        kws = rule.get("positive", [])
        hits = _find_quotes(transcript, kws, limit=2)
        deadline = any(k in transcript for k in ["いつまで", "いつ頃", "期限"])
        ideal = any(k in transcript for k in ["理想", "なりたい", "目標"])
        if hits and deadline and ideal:
            verdict = "できている"
            comment = "理想像と期限の両方を確認できています。"
        elif hits:
            verdict = "一部できている"
            comment = "目標に関する確認はありますが、期限または理想のどちらかが弱い可能性があります。"
            next_action = "「いつまでに」「どうなりたいか」をセットで必ず確認しましょう。"
        else:
            comment = "目標設定の確認が不足しています。"
            next_action = "理想体型と達成時期を具体的に聞きましょう。"
        quotes = hits

    elif item_id == "1-4":
        kws = rule.get("positive", [])
        hits = _find_quotes(transcript, kws, limit=2)
        if _has_comprehensive_health_check(transcript) or len(hits) >= 2:
            verdict = "できている"
            comment = "怪我・病気・医師からの制限を含めて、安全面の確認ができています。"
        elif hits:
            verdict = "一部できている"
            comment = "健康確認はありますが、もう少し丁寧に確認すると安心感が増します。"
            next_action = "怪我・病気・医師から止められていることをセットで確認しましょう。"
        else:
            comment = "既往歴・健康状態の確認が見当たりません。"
            next_action = "カウンセリング序盤で必ず健康状態を確認しましょう。"
        quotes = hits

    elif item_id == "1-5":
        kws = rule.get("positive", [])
        hits = _find_quotes(transcript, kws, limit=2)
        if len(hits) >= 2:
            verdict = "できている"
            comment = "これまでの取り組みを確認できています。"
        elif hits:
            verdict = "一部できている"
            comment = "過去の取り組みには触れていますが、詳細確認が足りない可能性があります。"
            next_action = "ジム・ダイエット等の経験と効果・継続理由を聞きましょう。"
        else:
            comment = "これまでの取り組みの確認が不足しています。"
            next_action = "「これまでどんなことを試されましたか？」を必ず入れましょう。"
        quotes = hits

    elif item_id == "2-1":
        misc = rule.get("misconception", [])
        clarify = rule.get("clarify", [])
        m_quotes = _find_quotes(transcript, misc, limit=1)
        c_quotes = _find_quotes(transcript, clarify, limit=2)
        if m_quotes and c_quotes:
            verdict = "できている"
            comment = "お客様の理解を確認し、姿勢・根本原因の視点で整理できています。"
            quotes = m_quotes + c_quotes[:1]
        elif m_quotes:
            verdict = "一部できている"
            comment = "体重・筋トレ等の話題はありますが、誤解の整理まで至っていない可能性があります。"
            quotes = m_quotes
            next_action = "「体重だけでは解決しにくい場合もあります」等、優しく確認しましょう。"
        else:
            verdict = "一部できている"
            comment = "誤解の確認は限定的です。必要に応じて丁寧に整理しましょう。"
            next_action = "お客様の思い込みを否定せず、一緒に確認する質問を入れましょう。"

    elif item_id == "2-2":
        pos = rule.get("positive", [])
        neg = rule.get("negative", [])
        neg_count = _count_phrase(transcript, neg)
        pos_quotes = _find_quotes(transcript, pos, limit=2)
        if pos_quotes and neg_count == 0:
            verdict = "できている"
            comment = "否定せず、お客様自身が気づける問いかけができています。"
            quotes = pos_quotes
        elif neg_count > 0:
            verdict = "できていない"
            comment = "否定的な表現が見られ、お客様の気持ちを損ねる可能性があります。"
            quotes = _find_quotes(transcript, neg, limit=1)
            next_action = "「かもしれませんね」「一緒に確認しましょう」等、受容的な言い回しに変えましょう。"
        elif pos_quotes:
            verdict = "一部できている"
            comment = "気づきを促す要素はありますが、もう一歩深い問いかけが効果的です。"
            quotes = pos_quotes
            next_action = "「姿勢が原因かもしれませんが、どう感じますか？」のような問いを試しましょう。"
        else:
            comment = "自然な気づきを促す問いかけが弱いです。"
            next_action = "原因を教えるのではなく、質問で本人に気づいてもらう工夫をしましょう。"

    elif item_id == "3-1":
        kws = rule.get("positive", [])
        hits = _find_quotes(transcript, kws, limit=2)
        if len(hits) >= 2:
            verdict = "できている"
            comment = "お客様の言葉を要約・言い換えするオウム返しができています。"
        elif hits:
            verdict = "一部できている"
            comment = "オウム返しはありますが、もう少し具体的な言い換えがあると良いです。"
            next_action = "「〇〇なんですね」と相手の言葉を短く返してから次の質問へ。"
        else:
            comment = "要約・言い換え型のオウム返しが少ないです。"
            next_action = "単なる「はい」ではなく、内容を返すオウム返しを意識しましょう。"
        quotes = hits

    elif item_id == "3-2":
        mono = rule.get("monotone", [])
        varied = rule.get("varied", [])
        mono_count = _count_phrase(transcript, mono)
        light_count = _count_phrase(transcript, ["はーい", "はぁい", "はいはい", "なるほど、なるほど"])
        thanks_count = transcript.count("ありがとうございます")
        varied_count = _count_phrase(transcript, varied)
        if mono_count >= 5 and varied_count < 3:
            verdict = "できていない"
            comment = "「なるほどですね」「はい、はい」等の単調な相槌が目立ちます。"
            quotes = _find_quotes(transcript, mono, limit=2)
            next_action = "相槌の前に一言要約を入れる、質問を挟むなど変化をつけましょう。"
        elif light_count >= 2 or thanks_count >= 8:
            verdict = "一部できている"
            comment = "相槌や感謝表現がやや繰り返され、接客が軽く聞こえる可能性があります。"
            quotes = _find_quotes(transcript, ["はーい", "はぁい", "はいはい", "なるほど、なるほど", "ありがとうございます"], limit=2)
            next_action = "「はい」は短く言い切り、相槌の後に一言要約を入れると丁寧な印象になります。"
        elif mono_count >= 3:
            verdict = "一部できている"
            comment = "相槌はありますが、やや単調な部分があります。"
            quotes = _find_quotes(transcript, mono, limit=1)
            next_action = "共感＋具体確認（「それはいつ頃からですか？」）のセットを意識しましょう。"
        else:
            verdict = "できている"
            comment = "相槌に一定の変化があり、単調さは目立ちません。"
            quotes = _find_quotes(transcript, varied or mono, limit=1)

    elif item_id == "3-3":
        neg = rule.get("negative", [])
        hits = _find_quotes(transcript, neg, limit=2)
        if len(hits) >= 2:
            verdict = "できていない"
            comment = "不要な個人的感想・評価が入っています。"
            quotes = hits
            next_action = "感想より「それで、今どんな状態ですか？」とお客様の話に戻しましょう。"
        elif hits:
            verdict = "一部できている"
            comment = "一部、余計な感想が混ざっている可能性があります。"
            quotes = hits
            next_action = "「いいですね！」等の評価は控え、傾聴に徹しましょう。"
        else:
            verdict = "できている"
            comment = "余計な個人的感想は確認されませんでした。"

    elif item_id == "3-4":
        neg = rule.get("negative", [])
        hits = _find_quotes(transcript, neg, limit=2)
        yes_count = transcript.count("はい")
        if hits or yes_count > 15:
            verdict = "できていない" if hits else "一部できている"
            comment = "相槌の連呼や押し付けがましい印象を与える可能性があります。"
            quotes = hits or _find_quotes(transcript, ["はい"], limit=1)
            next_action = "相槌の間に間を取り、要約や質問で会話のテンポを整えましょう。"
        else:
            verdict = "できている"
            comment = "不快感を与える相槌の連呼は確認されませんでした。"

    elif item_id == "3-5":
        pos = rule.get("positive", [])
        quotes = _find_quotes(transcript, pos[:3], limit=1)
        verdict = "一部できている" if quotes else "確認できない"
        comment = TONE_PROVISIONAL_NOTE
        if quotes:
            comment = f"文字起こし上は丁寧な表現が見られます。{TONE_PROVISIONAL_NOTE}"
        else:
            comment = f"声のトーンを推測できる発言が少ないです。{TONE_PROVISIONAL_NOTE}"
        next_action = "重要な確認（目標・悩みの要約）の語尾を少し上げ、笑顔を意識して話す（※音声で自己確認）。"

    else:
        comment = "評価対象外の項目です。"

    return ItemEvaluation(
        id=item_id,
        category=category,
        label=label,
        verdict=verdict,
        quotes=quotes[:2],
        comment=comment,
        next_action=next_action,
    )


def _rule_evaluate_all(transcript: str) -> list[ItemEvaluation]:
    criteria = load_criteria()
    results: list[ItemEvaluation] = []
    for cat in criteria["categories"]:
        for item in cat["items"]:
            results.append(_rule_evaluate_item(transcript, cat["name"], item))
    return results


def _items_to_checks(items: list[ItemEvaluation]) -> list[CheckItem]:
    checks: list[CheckItem] = []
    for item in items:
        passed = item.verdict == "できている"
        evidence = item.comment
        if item.quotes:
            q = item.quotes[0]
            ts = f"[{q.timestamp}] " if q.timestamp else ""
            evidence += f" 根拠: {ts}「{q.text}」"
        checks.append(
            CheckItem(
                id=item.id,
                category=item.category,
                label=item.label,
                passed=passed,
                evidence=evidence,
                suggestion=item.next_action,
            )
        )
    return checks


def _category_scores(items: list[ItemEvaluation]) -> list[ScoreItem]:
    by_cat: dict[str, list[int]] = {}
    for item in items:
        by_cat.setdefault(item.category, []).append(VERDICT_SCORE[item.verdict])
    scores: list[ScoreItem] = []
    short_names = {
        "1. 基本スキル・ヒアリング": "ヒアリング力",
        "2. 顧客理解とコーチング": "コーチング力",
        "3. コミュニケーション・マナー": "コミュニケーション力",
    }
    for cat, vals in by_cat.items():
        avg = round(sum(vals) / len(vals)) if vals else 0
        cat_items = [i for i in items if i.category == cat]
        ok = sum(1 for i in cat_items if i.verdict == "できている")
        unk = sum(1 for i in cat_items if i.verdict == "確認できない")
        scores.append(
            ScoreItem(
                name=short_names.get(cat, cat),
                score=avg,
                comment=f"できている {ok}/{len(cat_items)} ・確認できない {unk}（カテゴリ内）",
            )
        )
    return scores


def _weighted_overall_score(items: list[ItemEvaluation]) -> int:
    if not items:
        return 0
    weighted_sum = 0.0
    weight_total = 0.0
    for item in items:
        weight = ITEM_SCORE_WEIGHTS.get(item.id, 1.0)
        weighted_sum += VERDICT_SCORE[item.verdict] * weight
        weight_total += weight
    return round(weighted_sum / weight_total) if weight_total else 0


def _overall_label(score: int) -> str:
    if score >= 85:
        return "優秀"
    if score >= 70:
        return "良好"
    if score >= 55:
        return "育成中"
    return "要サポート"


def _parse_llm_items(data: list[dict], fallback: list[ItemEvaluation]) -> list[ItemEvaluation]:
    fb_map = {i.id: i for i in fallback}
    items: list[ItemEvaluation] = []
    for raw in data:
        item_id = raw.get("id", "")
        fb = fb_map.get(item_id)
        verdict = raw.get("verdict", fb.verdict if fb else "一部できている")
        if verdict not in VERDICT_SCORE:
            verdict = "一部できている"
        quotes = [
            QuoteItem(timestamp=q.get("timestamp", ""), text=q.get("text", ""))
            for q in raw.get("quotes", [])[:2]
            if q.get("text")
        ]
        items.append(
            ItemEvaluation(
                id=item_id,
                category=raw.get("category", fb.category if fb else ""),
                label=raw.get("label", fb.label if fb else ""),
                verdict=verdict,  # type: ignore[arg-type]
                quotes=quotes,
                comment=raw.get("comment", fb.comment if fb else ""),
                next_action=raw.get("next_action", fb.next_action if fb else ""),
            )
        )
    if len(items) < len(fallback):
        seen = {i.id for i in items}
        items.extend(i for i in fallback if i.id not in seen)
    return items


def _llm_evaluate(
    transcript: str,
    staff_name: str,
    rule_items: list[ItemEvaluation],
) -> dict | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None

    from openai import OpenAI

    criteria = load_criteria()
    items_spec = []
    for cat in criteria["categories"]:
        for item in cat["items"]:
            items_spec.append(
                f"- {item['id']} ({cat['name']}): {item['label']} — {item['guide']}"
            )
    rule_hints = "\n".join(
        f"- {i.id}: {i.verdict}（{i.comment[:80]}）" for i in rule_items[:12]
    )

    strict_list = "、".join(sorted(STRICT_ITEM_IDS))
    prompt = f"""あなたはピラティススタジオの現場責任者（店長クラス）です。
カウンセリングの文字起こしを、項目ごとに厳密に評価し、部下スタッフへの添削フィードバックを作成してください。
評価意図は「一般的なカウンセリング品質」ではなく「Pilates iU標準カウンセリングの再現率」です。
接客印象よりも、S/A/Bランクのマニュアル項目を実際に確認できているかを優先してください。

{FEEDBACK_TONE_RULES}

{IMPROVEMENT_PSYCHOLOGY_EXAMPLE}

【スタッフ名】{staff_name}

【評価項目】
{chr(10).join(items_spec)}

【判定】各項目は必ず以下のいずれか:
- できている
- 一部できている
- できていない
- 確認できない

【厳格ルール — 最重要】
1. 「できている」は quotes に文字起こし原文の引用が1件以上ある場合のみ。引用なしなら必ず「確認できない」
2. 引用は文字起こしに実在する発言のみ（創作禁止）
3. 厳しめ評価が必要な項目: {strict_list}
   - S-01: 体重変化/過去の取り組み/結果の確認なしに、姿勢や体重以外の原因を一方的に説明しているだけなら「一部できている」
   - S-01: 体重変化・過去の取り組み・取り組み結果・体重以外の可能性・姿勢診断への導線が揃って初めて「できている」
   - S-02: 過去の取り組みの引用がなければ「できている」不可
   - S-03: 結果・変化・続かなかった理由を明確に確認した引用がなければ「できている」不可
   - S-04: 理想体型の引用がなければ「できている」不可
   - S-05: 期限・希望時期の引用がなければ「できている」不可
   - A-01: 体重変化と悩みの関係を明確に確認した引用がなければ「できている」不可
   - B-01: 通う時間帯は、明確な確認質問がなければ「確認できない」
   - B-02: 仕事・業務内容は、明確な確認質問がなければ「確認できない」
   - B-03: 睡眠状態は、明確な確認質問がなければ「確認できない」
   - B-05: 怪我・病気・医師からの制限の確認引用がなければ「できている」不可
4. Q系（接客品質）はマニュアル遵守より優先しない。改善点はS/Aランクを優先
5. 甘く評価しない。曖昧なら「一部できている」または「確認できない」を選ぶ
6. next_action は具体セリフ例を含める（下記参照）
7. 育成トーンを保ちつつ、課題は明確に
8. 改善点は評価項目単位ではなく、お客様の主訴を中心に書く
   - 「何を聞けなかったか」ではなく「主訴に対して次に何を聞けば提案につながるか」を優先
   - ウエスト・内もも・二の腕・ヒップなどの主訴がある場合は、主訴ごとに「いつから」「どんな場面で」「過去の取り組みで変わった/変わらなかったか」を聞く流れにする
   - 腰痛など補足情報は、主訴より優先して改善点にしない
   - Bランク未確認（通う時間帯・仕事・睡眠）はLINEの主改善点ではなく補足扱い
   - 姿勢診断前に原因を断定しない。「詳しく見させていただきます」「一緒に確認してみましょう」を使う

{NEXT_ACTION_EXAMPLES}

【ルールベース参考】
{rule_hints}

【文字起こし】
{transcript[:14000]}

JSON形式:
{{
  "item_evaluations": [
    {{
      "id": "S-01",
      "category": "Sランク: 成約直結項目",
      "label": "...",
      "verdict": "できている|一部できている|できていない|確認できない",
      "quotes": [{{"timestamp": "00:12", "text": "引用"}}],
      "comment": "...",
      "next_action": "状況→具体セリフ→狙い の形式"
    }}
  ],
  "overall_assessment": {{
    "staff_status": "今のスタッフの状態（2-3文。点数より育成視点）",
    "top_issue": "一番の課題（1文）",
    "priority_improvement": "次回最優先で改善すること（具体行動1文）",
    "overall_comment": "総合コメント（2-3文）"
  }},
  "good_points": [{{"title": "...", "body": "良かった理由。〜だと思います／〜がおすすめです の語尾"}}],
  "improvement_points": [
    {{
      "title": "改善テーマ（内部用。本文に見出しとして出さない）",
      "body": "改善点→なぜ重要か→お客様心理→質問の流れと各質問の狙い→会話例。番号見出しなしの自然な段落。テクニックだけの「○○と言いましょう」禁止"
    }}
  ],
  "next_focus": ["具体行動1（セリフ例を含む）", "..."],
  "staff_feedback": "（使わない。空文字でよい）"
}}

improvement_points は最大2件。各 body は5要素（改善点/重要性/お客様心理/質問の流れと狙い/会話例）を自然な文章で。
「と言いましょう」「と伝えましょう」だけで終わらせない。文字起こしから読み取れるお客様の心理に触れる。
改善点は評価項目名ではなく、主訴に対する次の質問へ翻訳する。
good_points は1〜2件。名前なし。具体的事実を含む自然な文章。
"""

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "日本語のみ。JSONのみ返してください。発言引用は文字起こしに存在するものだけ。"
                        "社内LINEの添削文。名前・絵文字・番号見出し・箇条書き禁止。"
                        "「〜だと思います」「〜がおすすめです」「〜と感じました」を使う。"
                        "改善点は心理・意図・質問の狙い・会話の流れ・実際のセリフまで書く。"
                        "テクニックだけの指示は禁止。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        return json.loads(response.choices[0].message.content or "{}")
    except Exception:
        return None


def _build_good_points(items: list[ItemEvaluation]) -> list[FeedbackSection]:
    sections: list[FeedbackSection] = []
    for item in items:
        if item.verdict != "できている":
            continue
        sections.append(
            FeedbackSection(
                title=item.label,
                body=item.comment,
                quotes=item.quotes,
            )
        )
    return sections[:3]


_BANNED_PHRASES = (
    ("すると良いですよ", "がおすすめです"),
    ("なりますよ", "なると思います"),
    ("できますよ", "できると思います"),
    ("変わりますよ", "変わると思います"),
    ("ますよ", "です"),
    ("一緒にやっていきましょう", ""),
    ("いつでも声をかけてください", ""),
    ("次に伸ばすポイント", "改善点"),
    ("本日のフィードバック", ""),
    ("今日のカウンセリング、確認しました", ""),
    ("今日のカウンセリング確認しました", ""),
)


def _sanitize_tone(text: str) -> str:
    """添削トーンの禁止表現を除去・置換する。"""
    if not text:
        return text
    for banned, replacement in _BANNED_PHRASES:
        text = text.replace(banned, replacement)
    text = re.sub(r"[👍🌱📋📌🎯⚡📊✉️✅▶■]", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _strip_improvement_headings(body: str) -> str:
    """番号見出しを除去して本文だけ残す。"""
    parts: list[str] = []
    for line in body.splitlines():
        cleaned = re.sub(r"^[①②③④⑤][^：:\n]*[：:]\s*", "", line.strip())
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts) if parts else body.strip()


def _extract_example_phrase(next_action: str) -> str:
    """next_action から実際のセリフ例を取り出す。"""
    quoted = re.findall(r"「([^」]+)」", next_action)
    if quoted:
        return quoted[-1]
    text = next_action.strip().rstrip("。")
    if not text:
        return ""
    return text


def _customer_quote_hint(item: ItemEvaluation) -> str:
    if item.quotes:
        return f"「{item.quotes[0].text}」といった発言から、お客様の本音や不安が少し読み取れました。"
    return "お客様はまだ自分の悩みを言葉にしきれていない可能性があり、急かされると不安を感じやすいと思います。"


def _psychology_fallback_for_item(item: ItemEvaluation) -> str:
    """ルールベース改善点を心理・意図ベースの文章に整える。"""
    reason = item.comment.strip() if item.comment else "文字起こしを聞くと、ここにもう一段深く入れる余地がありました。"
    customer_hint = _customer_quote_hint(item)
    phrase = _extract_example_phrase(item.next_action or "")

    guides: dict[str, str] = {
        "S-01": (
            "主訴に対して、体重以外の可能性を一緒に確認する流れを作れるとさらに良くなると思います。"
            f"{reason} "
            f"{customer_hint} "
            "ここでは評価項目を埋めることより、ウエストや内ももなど相手が一番変えたい部位について、"
            "いつから気になるのか、どんな場面で気になるのか、体重が変わる前も同じ悩みがあったのかを確認することが大切だと思います。"
            "そのうえで「体重以外にも関係している可能性があるので、後ほど姿勢を詳しく見させていただきながら一緒に確認してみましょう」"
            "とつなげると、診断前に原因を断定せず提案につながる流れになります。"
        ),
        "S-03": (
            "過去の取り組み結果を、主訴に結びつけて確認できると提案につながりやすくなると思います。"
            f"{reason} "
            f"{customer_hint} "
            "単に何をしたかで終わらず、「その方法でウエストや内ももは変わりましたか？」"
            "「変わった部分と変わらなかった部分はありますか？」と聞くと、"
            "お客様自身も今までの方法では届かなかったポイントに気づきやすくなります。"
            "最後は「後ほど姿勢も詳しく見させていただきながら、一緒に確認してみましょう」とつなげるのがおすすめです。"
        ),
        "2-2": (
            "お客様に「今までの考え方だけでは改善しないかもしれない」と気づいていただくことが重要だと思います。"
            f"{reason} "
            f"{customer_hint} "
            "否定されると抵抗を感じやすいので、いつから悩んでいるか、何を原因だと思っているか、"
            "過去と現在で何が変わったかを確認してから伝える流れがおすすめです。"
            "各質問で引き出したいのは、本人の思い込みと不安の根っこです。"
            "会話例としては、「いつ頃から気になり始めましたか？」→「今まで何が原因だとお考えでしたか？」→"
            "「体重以外にも関係している可能性があるので、後ほど姿勢を詳しく見させていただきながら一緒に確認してみましょう。」"
            "と進めると、お客様の納得感につながりやすいと思います。"
        ),
        "2-1": (
            "お客様の思い込みを整理し、安心して次のステップに進めることが重要だと思います。"
            f"{reason} "
            f"{customer_hint} "
            "「体重を減らせば解決する」などの単純な考えを持っている場合、"
            "否定せず「他にも原因があるかもしれませんね」と一緒に確認する姿勢が大切だと思います。"
            "まず今の考えを聞き、次に過去の経験や変化を確認し、最後に別の視点を提案する流れがおすすめです。"
            "会話例としては、「今までどんなふうに改善しようとされていましたか？」→"
            "「その方法で変化は感じられましたか？」→"
            "「体重以外にも関係している可能性があるので、後ほど姿勢を詳しく見させていただきながら一緒に確認してみましょう。」"
            "と進めるとよいと思います。"
        ),
        "1-2": (
            "お客様が「何が本当に気になっているのか」を自分の言葉で語れることが重要だと思います。"
            f"{reason} "
            f"{customer_hint} "
            "漠然とした悩みのままだと、お客様自身も不安が残りやすいと思います。"
            "まず気になる部位や感覚を聞き、次にいつから・どんな時に気になるかを確認し、"
            "最後に理想の状態を想像してもらう流れがおすすめです。"
            "各質問で引き出したいのは、悩みの具体像と背景です。"
        ),
    }

    if item.id in guides:
        return _sanitize_tone(guides[item.id])

    flow = (
        f"まず背景を確認し、次にお客様の考えを聞き、最後に一緒に整理する流れがおすすめです。"
    )
    if phrase:
        dialogue = f"会話例としては、「{phrase}」と伝える前に、お客様の考えを確認する一言を挟むと伝わりやすいと思います。"
    else:
        dialogue = "会話例としては、確認の質問を2〜3段階入れてから提案する形がおすすめです。"

    return _sanitize_tone(
        f"「{item.label}」をもう一段意識すると、カウンセリングの質が上がると思います。"
        f"{reason} "
        f"{customer_hint} "
        "テクニックだけでなく、お客様が安心して話せる流れを作ることが大切だと思います。"
        f"{flow} "
        f"{dialogue}"
    )


def _format_improvement_body(item: ItemEvaluation) -> str:
    """改善点を心理・意図ベースの自然な段落に整える。"""
    return _psychology_fallback_for_item(item)


def _is_technique_only_feedback(body: str) -> bool:
    """テクニックだけの短い指示かどうか。"""
    if len(body) > 200:
        return False
    technique_markers = ("と言いましょう", "と伝えましょう", "と聞きましょう", "を意識しましょう")
    return any(marker in body for marker in technique_markers)


def _normalize_improvement_body(body: str, title: str = "") -> str:
    """LLM出力を自然な段落に揃える（見出し・箇条書きを除去）。"""
    body = _sanitize_tone(_strip_improvement_headings(body))
    if not body or _is_technique_only_feedback(body):
        theme = title or "ここ"
        return (
            f"「{theme}」について、お客様の心理とカウンセリングの意図まで伝えると伝わりやすいと思います。"
            "なぜその質問が必要か、何を引き出したいか、どんな流れで会話を進めるかまで含めて説明するのがおすすめです。"
            "文字起こし上、改善の余地がありました。"
        )
    if "お客様" not in body and title:
        body = (
            f"{body} "
            "お客様がどう感じているか、なぜその流れが必要かも添えると、スタッフが意図を持って話せると思います。"
        )
    if "「" not in body:
        body = (
            f"{body} "
            "会話例として、確認の質問を挟んだうえで具体セリフを入れる形がおすすめです。"
        )
    return body


def _max_improvement_count() -> int:
    return int(load_feedback_rules().get("max_improvement_points", 2))


IMPROVEMENT_PRIORITY = {
    # 第0階層: 成約に直結する最重要項目
    "S-01": 1,
    # Sランク
    "S-03": 10,
    "S-02": 11,
    "S-04": 12,
    "S-05": 13,
    # Aランク
    "A-01": 20,
    "A-03": 21,
    "A-02": 22,
    # Bランク
    "B-01": 40,
    "B-02": 41,
    "B-03": 42,
    "B-04": 43,
    "B-05": 44,
    # 接客品質
    "Q-02": 60,
    "Q-01": 61,
    "Q-03": 62,
    # 旧項目互換
    "1-2": 70,
    "2-1": 71,
    "2-2": 72,
    "1-3": 73,
    "1-4": 74,
    "3-2": 80,
    "3-4": 81,
    "3-1": 82,
    "3-3": 83,
    "3-5": 84,
}

VERDICT_PRIORITY = {
    "できていない": 0,
    "確認できない": 1,
    "一部できている": 2,
    "できている": 99,
}


def _improvement_sort_key(item: ItemEvaluation) -> tuple[int, int, str]:
    return (
        IMPROVEMENT_PRIORITY.get(item.id, 90),
        VERDICT_PRIORITY.get(item.verdict, 50),
        item.id,
    )


def _build_improvement_points(
    items: list[ItemEvaluation], overall_score: int
) -> list[FeedbackSection]:
    ordered = sorted(
        [i for i in items if i.verdict != "できている"],
        key=_improvement_sort_key,
    )
    limit = _max_improvement_count()
    sections: list[FeedbackSection] = []
    for item in ordered[:limit]:
        sections.append(
            FeedbackSection(
                title=item.label,
                body=_format_improvement_body(item),
                quotes=item.quotes,
            )
        )
    if not sections and overall_score >= 85:
        partial = [i for i in items if i.verdict == "一部できている"]
        growth = partial[0] if partial else items[0]
        sections.append(
            FeedbackSection(
                title="さらに伸ばせるポイント",
                body=_format_improvement_body(growth),
                quotes=growth.quotes,
            )
        )
    return sections


def _build_next_focus(items: list[ItemEvaluation]) -> list[str]:
    actions: list[str] = []
    for item in items:
        if item.verdict != "できている" and item.next_action:
            actions.append(item.next_action)
    if not actions:
        for item in items:
            if item.next_action:
                actions.append(item.next_action)
                break
    return actions[:3]


def _build_manager_summary(assessment: OverallAssessment, label: str) -> str:
    parts = [f"総合{label}"]
    if assessment.staff_status:
        parts.append(assessment.staff_status)
    if assessment.top_issue:
        parts.append(f"課題: {assessment.top_issue}")
    return "。".join(parts[:2]) + ("。" if len(parts) <= 2 else "")


def _fallback_assessment(
    items: list[ItemEvaluation], overall_score: int, staff_name: str
) -> OverallAssessment:
    weak = [i for i in items if i.verdict == "できていない"]
    partial = [i for i in items if i.verdict == "一部できている"]
    top = weak[0] if weak else (partial[0] if partial else None)
    name = staff_name if staff_name != "（未入力）" else "担当者"

    if overall_score >= 85:
        status = f"{name}さんは全体として安定したカウンセリングができています。"
    elif overall_score >= 70:
        status = f"{name}さんは基本スキルを押さえつつ、いくつか伸ばしどころがあります。"
    else:
        status = f"{name}さんはカウンセリングの型を身につけ最中です。一つずつ改善していきましょう。"

    return OverallAssessment(
        staff_status=status,
        top_issue=top.label if top else "特になし（現状維持でOK）",
        priority_improvement=top.next_action if top and top.next_action else "今の良い点を継続し、深掘り質問を意識する",
        overall_comment=f"全{len(items)}項目中、できている {sum(1 for i in items if i.verdict == 'できている')} 項目。"
        f"総合{overall_score}点。{top.label if top else '引き続きフォローを'}が次の焦点です。",
    )


def _format_good_point(gp: FeedbackSection) -> str:
    """良かった点を自然な1文に整える。"""
    body = _sanitize_tone(gp.body.strip()).rstrip("。")
    title = gp.title.strip()
    if any(marker in body for marker in ("だと思います", "がおすすめです", "と感じました", "が気になりました")):
        return f"{body}。" if body.endswith("。") else f"{body}。"
    if title and title not in body:
        return f"{title}はしっかりできていて、{body}と感じました。"
    return f"{body}と感じました。"


def _assemble_staff_feedback(
    staff_name: str,
    good_points: list[FeedbackSection],
    improvement_points: list[FeedbackSection],
) -> str:
    """固定の冒頭・締めでスタッフ向けLINE文面を組み立てる。"""
    rules = load_feedback_rules()
    opening = rules["opening"]
    closing = rules["closing"]
    lines: list[str] = []
    if isinstance(opening, list):
        lines.extend(opening)
    else:
        lines.extend(str(opening).split("\n"))
    lines.append("")

    for gp in good_points[:2]:
        lines.append(_format_good_point(gp))
    if good_points:
        lines.append("")

    limit = _max_improvement_count()
    selected = improvement_points[:limit]
    if selected:
        intro = rules.get("improvement_intro", "改善点は{count}つです。").format(count=len(selected))
        lines.append(intro)
        lines.append("")
        for ip in selected:
            lines.append(_normalize_improvement_body(ip.body, ip.title))
            lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    lines.extend(["", closing])
    return "\n".join(lines)


def _fallback_staff_feedback(
    staff_name: str,
    good_points: list[FeedbackSection],
    improvement_points: list[FeedbackSection],
    next_focus: list[str],
) -> str:
    return _assemble_staff_feedback(staff_name, good_points, improvement_points)


def _pm_review_debug_enabled() -> bool:
    """PMレビュー前後の長文ログは明示的に有効化した時だけ出す。"""
    return os.getenv("PM_REVIEW_DEBUG", "").lower() in {"1", "true", "yes", "on"}


def _log_pm_review_debug(before: str, after: str, notes: str, changed: bool) -> None:
    if not _pm_review_debug_enabled():
        return
    logger.info("=== PMレビュー DEBUG ===")
    logger.info("PMレビュー changed=%s notes=%s", changed, notes)
    logger.info("--- PMレビュー前 staff_feedback ---\n%s", before)
    logger.info("--- PMレビュー後 reviewed_feedback ---\n%s", after)
    logger.info("=== PMレビュー DEBUG END ===")


def _build_line_text(result: EvaluationResult) -> str:
    lines = [
        "━━━━━━━━━━━━━━━━",
        "📋 カウンセリング育成フィードバック",
        "━━━━━━━━━━━━━━━━",
        f"担当: {result.staff_name}",
        f"日付: {result.session_date}",
        f"総合: {result.overall_score}点（{result.overall_label}）",
        "",
        "📌 今の状態",
        result.overall_assessment.staff_status or result.overall_comment,
        "",
        "🎯 一番の課題",
        result.overall_assessment.top_issue,
        "",
        "⚡ 次回最優先",
        result.overall_assessment.priority_improvement,
        "",
    ]

    if result.scores:
        lines.append("📊 カテゴリスコア")
        for s in result.scores:
            bar = "▓" * (s.score // 10) + "░" * (10 - s.score // 10)
            lines.append(f"・{s.name} {s.score}点 {bar}")
        lines.append("")

    if result.good_points:
        lines += ["👍 良かった点"]
        for gp in result.good_points:
            lines.append(f"▶ {gp.title}")
            lines.append(f"  {gp.body}")
        lines.append("")

    if result.improvement_points:
        lines += ["🌱 次に伸ばすポイント（最大2件）"]
        for ip in result.improvement_points[: _max_improvement_count()]:
            lines.append(f"▶ {ip.title}")
            for body_line in ip.body.splitlines():
                lines.append(f"  {body_line}")
        lines.append("")

    if result.next_focus:
        lines += ["✅ 次回意識すること"]
        for item in result.next_focus:
            lines.append(f"□ {item}")
        lines.append("")

    lines += ["✉️ スタッフへのメッセージ", result.staff_feedback, "", "━━━━━━━━━━━━━━━━"]
    return "\n".join(lines)


def evaluate_counseling(
    transcript: str,
    *,
    staff_name: str = "（未入力）",
    session_date: str = "（未入力）",
    source: str = "audio",
    use_llm: bool = True,
) -> EvaluationResult:
    low_quality = _is_low_quality_transcript(transcript)
    rule_items = _rule_evaluate_all(transcript)
    items = rule_items
    assessment = _fallback_assessment(rule_items, 0, staff_name)
    good_points: list[FeedbackSection] = []
    improvement_points: list[FeedbackSection] = []
    next_focus: list[str] = []

    if use_llm and not low_quality:
        llm_data = _llm_evaluate(transcript, staff_name, rule_items)
        if llm_data:
            items = _parse_llm_items(llm_data.get("item_evaluations", []), rule_items)
            oa = llm_data.get("overall_assessment", {})
            assessment = OverallAssessment(
                staff_status=oa.get("staff_status", ""),
                top_issue=oa.get("top_issue", ""),
                priority_improvement=oa.get("priority_improvement", ""),
                overall_comment=oa.get("overall_comment", ""),
            )
            good_points = [
                FeedbackSection(
                    title=g["title"],
                    body=_sanitize_tone(g.get("body", "")),
                )
                for g in llm_data.get("good_points", [])[:2]
                if g.get("title")
            ]
            improvement_points = [
                FeedbackSection(
                    title=g["title"],
                    body=_normalize_improvement_body(g.get("body", ""), g.get("title", "")),
                )
                for g in llm_data.get("improvement_points", [])[: _max_improvement_count()]
                if g.get("title")
            ]
            next_focus = llm_data.get("next_focus", [])[:3]

    items = _enforce_evidence_strictness(items, transcript, low_quality=low_quality)
    items = _supplement_quotes_from_transcript(items, rule_items, transcript)
    items = _reconcile_verdicts(items, transcript)
    items = _enforce_evidence_strictness(items, transcript, low_quality=low_quality)

    tq = _transcript_quality(transcript)
    if tq == "medium":
        for item in items:
            if item.id in STRICT_ITEM_IDS and item.verdict == "できている" and len(item.quotes) < 2:
                item.verdict = "一部できている"
        quality_note = "※文字起こしに誤変換の可能性があります（評価は参考値としてご確認ください）。"
        if quality_note not in (assessment.staff_status or ""):
            assessment.staff_status = f"{quality_note}\n{assessment.staff_status}".strip()
    elif tq == "low":
        quality_note = "※文字起こし品質が低く、評価の信頼性が限定的です。録音または文字起こしを再確認してください。"
        assessment.staff_status = quality_note

    scores = _category_scores(items)
    overall_score = _weighted_overall_score(items)
    label = _overall_label(overall_score)

    if not assessment.staff_status:
        assessment = _fallback_assessment(items, overall_score, staff_name)
    if not good_points:
        good_points = _build_good_points(items)
    if improvement_points and all(item.verdict == "できている" for item in items):
        improvement_points = []
    if not improvement_points:
        improvement_points = _build_improvement_points(items, overall_score)
    improvement_points = improvement_points[: _max_improvement_count()]
    if not next_focus:
        next_focus = _build_next_focus(items)
    staff_feedback = _assemble_staff_feedback(staff_name, good_points, improvement_points)
    pm_review_before = staff_feedback
    pm_review = review_staff_feedback(
        transcript=transcript,
        staff_feedback=staff_feedback,
        good_points=good_points,
        improvement_points=improvement_points,
        item_evaluations=items,
        use_llm=use_llm,
        mode="safe_format" if low_quality else "normal",
    )
    if pm_review.changed:
        logger.info("PMレビューでLINE文面を修正: %s", pm_review.review_notes)
    else:
        logger.info("PMレビュー: %s", pm_review.review_notes)
    staff_feedback = pm_review.reviewed_feedback
    _log_pm_review_debug(
        before=pm_review_before,
        after=staff_feedback,
        notes=pm_review.review_notes,
        changed=pm_review.changed,
    )

    checks = _items_to_checks(items)
    manager_summary = _build_manager_summary(assessment, label)
    overall_comment = assessment.overall_comment or manager_summary

    result = EvaluationResult(
        staff_name=staff_name,
        session_date=session_date,
        source=source,  # type: ignore[arg-type]
        transcript=transcript,
        item_evaluations=items,
        checks=checks,
        scores=scores,
        overall_assessment=assessment,
        overall_score=overall_score,
        overall_label=label,
        manager_summary=manager_summary,
        overall_comment=overall_comment,
        staff_feedback=staff_feedback,
        good_points=good_points,
        improvement_points=improvement_points,
        next_focus=next_focus,
        summary=overall_comment,
        action_plan=next_focus,
    )
    result.line_text = staff_feedback
    return result
