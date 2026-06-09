from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["できている", "一部できている", "できていない", "確認できない"]


class CheckItem(BaseModel):
    id: str
    category: str
    label: str
    passed: bool
    evidence: str = ""
    suggestion: str = ""


class ScoreItem(BaseModel):
    name: str
    score: int
    comment: str = ""


class QuoteItem(BaseModel):
    timestamp: str
    text: str
    context: str = ""


class ItemEvaluation(BaseModel):
    id: str
    category: str
    label: str
    verdict: Verdict
    quotes: list[QuoteItem] = Field(default_factory=list)
    comment: str = ""
    next_action: str = ""


class OverallAssessment(BaseModel):
    staff_status: str = ""
    top_issue: str = ""
    priority_improvement: str = ""
    overall_comment: str = ""


class FeedbackSection(BaseModel):
    title: str
    body: str
    quotes: list[QuoteItem] = Field(default_factory=list)


class FeedbackHistoryItem(BaseModel):
    id: str
    created_at: str
    staff_name: str


class FeedbackHistoryDetail(BaseModel):
    id: str
    created_at: str
    staff_name: str
    audio_file_name: str = ""
    transcript: str = ""
    feedback: str = ""


class StaffHistoryCount(BaseModel):
    staff_name: str
    count: int


class FeedbackHistoryStats(BaseModel):
    total: int
    staff_counts: list[StaffHistoryCount] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    staff_name: str = "（未入力）"
    session_date: str = "（未入力）"
    source: Literal["demo", "transcript", "audio"] = "demo"
    transcript: str
    item_evaluations: list[ItemEvaluation] = Field(default_factory=list)
    checks: list[CheckItem] = Field(default_factory=list)
    scores: list[ScoreItem] = Field(default_factory=list)
    overall_assessment: OverallAssessment = Field(default_factory=OverallAssessment)
    overall_score: int = 0
    overall_label: str = "良好"
    manager_summary: str = ""
    overall_comment: str = ""
    staff_feedback: str = ""
    good_points: list[FeedbackSection] = Field(default_factory=list)
    improvement_points: list[FeedbackSection] = Field(default_factory=list)
    next_focus: list[str] = Field(default_factory=list)
    line_text: str = ""
    summary: str = ""
    action_plan: list[str] = Field(default_factory=list)
