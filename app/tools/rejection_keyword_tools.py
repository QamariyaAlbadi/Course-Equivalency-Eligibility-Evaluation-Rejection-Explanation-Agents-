"""Rejection keywords. Verified keywords come straight from the eligibility agent's
code-calculated evidence; judged keywords are chosen by Gemini from a closed list."""

import json
from pathlib import Path

from app.exceptions import KnowledgeBaseError

_PATH = Path(__file__).resolve().parent.parent / "knowledge_base" / "rejection_keywords.json"


def load_keywords() -> dict:
    try:
        data = json.loads(_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise KnowledgeBaseError(f"Could not load rejection_keywords.json: {exc}") from exc
    if not isinstance(data.get("verified"), dict) or not isinstance(data.get("judged"), dict):
        raise KnowledgeBaseError("rejection_keywords.json must define 'verified' and 'judged'.")
    return data


def verified_facts(agent8: dict) -> dict:
    """Keyword id -> supporting facts, from code-calculated evidence only."""
    ev = agent8["evidence"]
    old, new, sim = ev["old_course"], ev["new_course"], ev["similarity"]
    found: dict[str, dict] = {}
    cn = ev["constraints"]
    if old["passed"] is False:
        found["old_course_not_passed"] = {"grade": old["grade"], "course": old["name"] or old["course_id"]}
    elif cn["minimum_grade"]["meets"] is False:
        found["grade_below_minimum"] = {"grade": cn["minimum_grade"]["grade"], "required_grade": cn["minimum_grade"]["required"]}
    if cn["study_plan"]["in_plan"] is False:
        found["course_not_in_study_plan"] = {"course": new["name"] or new["course_id"]}
        pcg = cn["study_plan"].get("program_change")
        if pcg and pcg.get("result") == "rejected":
            found["program_change_rejected"] = {"program": pcg["new_program_name"]}
    tl = cn["transfer_limit"]
    if tl["within_limit"] is False:
        found["transfer_credit_limit_exceeded"] = {"total_after": tl["total_after"], "limit": tl["cap_credit_hours"]}
    if sim and sim["score_band"] == "BELOW_RANGE":
        found["similarity_below_threshold"] = {"similarity_percent": sim["overall_similarity_percent"],
                                               "required_percent": sim["thresholds"]["review_min"]}
    elif sim and sim["score_band"] == "REVIEW_RANGE":
        found["similarity_below_threshold"] = {"similarity_percent": sim["overall_similarity_percent"],
                                               "required_percent": sim["thresholds"]["equivalent_min"]}
    if cn["credit_hours"]["old_covers_new"] is False:
        found["credit_hours_lower"] = {"completed_credit_hours": cn["credit_hours"]["old"],
                                       "requested_credit_hours": cn["credit_hours"]["new"]}
    lvl = ev["academic_level"]
    if lvl["old_at_least_new"] is False:
        found["level_mismatch"] = {"completed_level": lvl["old_level"] * 100, "requested_level": lvl["new_level"] * 100}
    if sim and sim["new_topics_not_covered"]:
        found["missing_course_topics"] = {"topics": sim["new_topics_not_covered"][:8], "count": len(sim["new_topics_not_covered"])}
    return found