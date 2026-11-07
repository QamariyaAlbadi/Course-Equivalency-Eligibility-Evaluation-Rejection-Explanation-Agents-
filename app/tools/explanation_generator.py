"""Rejection Explanation Generator: validates Gemini's suggested reasons and builds the
template fallback. Every reason has to use a known keyword and may not contain numbers that
are not in the evidence."""

import re

_NUM = re.compile(r"\d+(?:\.\d+)?")

_TEMPLATES = {
    "old_course_not_passed": "The course you completed ({course}) does not have a passing grade ({grade}), so it cannot be accepted as equivalent.",
    "grade_below_minimum": "Your grade in the completed course ({grade}) is below the minimum grade of {required_grade} required for equivalency.",
    "course_not_in_study_plan": "The requested course ({course}) is not part of your study plan, so an equivalency cannot be granted for it.",
    "program_change_rejected": "Your program change to {program} was not approved, so you remain in your current program and the requested course is not part of its study plan.",
    "transfer_credit_limit_exceeded": "Approving this request would bring your transferred credit hours to {total_after}, which is above the maximum of {limit}.",
    "similarity_below_threshold": "The syllabus comparison shows {similarity_percent}% similarity between the two courses, which is below the {required_percent}% required for equivalency.",
    "credit_hours_lower": "The course you completed has {completed_credit_hours} credit hours, which is fewer than the {requested_credit_hours} credit hours of the requested course.",
    "level_mismatch": "The course you completed is at the {completed_level}-level, which is lower than the {requested_level}-level of the requested course.",
    "missing_course_topics": "Important topics of the requested course are not covered by the course you completed, including: {topics}.",
}
_JUDGED = {
    "learning_outcomes_gap": "The learning outcomes of the requested course are not fully met by the course you completed.",
    "assessment_structure_differs": "The assessment structure of the two courses differs significantly.",
    "different_course_focus": "The two courses have a different focus.",
    "insufficient_syllabus_detail": "The syllabus does not give enough detail to support equivalency.",
}


def _sentence(key: str, facts: dict) -> str:
    f = dict(facts)
    if "topics" in f:
        f["topics"] = "; ".join(f["topics"])
    return _TEMPLATES[key].format(**f)


def allowed_numbers(context_text: str) -> set[str]:
    return set(_NUM.findall(context_text))


def validate_reasons(reasons: list[dict], valid_keywords: set[str], allowed: set[str]) -> tuple[list[dict], list[str]]:
    kept, dropped = [], []
    for r in reasons:
        kws = [k for k in r.get("addresses_keywords", []) if k in valid_keywords]
        text = (r.get("text") or "").strip()
        if not text or not kws:
            dropped.append("reason without a valid keyword was removed")
            continue
        bad = [n for n in _NUM.findall(text) if n not in allowed]
        if bad:
            dropped.append(f"reason with unverified number(s) {bad} was removed")
            continue
        kept.append({"tone": r.get("tone", "formal"), "text": text, "addresses_keywords": kws})
    return kept, dropped


def template_reasons(verified: dict, judged: list[str]) -> list[dict]:
    parts = [(k, _sentence(k, v)) for k, v in verified.items()] + [(k, _JUDGED[k]) for k in judged if k in _JUDGED]
    if not parts:
        return []
    keys = [k for k, _ in parts]
    body = " ".join(s for _, s in parts)
    first = parts[0][1]
    return [
        {"tone": "formal", "text": f"After reviewing your Course Equivalency request, it cannot be approved. {body}", "addresses_keywords": keys},
        {"tone": "concise", "text": first, "addresses_keywords": [parts[0][0]]},
        {"tone": "supportive", "text": f"Thank you for your request. Unfortunately, it could not be approved at this time. {body} You are welcome to speak with your academic advisor about other options.", "addresses_keywords": keys},
    ]