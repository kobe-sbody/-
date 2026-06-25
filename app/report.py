from __future__ import annotations

from pathlib import Path
import re

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.feedback_history import is_configured as supabase_configured
from app.models import EvaluationResult, ItemEvaluation
from app.pm_review import _primary_concerns_for_prompt

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"

LABEL_CLASS = {
    "優秀": "label-excellent",
    "良好": "label-good",
    "育成中": "label-growing",
    "要サポート": "label-support",
}

VERDICT_CLASS = {
    "できている": "ok",
    "一部できている": "partial",
    "できていない": "ng",
    "確認できない": "unknown",
}

VERDICT_SCORE = {
    "できている": 100,
    "一部できている": 70,
    "できていない": 35,
    "確認できない": 25,
}

RANK_META = {
    "S": {"name": "Sランク", "description": "成約直結項目"},
    "A": {"name": "Aランク", "description": "提案精度を高める項目"},
    "B": {"name": "Bランク", "description": "生活背景・継続条件"},
    "Q": {"name": "接客品質", "description": "安心感・会話品質"},
}


def _item_rank(item_id: str) -> str:
    prefix = item_id.split("-", 1)[0] if "-" in item_id else ""
    return prefix if prefix in RANK_META else "Q"


def _group_by_category(items: list[ItemEvaluation]) -> list[dict]:
    order: list[str] = []
    groups: dict[str, list[ItemEvaluation]] = {}
    for item in items:
        if item.category not in groups:
            order.append(item.category)
            groups[item.category] = []
        groups[item.category].append(item)
    return [{"name": name, "evaluations": groups[name]} for name in order]


def _rank_score(items: list[ItemEvaluation]) -> int:
    if not items:
        return 0
    return round(sum(VERDICT_SCORE[item.verdict] for item in items) / len(items))


def _rank_sections(items: list[ItemEvaluation]) -> list[dict]:
    sections: list[dict] = []
    for rank, meta in RANK_META.items():
        rank_items = [item for item in items if _item_rank(item.id) == rank]
        if not rank_items:
            continue
        sections.append(
            {
                "rank": rank,
                "name": meta["name"],
                "description": meta["description"],
                "score": _rank_score(rank_items),
                "ok_count": sum(1 for item in rank_items if item.verdict == "できている"),
                "total_count": len(rank_items),
                "evaluations": rank_items,
            }
        )
    return sections


def _extract_question_examples(text: str, limit: int = 3) -> list[str]:
    examples = [match.strip() for match in re.findall(r"「([^」]+)」", text) if match.strip()]
    return examples[:limit]


def _shorten_text(text: str, *, max_chars: int = 120) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip("。、 ") + "。"


def _concern_phrase(primary_concerns: list[str]) -> str:
    return "や".join(primary_concerns[:2]) if primary_concerns else "主訴"


def _action_for_issue(title: str, body: str, primary_concerns: list[str]) -> str:
    concerns = _concern_phrase(primary_concerns)
    if "取り組み結果" in title or "過去の取り組み結果" in body:
        return f"過去の取り組み結果を、{concerns}と紐付けて確認する"
    if "体重以外" in title or "体重以外" in body:
        return f"{concerns}について、体重以外の可能性に気づける質問を挟む"
    if "理想" in title or "理想" in body:
        return f"{concerns}の理想状態を具体的な言葉で確認する"
    return _shorten_text(title or body, max_chars=48)


def _why_for_issue(title: str, body: str, primary_concerns: list[str]) -> str:
    concerns = _concern_phrase(primary_concerns)
    if "取り組み結果" in title or "過去の取り組み結果" in body:
        return (
            f"過去の取り組みで何が変わり、{concerns}がどう残ったのかを確認できると、"
            "姿勢診断への導線が自然になります。"
        )
    if "体重以外" in title or "体重以外" in body:
        return (
            f"{concerns}の原因を決めつけずに一緒に整理できると、"
            "お客様が姿勢診断を受ける意味を理解しやすくなります。"
        )
    return _shorten_text(body, max_chars=120)


