"""Gemini layer for the Course Equivalency eligibility agent.

Two separate calls, neither with tool access:
  1. analyze_syllabi  -- reads BOTH syllabi, extracts topics / learning outcomes / credit
                         hours / assessment structure, and says which items of the requested
                         course are covered by the completed course. Extraction and matching
                         only; the percentages are calculated by Python afterwards.
  2. get_gemini_decision -- receives the verified evidence package and the decision rules and
                         returns APPROVE / REJECT / MANUAL_REVIEW with reasoning.
If Gemini is unreachable the agent returns MANUAL_REVIEW with the evidence attached; it never guesses.
"""

import json
from typing import Literal

from pydantic import BaseModel

from app.config import GEMINI_API_KEY
from app.services.llm_client import generate_with_fallback
from app.tools.match_verifier import verify_matches


class _Assessment(BaseModel):
    exams: float | None = None
    quizzes: float | None = None
    assignments: float | None = None
    projects: float | None = None
    labs: float | None = None
    participation: float | None = None
    other: float | None = None


class _CourseInfo(BaseModel):
    title: str
    credit_hours: int | None = None
    topics: list[str]
    learning_outcomes: list[str]
    assessment: _Assessment


class _Match(BaseModel):
    new_index: int
    old_index: int
    strength: Literal["full", "partial"]
    old_quote: str  # exact text copied from the OLD syllabus that shows the old course covers the item
    new_quote: str  # exact text copied from the NEW syllabus where the item is listed


class _SyllabusAnalysis(BaseModel):
    old: _CourseInfo
    new: _CourseInfo
    topic_matches: list[_Match]
    outcome_matches: list[_Match]


class _Check(BaseModel):
    name: str
    status: Literal["PASS", "FAIL", "N/A"]
    explanation: str


class _Decision(BaseModel):
    recommendation: Literal["APPROVE", "REJECT", "MANUAL_REVIEW"]
    reasoning: str
    checks: list[_Check]
    major_similarities: list[str]
    major_differences: list[str]


_ANALYSIS_RULES = """You compare two university course syllabi for a course equivalency request.
OLD = the course the student already completed. NEW = the course the student wants it to count for.

Rules:
1. Use ONLY the text of the two syllabi. Never invent topics, outcomes, credit hours or percentages.
2. For each course extract: title, credit hours (null if not stated), a list of distinct topics
   (short phrases, weekly schedule items merged into topics), a list of learning outcomes
   (short phrases), and the assessment weights in percent per category (exams, quizzes,
   assignments, projects, labs, participation, other; null when not stated).
3. topic_matches: for every NEW topic that the OLD course covers, give new_index (position in
   NEW topics), old_index (position in OLD topics) and strength: "full" when the OLD topic
   covers it at the same depth, "partial" when it is only touched on or covered at lower depth.
   Do not list NEW topics the OLD course does not cover.
4. outcome_matches: the same, for learning outcomes.
5. Indexes start at 0 and must refer to the lists you return.
6. EVIDENCE: every match must include old_quote and new_quote. Each is a short piece of text (at least
   one full phrase or line, copied CHARACTER FOR CHARACTER from the syllabus text above, never your own
   topic label, never paraphrased or translated; prefer the whole line or sentence of the syllabus) taken from the OLD syllabus (old_quote) and the NEW syllabus (new_quote). The quotes are
   checked by code; a match whose quote cannot be found in the document is discarded. If you cannot
   quote the OLD syllabus for a topic, do not list it as a match.
"""

