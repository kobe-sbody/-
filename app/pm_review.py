from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from app.logger import logger
from app.models import FeedbackSection, ItemEvaluation

ROOT = Path(__file__).resolve().parent.parent
PM_RULES_PATH = ROOT / "config" / "pm_rules.md"

REQUIRED_OPENING = "お疲れ様です。\n録音データありがとうございます！"
PREFERRED_CLOSING = "引き続きよろしくお願いします！"
ALLOWED_CLOSINGS = (PREFERRED_CLOSING, "ご確認よろしくお願いします！")


@dataclass
class PMReviewResult:
    reviewed_feedback: str
    review_notes: str
    changed: bool


def load_pm_rules() -> str:
    return PM_RULES_PATH.read_text(encoding="utf-8")


def _safe_model_dump(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    return {}


def _sections_for_prompt(sections: Sequence[FeedbackSection]) -> list[dict[str, Any]]:
    return [
        {
            "title": section.title,
            "body": section.body,
            "quotes": [quote.model_dump() for quote in section.quotes[:2]],
        }
        for section in sections[:3]
    ]


def _items_for_prompt(items: Sequence[ItemEvaluation]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.id,
            "category": item.category,
            "label": item.label,
            "verdict": item.verdict,
            "comment": item.comment,
            "next_action": item.next_action,
            "quotes": [quote.model_dump() for quote in item.quotes[:2]],
        }
        for item in items[:12]
    ]


def _primary_concerns_for_prompt(transcript: str) -> list[str]:
    """お客様の主訴候補を抽出する。

    スタッフが選択肢として出しただけの部位より、お客様の回答・肯定に近い部位を優先する。
    """
    aliases = {
        "ウエスト": ["ウエスト", "くびれ", "下っ腹", "下腹", "お腹"],
        "内もも": ["内もも", "内腿", "うちもも", "内側"],
        "外もも": ["外もも", "外腿", "外側"],
        "前もも": ["前もも", "前腿"],
        "二の腕": ["二の腕"],
        "ヒップ": ["ヒップ", "お尻", "おしり"],
        "ふくらはぎ": ["ふくらはぎ"],
        "太もも": ["太もも"],
    }
    supplemental = ["腰痛", "腰", "反り腰", "すり腰", "スリゴ"]
    answer_markers = [
        "気になる",
        "気にな",
        "厚み",
        "張り",
        "太く",
        "細く",
        "垂れ",
        "出て",
        "すっきり",
        "キュッ",
        "変わ",
        "ずっと",
        "同じ",
        "嫌",
    ]
    option_markers = [
        "だったら",
        "の方から",
        "になるんですけど",
        "チェック",
        "部位ごと",
        "例えば",
        "まず",
        "最後",
        "どうなって欲しい",
    ]

    def normalize_line(raw: str) -> str:
        return re.sub(r"^\[\d{2}:\d{2}\]\s*", "", raw.strip())

    lines = [normalize_line(line) for line in transcript.splitlines() if normalize_line(line)]
    scores: Counter[str] = Counter()

    def has_thigh_option_context(text: str) -> bool:
        return (
            any(term in text for term in ["太もも", "もも", "下半身", "脚", "足"])
            and sum(1 for term in ["内もも", "外もも", "前もも", "内側", "外側", "前側"] if term in text) >= 2
        )

    def short_thigh_answer(text: str) -> str | None:
        compact = re.sub(r"[\s、。！？?ですねですます]+", "", text)
        if compact in {"内", "内側", "内もも", "うちもも", "内腿"}:
            return "内もも"
        if compact in {"外", "外側", "外もも", "外腿"}:
            return "外もも"
        if compact in {"前", "前側", "前もも", "前腿"}:
            return "前もも"
        return None

    # 「内もも・外もも・前もも」の選択肢提示直後の短い回答を、お客様の主訴として強く扱う。
    for idx, text in enumerate(lines):
        if not has_thigh_option_context(text):
            continue
        for follow in lines[idx + 1 : idx + 4]:
            answer = short_thigh_answer(follow)
            if answer:
                scores[answer] += 10
                continue
            # スタッフが回答を復唱して肯定確認した場合も主訴として加点する。
            if "内もも" in follow and any(marker in follow for marker in ["ですね", "です", "気になる"]):
                scores["内もも"] += 6
            if "外もも" in follow and any(marker in follow for marker in ["ですね", "です", "気になる"]):
                scores["外もも"] += 6
            if "前もも" in follow and any(marker in follow for marker in ["ですね", "です", "気になる"]):
                scores["前もも"] += 6

    for idx, text in enumerate(lines):
        if any(term in text for term in supplemental):
            continue

        is_question_or_option = "?" in text or "？" in text or any(marker in text for marker in option_markers)
        is_answer_like = any(marker in text for marker in answer_markers)
        following = " ".join(lines[idx + 1 : idx + 4])
        matched_concerns: list[tuple[str, list[str]]] = []

        for concern, terms in aliases.items():
            matched = any(term in text for term in terms)
            # 「内」単独は誤検出しやすいので、下半身/もも文脈でだけ内ももに寄せる。
            if concern == "内もも" and not matched:
                matched = bool(re.search(r"内(側)?", text)) and any(ctx in text for ctx in ["もも", "太もも", "脚", "足", "下半身"])
            if matched:
                matched_concerns.append((concern, terms))

        multiple_options = len(matched_concerns) >= 2 and is_question_or_option
        for concern, terms in matched_concerns:
            if multiple_options:
                scores[concern] += 1
            elif is_answer_like and not is_question_or_option:
                scores[concern] += 6
            elif is_answer_like:
                scores[concern] += 3
            else:
                scores[concern] += 1

            # スタッフが部位を確認した直後、お客様の回答らしい行が続けば主訴として加点する。
            following_mentions_same_concern = any(term in following for term in terms)
            if concern == "内もも" and not following_mentions_same_concern:
                following_mentions_same_concern = bool(re.search(r"内(側)?", following)) and any(
                    ctx in f"{text} {following}" for ctx in ["もも", "太もも", "脚", "足", "下半身"]
                )
            if (
                is_question_or_option
                and any(marker in following for marker in answer_markers)
                and (not multiple_options or following_mentions_same_concern)
            ):
                scores[concern] += 4

    # 「内」「内側」が回答行に単独で出るケースを、直前の下半身/もも文脈から内ももとして拾う。
    for idx, text in enumerate(lines):
        if has_thigh_option_context(text):
            continue
        if not re.search(r"内(側)?", text):
            continue
        context = " ".join(lines[max(0, idx - 2) : idx + 2])
        if any(ctx in context for ctx in ["もも", "太もも", "脚", "足", "下半身"]):
            scores["内もも"] += 6 if any(marker in text for marker in answer_markers) else 3

    for term in ("腰痛", "腰"):
        scores.pop(term, None)

    return [term for term, score in scores.most_common(5) if score >= 3]


