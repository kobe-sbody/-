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

ROOT = Path(__file__).resolve().parent.parent
CRITERIA_PATH = ROOT / "config" / "evaluation_criteria.json"
MANUAL_PATH = ROOT / "config" / "manual.json"

VERDICT_SCORE = {
    "できている": 100,
    "一部できている": 70,
    "できていない": 35,
    "確認できない": 25,
}

STRICT_ITEM_IDS = {"1-2", "1-3", "1-4", "2-1", "2-2", "3-2", "3-3"}
TONE_ITEM_ID = "3-5"
TONE_PROVISIONAL_NOTE = (
    "※暫定評価：文字起こしからの推測です。"
    "抑揚・明るさは音声解析で評価予定（現時点では参考程度）。"
)

NEXT_ACTION_EXAMPLES = """
next_action は必ず「状況→具体セリフ→狙い」の形式で書くこと。
NG: 悩みを深掘りしましょう
OK: 「二の腕が気になる」と言われたら「たるみ・張り・脂肪感・姿勢由来のどれが気になりますか？」と追加で聞く
"""


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

    if item.id == "1-2":
        has_concern = any(k in quotes_text for k in ["悩み", "気になる", "二の腕", "太もも", "たるみ", "張り", "具体", "詳しく"])
        has_deep = any(k in quotes_text for k in ["具体的", "どの", "いつ", "詳しく", "1個ずつ", "部位", "聞いてい"])
        if item.verdict == "できている" and not (has_concern and has_deep):
            item.verdict = "一部できている" if item.quotes else "確認できない"

    elif item.id == "1-3":
        has_goal = any(k in quotes_text for k in ["目標", "理想", "なりたい", "いつまで", "いつ頃"])
        if item.verdict == "できている" and not has_goal:
            item.verdict = "一部できている" if item.quotes else "確認できない"

    elif item.id == "1-4":
        has_health = any(k in quotes_text for k in ["怪我", "痛", "既往", "病気", "手術", "通院", "健康"])
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
        mono = _count_phrase(transcript, ["なるほどですね", "はい、はい", "はいはい"])
        if mono >= 3 and item.verdict == "できている":
            item.verdict = "できていない"
            item.comment = "単調な相槌が複数回確認されました。"
            item.quotes = _find_quotes(transcript, ["なるほどですね", "はい、はい"], limit=2) or item.quotes

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

        if item.verdict == "できている" and not item.quotes:
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

        if item.verdict == "できている" and not item.quotes:
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
        if item.quotes:
            continue
        rule_item = rule_map.get(item.id)
        if rule_item and rule_item.quotes:
            item.quotes = _sanitize_quotes(rule_item.quotes, transcript)
        if item.quotes:
            continue

        rule = item_rules.get(item.id, {})
        keywords: list[str] = []
        for key in ("positive", "concern", "deep_dive", "follow_up", "clarify", "monotone", "negative"):
            keywords.extend(rule.get(key, []))
        if keywords:
            item.quotes = _find_quotes(transcript, keywords, limit=2)

    return items