_DECISION_RULES = """You are the decision layer of a Course Equivalency eligibility agent. You receive a
verified evidence package. Recommend whether the previously completed course (old) can be accepted
as equivalent to the requested course (new). The HOD makes the final decision; you only recommend.

Rules:
1. Use ONLY the evidence package. Never invent grades, topics, numbers or policy.
2. Hard constraints (from the university's course equivalency rules). If any is verified as not
   met, recommend REJECT:
   a. constraints.minimum_grade.meets is false (the completed course needs at least a C+);
   b. constraints.study_plan.in_plan is false (the requested course must be in the study plan of the
      program in study_plan.program_checked; after an approved program change that is the new program);
   c. constraints.credit_hours.old_covers_new is false (the completed course must have credit hours
      equal to or greater than the requested course);
   d. constraints.transfer_limit.within_limit is false (total transferred credits would exceed the limit).
   A study_plan.program_change with result "rejected" means the student stays in the current
   program; it is a reason for the study-plan failure, not a pending matter.
3. If a hard constraint is unverified (its value is null; for the study plan this includes a program
   change that is still pending, explained in study_plan.note), or the old course grade is missing, recommend
   MANUAL_REVIEW and say what could not be verified. Unverified is not a failure.
4. If a syllabus is missing or unreadable (syllabi.*.readable is false), or the similarity score is
   null -> MANUAL_REVIEW and repeat syllabus_request in your reasoning. Never guess content.
5. When all hard constraints are met and a score exists, use overall_similarity_percent against the
   thresholds: at or above equivalent_min -> APPROVE is expected; at or above review_min but below
   equivalent_min -> MANUAL_REVIEW; below review_min -> REJECT. You may move one step toward
   MANUAL_REVIEW (never toward APPROVE) when the evidence shows a serious problem such as a lower
   academic level or large uncovered core topics, and you must say why.
6. If old_course.grade_source is "request", the grade was stated in the request and is not confirmed by
   a transcript; say so in your reasoning.
7. Return checks named: minimum_grade, study_plan, credit_hours, transfer_credit_limit,
   syllabi_available, content_similarity, academic_level. Use status N/A when it does not apply or
   cannot be verified.
8. major_similarities and major_differences: short, specific items taken from the evidence
   (for example uncovered topics from new_topics_not_covered). Use empty lists if there are none.
9. reasoning: 2 to 4 sentences, plain language, mention the similarity percentage when present.
"""


def _generate_with_retry(client, contents, config):
    return generate_with_fallback(client, contents, config)


def _client_and_types():
    from google import genai
    from google.genai import types
    return genai.Client(api_key=GEMINI_API_KEY), types


def analyze_syllabi(old_text: str, new_text: str) -> dict:
    out = {"status": "ok", "error": None, "analysis": None}
    if not GEMINI_API_KEY:
        out.update(status="unavailable", error="GEMINI_API_KEY is not set.")
        return out
    try:
        client, types = _client_and_types()
        config = types.GenerateContentConfig(system_instruction=_ANALYSIS_RULES, response_mime_type="application/json",
                                             response_schema=_SyllabusAnalysis, temperature=0.0)
        prompt = f"=== OLD COURSE SYLLABUS ===\n{old_text}\n\n=== NEW COURSE SYLLABUS ===\n{new_text}"
        resp = _generate_with_retry(client, prompt, config)
        if resp.parsed is None:
            out.update(status="error", error="Gemini returned no parsable syllabus analysis.")
            return out
        out["analysis"] = verify_matches(resp.parsed.model_dump(), old_text, new_text)
    except Exception as exc:  # noqa: BLE001
        out.update(status="error", error=f"{type(exc).__name__}: {exc}")
    return out


def get_gemini_decision(evidence_package: dict) -> dict:
    out = {"status": "ok", "error": None, "recommendation": "MANUAL_REVIEW", "reasoning": "", "checks": [],
           "major_similarities": [], "major_differences": []}
    if not GEMINI_API_KEY:
        out.update(status="unavailable", error="GEMINI_API_KEY is not set.")
        return out
    try:
        client, types = _client_and_types()
        config = types.GenerateContentConfig(system_instruction=_DECISION_RULES, response_mime_type="application/json",
                                             response_schema=_Decision, temperature=0.0)
        resp = _generate_with_retry(client, json.dumps(evidence_package, indent=2, default=str), config)
        p = resp.parsed
        if p is None:
            out.update(status="error", error="Gemini returned no parsable decision.")
            return out
        out.update(recommendation=p.recommendation, reasoning=p.reasoning, checks=[c.model_dump() for c in p.checks],
                   major_similarities=list(p.major_similarities), major_differences=list(p.major_differences))
    except Exception as exc:  # noqa: BLE001
        out.update(status="error", error=f"{type(exc).__name__}: {exc}")
    return out