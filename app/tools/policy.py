"""Loads the editable equivalency policy and the approved grade policy."""

import json
from pathlib import Path

from app.exceptions import KnowledgeBaseError

_KB = Path(__file__).resolve().parent.parent / "knowledge_base"
_cache: dict[str, dict] = {}


def _load(name: str) -> dict:
    if name in _cache:
        return _cache[name]
    path = _KB / name
    if not path.exists():
        raise KnowledgeBaseError(f"Knowledge base file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise KnowledgeBaseError(f"{name} is malformed JSON: {exc}") from exc
    _cache[name] = data
    return data


def load_equivalency_policy() -> dict:
    p = _load("equivalency_policy.json")
    t = p.get("similarity_thresholds") or {}
    if not isinstance(t.get("equivalent_min"), (int, float)) or not isinstance(t.get("review_min"), (int, float)):
        raise KnowledgeBaseError("equivalency_policy.json must define similarity_thresholds.equivalent_min and review_min.")
    if t["review_min"] > t["equivalent_min"]:
        raise KnowledgeBaseError("review_min cannot be above equivalent_min.")
    if not isinstance(p.get("weights"), dict) or not p["weights"]:
        raise KnowledgeBaseError("equivalency_policy.json must define weights.")
    return p


def load_grade_policy() -> dict:
    data = _load("grade_policy.json")
    if not isinstance(data.get("passing_grades"), list) or not isinstance(data.get("failing_grades"), list):
        raise KnowledgeBaseError("Grade policy file must define 'passing_grades' and 'failing_grades' lists.")
    return data


def classify_grade(grade: str | None) -> bool | None:
    """True = passing, False = failing or not completed (NULL), None = grade not covered by policy."""
    if grade is None:
        return False
    policy = load_grade_policy()
    g = str(grade).strip().upper()
    if g in {x.upper() for x in policy["passing_grades"]}:
        return True
    if g in {x.upper() for x in policy["failing_grades"]}:
        return False
    return None


def meets_minimum_grade(grade: str | None) -> bool | None:
    """Equivalency needs at least the minimum grade (C+). True/False, or None when the grade is
    missing or not covered by the policy (unverified, never guessed)."""
    p = load_grade_policy()
    if grade is None or not str(grade).strip():
        return None
    g = str(grade).strip().upper()
    order = [x.upper() for x in p["grade_order"]]
    if g in order:
        return order.index(g) <= order.index(p["equivalency_minimum_grade"].upper())
    if g in {x.upper() for x in p["failing_grades"]}:
        return False
    return None