def _upgrade_verdict_from_evidence(item: ItemEvaluation, transcript: str) -> None:
    """引用テキスト内の根拠がある場合のみ、過小評価を是正。"""
    if item.id == TONE_ITEM_ID or not item.quotes:
        return

    quotes_text = " ".join(q.text for q in item.quotes)

    if item.id == "1-1":
        if re.search(r"[ぁ-んァ-ン一-龥]{1,8}さん", quotes_text):
            item.verdict = "できている"
            item.comment = "お客様のお名前で呼びかけが確認できました。"

    elif item.id == "1-2":
        concern = any(k in quotes_text for k in ["悩み", "気になる", "二の腕", "太もも", "たるみ", "具体", "詳しく"])
        deep = any(k in quotes_text for k in ["具体的", "どの", "いつ", "詳しく", "1個ずつ", "部位", "聞いてい"])
        if concern and deep and item.verdict in ("確認できない", "一部できている", "できていない"):
            item.verdict = "できている"
            item.comment = "悩みの具体部位と深掘り質問が引用で確認できました。"

    elif item.id == "1-3":
        has_deadline = "いつまで" in quotes_text or "いつ頃" in quotes_text
        has_ideal = any(k in quotes_text for k in ["理想", "なりたい", "目標"])
        if has_deadline and has_ideal:
            item.verdict = "できている"
        elif has_deadline or has_ideal:
            item.verdict = "一部できている"

    elif item.id == "1-4":
        if any(k in quotes_text for k in ["怪我", "既往", "病気", "痛", "健康", "お身体"]):
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

        if not item.quotes and item.verdict == "できている":
            item.verdict = "確認できない"

        if item.quotes and item.verdict == "確認できない":
            item.verdict = "一部できている"

        _upgrade_verdict_from_evidence(item, transcript)

        if item.id in STRICT_ITEM_IDS:
            _strict_adjust_item(item, transcript)

        if item.verdict == "できている" and not item.quotes:
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


def _rule_evaluate_item(transcript: str, category: str, item: dict) -> ItemEvaluation:
    item_id = item["id"]
    label = item["label"]
    rule = item.get("rule", {})
    quotes: list[QuoteItem] = []
    comment = ""
    next_action = ""
    verdict: Verdict = "できていない"

    if item_id == "1-1":
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
        if c_hit and len(d_quotes) >= 2:
            verdict = "できている"
            comment = "悩みの具体部位や背景まで深掘りできています。"
            quotes = d_quotes
        elif c_hit and d_quotes:
            verdict = "一部できている"
            comment = "悩みは聞けていますが、具体化の質問がもう一歩足りない可能性があります。"
            quotes = _find_quotes(transcript, concerns, limit=1) + d_quotes[:1]
            next_action = "「どの部分が特に気になりますか？」「いつ頃からですか？」を追加しましょう。"
        elif c_hit:
            verdict = "一部できている"
            comment = "悩みのヒアリングはありますが、深掘り質問が不足しています。"
            quotes = _find_quotes(transcript, concerns, limit=2)
            next_action = "部位名（二の腕・太もも等）まで具体化する質問を入れましょう。"
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
        if len(hits) >= 2:
            verdict = "できている"
            comment = "既往歴・健康状態の確認ができています。"
        elif hits:
            verdict = "一部できている"
            comment = "健康確認はありますが、もう少し丁寧に確認すると安心感が増します。"
            next_action = "怪我・病気・手術歴を漏れなく確認しましょう。"
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
        varied_count = _count_phrase(transcript, varied)
        if mono_count >= 5 and varied_count < 3:
            verdict = "できていない"
            comment = "「なるほどですね」「はい、はい」等の単調な相槌が目立ちます。"
            quotes = _find_quotes(transcript, mono, limit=2)
            next_action = "相槌の前に一言要約を入れる、質問を挟むなど変化をつけましょう。"
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
    prompt = f"""あなたはピラティススタジオの教育担当マネージャーです。
カウンセリングの文字起こしを、項目ごとに厳密に評価してください。

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
   - 1-2: 悩みの部位/内容 AND 深掘り質問の両方の引用がなければ「できている」不可
   - 1-3: 理想・期限の確認引用がなければ「できている」不可
   - 1-4: 怪我・既往・健康の確認引用がなければ「できている」不可
   - 2-1: 誤解の確認または姿勢・原因の整理の引用がなければ「できている」不可
   - 2-2: 否定表現があれば「できていない」。気づきを促す質問の引用がなければ「できている」不可
   - 3-2: 「なるほどですね」「はい、はい」が目立てば「できていない」または「一部できている」
   - 3-3: 「いいですね！」等の余計な感想があれば「できていない」
4. 3-5（声のトーン）: 最高でも「一部できている」。comment に必ず「文字起こしからの推測・音声未確認」を明記
5. 甘く評価しない。曖昧なら「一部できている」または「確認できない」を選ぶ
6. next_action は具体セリフ例を含める（下記参照）
7. 育成トーンを保ちつつ、課題は明確に

{NEXT_ACTION_EXAMPLES}

【ルールベース参考】
{rule_hints}

【文字起こし】
{transcript[:14000]}

JSON形式:
{{
  "item_evaluations": [
    {{
      "id": "1-1",
      "category": "1. 基本スキル・ヒアリング",
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
  "good_points": [{{"title": "...", "body": "..."}}],
  "improvement_points": [{{"title": "...", "body": "..."}}],
  "next_focus": ["具体行動1", "..."],
  "staff_feedback": "LINE文面（良かった点→伸ばすポイント→次回具体行動。前向きに）"
}}
"""

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": "日本語のみ。JSONのみ返してください。発言引用は文字起こしに存在するものだけ。",
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