def _is_valid_reviewed_feedback(text: str) -> bool:
    if not text.strip():
        return False
    if not text.startswith(REQUIRED_OPENING):
        return False
    if not text.rstrip().endswith(ALLOWED_CLOSINGS):
        return False
    # LINEで送る前提なので、異常に長い出力は採用しない。
    return len(text) <= 2200


def _is_valid_safe_format(original: str, reviewed: str) -> bool:
    """低品質時は注意文を残し、内容を過度に増やさない。"""
    required_warning = "録音または文字起こしを再確認してください"
    if required_warning in original and required_warning not in reviewed:
        return False
    # safe_format は整形だけなので、大幅な加筆は不採用。
    return len(reviewed) <= max(len(original) + 250, int(len(original) * 1.25))


def _fallback(staff_feedback: str, note: str) -> PMReviewResult:
    return PMReviewResult(
        reviewed_feedback=staff_feedback,
        review_notes=note,
        changed=False,
    )


def review_staff_feedback(
    *,
    transcript: str,
    staff_feedback: str,
    good_points: Sequence[FeedbackSection],
    improvement_points: Sequence[FeedbackSection],
    supplemental_points: Sequence[FeedbackSection] = (),
    evaluation_result: Any = None,
    item_evaluations: Sequence[ItemEvaluation] = (),
    use_llm: bool = True,
    mode: str = "normal",
) -> PMReviewResult:
    """スタッフ向けLINE文面をPM目線で最終レビューする。

    失敗時は必ず元の staff_feedback を返し、評価処理全体を止めない。
    """
    if not use_llm:
        return _fallback(staff_feedback, "PMレビュー未実行（LLM無効）")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return _fallback(staff_feedback, "PMレビュー未実行（OPENAI_API_KEY未設定）")

    try:
        from openai import OpenAI

        logger.info("PMレビュー開始 mode=%s model=%s", mode, os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
        pm_rules = load_pm_rules()
        evaluation_payload = _safe_model_dump(evaluation_result)
        if not evaluation_payload and item_evaluations:
            evaluation_payload = {"item_evaluations": _items_for_prompt(item_evaluations)}
        primary_concerns = _primary_concerns_for_prompt(transcript)

        mode_instruction = (
            "通常PMレビューです。内容の正確性を守りながら、育成効果・読みやすさ・文体を改善してください。"
            "良かった点は接客行動として選び直し、改善点は「最優先で改善したいこと」と「さらに良くするなら」の2階層に整えてください。"
            "最優先はSランク未達があればS、Sが達成済みならA、S/Aが達成済みならBの順で選んでください。"
            "改善点は評価項目単位ではなく、お客様の主訴を中心に組み立ててください。"
            "「何を聞けなかったか」ではなく「主訴に対して次に何を聞けば提案につながるか」を優先してください。"
            "Bランク未確認はS/Aの主課題がある場合、主改善ではなく補足の温度感にしてください。"
            if mode != "safe_format"
            else (
                "低品質文字起こし用のsafe_formatモードです。内容を増やさず、文章の整形だけ行ってください。"
                "悩み・改善点・提案内容・会話例を新しく追加してはいけません。"
                "「録音または文字起こしを再確認してください」という注意文は必ず残してください。"
                "LINEで送っても違和感のない自然な文章に整えることだけが目的です。"
            )
        )

        prompt = f"""以下はStudio Coachのスタッフ向けLINE文面です。
PMチェックルールに従って、必要な場合だけ文面を修正してください。

【PMチェックルール】
{pm_rules}

【今回のPMレビューモード】
{mode_instruction}

【通常時の理想構成】
お疲れ様です。
録音データありがとうございます！

今回良かった点は2つです。

1つ目は、〇〇です。
具体的には、〜〜ができていました。
これは安心感や話しやすさにつながる大事なポイントです。

2つ目は、〇〇です。
〜〜のように会話を自然に広げられていた点が良かったです。

最優先で改善したいことです。

〇〇です。
理由：
〜〜

次回の聞き方：
「〜〜ですか？」
「〜〜の時に気になりますか？」
「理想はどんな状態ですか？」

さらに良くするなら、強いて言うと以下も確認できると良いです。
ただし今回の録音では大きな問題ではありません。

・通いやすい時間帯を確認する
・仕事や生活背景を確認する

全体的には、〇〇はできているので、次回は△△を意識できるとさらに良くなると思います。
引き続きよろしくお願いします！

【今回の重要方針】
- 改善点は評価項目名をそのまま並べず、お客様の主訴を中心に書く
- 改善点は「最優先で改善したいこと」と「さらに良くするなら」の2階層にする
- 最優先で改善したいことは最大1〜2個に絞る
- さらに良くするならは0〜3個までにし、Bランク未確認を強く言いすぎない
- Sランク未達がある場合はSランク中心、Sランク達成済みならAランク、S/Aランク達成済みならBランクを見る
- Bランク項目は「ここを直してください」ではなく「強いて言うなら、ここも確認できるとさらに良くなります」の温度感にする
- ウエスト・内もも・二の腕・ヒップなど主訴がある場合は、その主訴に対して次に聞く質問を提案する
- 腰痛など補足情報は、主訴より優先して改善点にしない
- Bランクの未確認（通う時間帯・仕事・睡眠など）は、レポートでは扱ってよいが、LINEでは主訴深掘りより強く出さない
- 「何を聞けなかったか」ではなく、「主訴に対して次に何を聞けば提案につながるか」を書く
- 姿勢診断前に原因を断定しない
- 「説明します」ではなく、「詳しく見させていただきます」「一緒に確認してみましょう」を推奨する
- 例: 「体重以外にも原因があるかもしれませんね。後ほど姿勢を詳しく見させていただきながら、一緒に確認してみましょう」
- 改善点が「過去の取り組みを確認すること」だけで終わっていて、主訴名が入っていない場合は修正する
- 質問例には、主訴名を入れる。例: 「その取り組みで、ウエストや内ももは変わりましたか？」
- 主訴候補がある場合、改善点1の「次回の聞き方」に主訴候補の1番目を必ず入れる
- 「姿勢のお悩み」「体型のお悩み」のような抽象表現だけで終わらせず、ウエスト・内もも等の具体部位名に置き換える
- 今回の主訴候補に腰・腰痛が含まれても、それは補足情報として扱い、主訴より優先しない

【必ず直す表現】
- 「良かったと思いますと感じました」→「良かったです」「良いと思います」
- 「お客様の名前を確認し」など不自然な途中切れ
- 同じ文の中で「お客様」を何度も繰り返す表現
- 評価項目名をそのまま褒める表現（例: 「お客様の名前を呼ぶはしっかりできていて」）
- 改善点が長い1段落で続く文章
- 「原因です」「説明します」など、診断前に断定している表現

【文字起こし（根拠。ここにない内容は追加しない）】
{transcript[:9000]}

【主訴候補（自動抽出。改善点ではこの中の上位1〜2個を優先）】
{json.dumps(primary_concerns, ensure_ascii=False)}

【良かった点】
{json.dumps(_sections_for_prompt(good_points), ensure_ascii=False)}

【改善点】
{json.dumps(_sections_for_prompt(improvement_points), ensure_ascii=False)}

【さらに良くするなら（補足扱い。強く言いすぎない）】
{json.dumps(_sections_for_prompt(supplemental_points), ensure_ascii=False)}

【評価データ】
{json.dumps(evaluation_payload, ensure_ascii=False)[:6000]}

【現在のLINE文面】
{staff_feedback}

出力条件:
- JSONのみ返す
- reviewed_feedback はLINEでそのまま送れる完成文にする
- 冒頭と締めは固定フォーマットを必ず維持する
- 通常時の締めはできるだけ「引き続きよろしくお願いします！」にする
- スタッフ名は入れない
- 音声・文字起こしにない内容は追加しない
- 抽象論、精神論、根性論を避ける
- 修正不要なら reviewed_feedback は元文面と同じでよい
- safe_formatモードでは内容を増やさず、文体・重複・長さだけを整える
- 通常時は「今回良かった点は2つです。」「最優先で改善したいことです。」を基本構成にする
- 通常時は最優先の改善点に「理由：」「次回の聞き方：」を入れる
- 補足がある場合だけ「さらに良くするなら」を追加する
- 「さらに良くするなら」はBランク未確認を補足として扱い、「今回の録音では大きな問題ではありません」の温度感を入れる
- 次回の聞き方には、実際に使える質問例を2〜3個入れる
- 良かった点は、評価データだけでなく文字起こし上の接客行動（相槌、受け止め、会話を広げる、深掘り）から選ぶ
- ただし、文字起こしにない良かった点は作らない
- 改善点はS-01/S-03のような最重要課題でも、本文では「主訴に対する次の質問」に翻訳する
- S/Aランクに未達がある場合、Bランクの未確認をLINEの主改善点にしない
- 通常時の改善点には、主訴名（例: ウエスト、内もも等）を少なくとも1回入れる
- 通常時の改善点1には、主訴候補の1番目を必ず入れる
- 主訴候補が2つ以上ある場合は、次回の聞き方に上位2つを両方入れる
- 例: 主訴候補が「ウエスト」「内もも」なら、「その取り組みでウエストや内ももは変わりましたか？」のように書く
- 「過去の取り組み」や「取り組み結果」は、必ず「その結果、主訴が変わったか/変わらなかったか」の質問に変換する
- 「姿勢のお悩み」は抽象的なので、主訴候補がある場合は使わない

JSON形式:
{{
  "reviewed_feedback": "最終LINE文面",
  "review_notes": "修正した理由。修正不要ならその理由",
  "changed": true
}}
"""

        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "あなたはStudio Coachの品質管理責任者です。"
                        "目的は評価ではなくスタッフ育成です。"
                        "JSONのみ返してください。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        reviewed_feedback = str(data.get("reviewed_feedback", "")).strip()
        review_notes = str(data.get("review_notes", "")).strip()
        changed = bool(data.get("changed", False))

        if not _is_valid_reviewed_feedback(reviewed_feedback):
            logger.warning("PMレビュー出力を不採用: 固定フォーマット不一致または長すぎ")
            return _fallback(staff_feedback, "PMレビュー出力不採用（形式不一致）")
        if mode == "safe_format" and not _is_valid_safe_format(staff_feedback, reviewed_feedback):
            logger.warning("PMレビュー出力を不採用: safe_formatで注意文欠落または加筆過多")
            return _fallback(staff_feedback, "PMレビュー出力不採用（safe_format制約違反）")

        return PMReviewResult(
            reviewed_feedback=reviewed_feedback,
            review_notes=review_notes or "PMレビュー完了",
            changed=changed and reviewed_feedback != staff_feedback,
        )
    except Exception as exc:
        logger.error("PMレビュー失敗: %s", exc)
        return _fallback(staff_feedback, f"PMレビュー失敗: {exc}")