def _next_steps_for_issue(title: str, body: str, primary_concerns: list[str]) -> list[str]:
    concerns = _concern_phrase(primary_concerns)
    if "取り組み結果" in title or "過去の取り組み結果" in body:
        return [
            "過去に試した運動やケアを確認する",
            f"その結果、{concerns}が変わったかを聞く",
            "変わった部分と変わらなかった部分を整理する",
        ]
    if "体重以外" in title or "体重以外" in body:
        return [
            f"{concerns}が気になる場面を確認する",
            "体重が変わる前後で同じ悩みがあったかを聞く",
            "姿勢診断で一緒に確認する流れにつなげる",
        ]
    return [_shorten_text(body, max_chars=80)]


def _primary_issue(result: EvaluationResult, primary_concerns: list[str]) -> dict:
    section = result.improvement_points[0] if result.improvement_points else None
    if not section:
        return {
            "title": result.overall_assessment.top_issue or "特になし",
            "action": result.overall_assessment.priority_improvement or "大きな改善課題はありません。",
            "why": "現在の良い点を継続しながら、次回も主訴の確認を丁寧に進めましょう。",
            "next_steps": [],
            "questions": [],
        }
    action = _action_for_issue(section.title, section.body, primary_concerns)
    return {
        "title": section.title,
        "action": action,
        "why": _why_for_issue(section.title, section.body, primary_concerns),
        "next_steps": _next_steps_for_issue(section.title, section.body, primary_concerns),
        "questions": _extract_question_examples(section.body),
    }


def _supplemental_improvements(items: list[ItemEvaluation]) -> list[dict]:
    b_items = [
        item
        for item in items
        if _item_rank(item.id) == "B" and item.verdict != "できている"
    ]
    return [
        {
            "title": item.label,
            "verdict": item.verdict,
            "action": item.next_action or item.comment,
        }
        for item in b_items[:3]
    ]


def _score_cards(result: EvaluationResult, rank_sections: list[dict]) -> list[dict]:
    cards = [
        {
            "label": "総合スコア",
            "score": result.overall_score,
            "note": result.overall_label,
            "kind": "overall",
        }
    ]
    for section in rank_sections:
        cards.append(
            {
                "label": section["name"],
                "score": section["score"],
                "note": f"{section['ok_count']}/{section['total_count']}項目 達成",
                "kind": section["rank"].lower(),
            }
        )
    return cards


def _brief_summary(result: EvaluationResult, primary_issue: dict) -> str:
    good = result.good_points[0].title if result.good_points else "主訴や理想の確認"
    action = primary_issue.get("action") or "次回の深掘り"
    return (
        f"{good}はできています。一方で、{action}ことで、"
        "姿勢診断への導線がさらに自然になります。"
    )


def render_report(result: EvaluationResult, report_id: str = "") -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html")
    weakest = min(result.scores, key=lambda s: s.score) if result.scores else None
    rank_sections = _rank_sections(result.item_evaluations)
    primary_concerns = _primary_concerns_for_prompt(result.transcript)
    primary_issue = _primary_issue(result, primary_concerns)
    return template.render(
        result=result,
        report_id=report_id,
        label_class=LABEL_CLASS.get(result.overall_label, "label-good"),
        verdict_class=VERDICT_CLASS,
        categories=_group_by_category(result.item_evaluations),
        rank_sections=rank_sections,
        score_cards=_score_cards(result, rank_sections),
        primary_concerns=primary_concerns,
        primary_issue=primary_issue,
        brief_summary=_brief_summary(result, primary_issue),
        supplemental_improvements=_supplemental_improvements(result.item_evaluations),
        weakest=weakest,
        active_nav="upload",
        history_enabled=supabase_configured(),
    )
