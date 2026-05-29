from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.models import EvaluationResult, ItemEvaluation

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


def _group_by_category(items: list[ItemEvaluation]) -> list[dict]:
    order: list[str] = []
    groups: dict[str, list[ItemEvaluation]] = {}
    for item in items:
        if item.category not in groups:
            order.append(item.category)
            groups[item.category] = []
        groups[item.category].append(item)
    return [{"name": name, "evaluations": groups[name]} for name in order]


def render_report(result: EvaluationResult, report_id: str = "") -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("report.html")
    weakest = min(result.scores, key=lambda s: s.score) if result.scores else None
    return template.render(
        result=result,
        report_id=report_id,
        label_class=LABEL_CLASS.get(result.overall_label, "label-good"),
        verdict_class=VERDICT_CLASS,
        categories=_group_by_category(result.item_evaluations),
        weakest=weakest,
    )