def _build_improvement_points(
    items: list[ItemEvaluation], overall_score: int
) -> list[FeedbackSection]:
    weak = [i for i in items if i.verdict == "できていない"]
    unknown = [i for i in items if i.verdict == "確認できない"]
    partial = [i for i in items if i.verdict == "一部できている"]
    ordered = weak + unknown + partial
    sections: list[FeedbackSection] = []
    for item in ordered[:3]:
        sections.append(
            FeedbackSection(
                title=item.label,
                body=f"{item.comment} 次回: {item.next_action}" if item.next_action else item.comment,
                quotes=item.quotes,
            )
        )
    if not sections and overall_score >= 85:
        growth = partial[0] if partial else items[0]
        sections.append(
            FeedbackSection(
                title="さらに伸ばせるポイント",
                body=f"{growth.comment} {growth.next_action}".strip(),
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


def _fallback_staff_feedback(
    staff_name: str,
    good_points: list[FeedbackSection],
    improvement_points: list[FeedbackSection],
    next_focus: list[str],
) -> str:
    name = staff_name if staff_name != "（未入力）" else "担当者"
    lines = [f"{name}さん、今日のカウンセリングお疲れさまでした。", ""]
    if good_points:
        lines.append(f"👍 良かった点")
        lines.append(f"「{good_points[0].title}」がとても良かったです。{good_points[0].body}")
        lines.append("")
    if improvement_points:
        lines.append("🌱 次に伸ばすポイント")
        lines.append(f"{improvement_points[0].title} — {improvement_points[0].body}")
        lines.append("")
    if next_focus:
        lines.append("✅ 次回意識すること")
        lines.append(f"・{next_focus[0]}")
    lines.append("")
    lines.append("一緒に少しずつ磨いていきましょう。分からないことがあればいつでも声をかけてください。")
    return "\n".join(lines)


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
        lines += ["🌱 次に伸ばすポイント"]
        for ip in result.improvement_points:
            lines.append(f"▶ {ip.title}")
            lines.append(f"  {ip.body}")
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
    staff_feedback = ""

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
                FeedbackSection(title=g["title"], body=g["body"])
                for g in llm_data.get("good_points", [])[:3]
                if g.get("title")
            ]
            improvement_points = [
                FeedbackSection(title=g["title"], body=g["body"])
                for g in llm_data.get("improvement_points", [])[:3]
                if g.get("title")
            ]
            next_focus = llm_data.get("next_focus", [])[:3]
            staff_feedback = llm_data.get("staff_feedback", "")

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
    overall_score = round(sum(VERDICT_SCORE[i.verdict] for i in items) / len(items)) if items else 0
    label = _overall_label(overall_score)

    if not assessment.staff_status:
        assessment = _fallback_assessment(items, overall_score, staff_name)
    if not good_points:
        good_points = _build_good_points(items)
    if not improvement_points:
        improvement_points = _build_improvement_points(items, overall_score)
    if not next_focus:
        next_focus = _build_next_focus(items)
    if not staff_feedback:
        staff_feedback = _fallback_staff_feedback(staff_name, good_points, improvement_points, next_focus)

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
    result.line_text = _build_line_text(result)
    return result
