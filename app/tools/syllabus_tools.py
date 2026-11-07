"""Syllabus Retrieval and Syllabus Comparison tools.

Retrieval finds the two syllabi uploaded for a request (Supabase bucket, or a local folder
for testing) and reads them with the Document Reader. Comparison turns Gemini's structured
topic/outcome matching into numbers: Gemini only says which items match; the percentages
and the overall similarity score are calculated here, with the weights in
app/knowledge_base/equivalency_policy.json.

Direction: the completed (old) course must cover the requested (new) course, so coverage is
measured over the NEW course's topics and learning outcomes.
"""

import math
import re
from collections import Counter
from pathlib import Path

from app.exceptions import AgentError, DocumentReadError
from app.services import storage
from app.tools.document_reader import SUPPORTED, read_document

_SAMPLES = Path(__file__).resolve().parent.parent.parent / "samples" / "syllabi"


def _empty(side: str) -> dict:
    return {"side": side, "available": False, "readable": False, "source": None, "filename": None, "files": [], "file_count": 0, "text": None, "error": None}


def _find_side(names: list[str], side: str) -> list[str]:
    """All files uploaded for one side (old or new), in name order."""
    found = []
    for n in sorted(names):
        base = n.rsplit("/", 1)[-1].lower()
        if base.startswith(f"{side}_course") or (base.startswith(side) and base.rsplit(".", 1)[-1] in SUPPORTED):
            found.append(n)
    return found


def get_syllabi(request_id: str, max_chars: int = 60000, local_dir: str | None = None) -> dict:
    """Returns {'old': {...}, 'new': {...}} with the extracted text of each syllabus."""
    result = {"old": _empty("old"), "new": _empty("new")}
    local = Path(local_dir) if local_dir else None
    if local is None and not storage.is_configured():
        local = _SAMPLES / request_id
    names: list[str] = []
    bucket_error = None
    if local is None:
        try:
            names = storage.list_syllabi(request_id)
        except AgentError as exc:
            bucket_error = str(exc)
            local = _SAMPLES / request_id
    if local is not None:
        names = [str(p) for p in sorted(local.glob("*"))] if local.is_dir() else []
    for side in ("old", "new"):
        item = result[side]
        found = _find_side(names, side)
        if not found:
            item["error"] = bucket_error or f"No {side}-course syllabus has been uploaded."
            continue
        item.update(available=True, source="local" if local is not None else "bucket",
                    files=[n.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for n in found], file_count=len(found))
        item["filename"] = ", ".join(item["files"])
        texts, problems = [], []
        for name in found:
            label = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            try:
                data = Path(name).read_bytes() if local is not None else storage.download_syllabus(name)
                texts.append(read_document(data, name, max_chars) if len(found) == 1 else f"--- {label} ---\n" + read_document(data, name, max_chars))
            except (AgentError, DocumentReadError, OSError) as exc:
                problems.append(f"{label}: {exc}")
        if texts:
            item["text"] = "\n\n".join(texts)[:max_chars]
            item["readable"] = True
            if problems:
                item["error"] = "Some files could not be read: " + "; ".join(problems)
        else:
            item["error"] = "; ".join(problems)
    return result


def syllabus_request_message(syllabi: dict) -> str | None:
    """What to ask the course professor for, when something is missing or unreadable."""
    needed = [("old", "previously completed course"), ("new", "requested course")]
    missing = [label for s, label in needed if not syllabi[s]["readable"]]
    if not missing:
        return None
    return "Ask the course professor to upload a readable syllabus (PDF, DOCX or TXT) for the " + " and the ".join(missing) + "."


def text_overlap_percent(a: str, b: str) -> float:
    """Plain word-count cosine similarity. A rough baseline used as supporting evidence only."""
    def vec(t):
        return Counter(w for w in re.findall(r"[a-z]{3,}", t.lower()))
    va, vb = vec(a), vec(b)
    dot = sum(va[w] * vb[w] for w in va)
    na, nb = math.sqrt(sum(v * v for v in va.values())), math.sqrt(sum(v * v for v in vb.values()))
    return round(100 * dot / (na * nb), 1) if na and nb else 0.0


def _coverage(n_new: int, matches: list[dict], partial_weight: float) -> tuple[float | None, list[int]]:
    """Percentage of the new course's items covered by the old course, plus the uncovered indexes."""
    if n_new == 0:
        return None, []
    best: dict[int, float] = {}
    for m in matches:
        ni, oi = m.get("new_index"), m.get("old_index")
        if not isinstance(ni, int) or not isinstance(oi, int) or not 0 <= ni < n_new:
            continue
        w = 1.0 if m.get("strength") == "full" else partial_weight
        best[ni] = max(best.get(ni, 0.0), w)
    covered = sum(best.values())
    uncovered = [i for i in range(n_new) if i not in best]
    return round(100 * covered / n_new, 1), uncovered


def _assessment_similarity(a: dict | None, b: dict | None, categories: list[str]) -> float | None:
    def norm(d):
        vals = {c: float((d or {}).get(c) or 0) for c in categories}
        total = sum(vals.values())
        return {c: 100 * v / total for c, v in vals.items()} if total > 0 else None
    na, nb = norm(a), norm(b)
    if na is None or nb is None:
        return None
    return round(100 - sum(abs(na[c] - nb[c]) for c in categories) / 2, 1)


def compute_similarity(analysis: dict, old_credits, new_credits, policy: dict) -> dict:
    """Deterministic scoring of Gemini's structured comparison."""
    old, new = analysis["old"], analysis["new"]
    pw = policy.get("partial_match_weight", 0.5)
    topics_pct, topics_missing = _coverage(len(new["topics"]), analysis.get("topic_matches", []), pw)
    outcomes_pct, outcomes_missing = _coverage(len(new["learning_outcomes"]), analysis.get("outcome_matches", []), pw)
    assess = _assessment_similarity(old.get("assessment"), new.get("assessment"), policy["assessment_categories"])

    oc = old_credits if old_credits is not None else old.get("credit_hours")
    nc = new_credits if new_credits is not None else new.get("credit_hours")
    credit_pct = round(100 * min(oc / nc, 1.0), 1) if oc and nc else None
    credit_note = None
    for label, db, doc in (("old", old_credits, old.get("credit_hours")), ("new", new_credits, new.get("credit_hours"))):
        if db is not None and doc is not None and db != doc:
            credit_note = f"The {label} syllabus states {doc} credit hours but the catalog says {db}."

    components = {"topics": topics_pct, "learning_outcomes": outcomes_pct, "assessment_structure": assess, "credit_hours": credit_pct}
    weights = policy["weights"]
    used = {k: v for k, v in components.items() if v is not None}
    total_w = sum(weights[k] for k in used)
    overall = round(sum(weights[k] * v for k, v in used.items()) / total_w, 1) if used and topics_pct is not None and total_w else None

    t = policy["similarity_thresholds"]
    band = None if overall is None else ("EQUIVALENT_RANGE" if overall >= t["equivalent_min"]
                                         else "REVIEW_RANGE" if overall >= t["review_min"] else "BELOW_RANGE")
    ver = analysis.get("verification")
    limit = policy.get("max_unverified_match_percent", 30)
    return {
        "match_verification": ver,
        "verification_ok": None if ver is None else ver["dropped_percent"] <= limit,
        "overall_similarity_percent": overall,
        "score_band": band,
        "thresholds": t,
        "components_percent": components,
        "credit_hours": {"old": oc, "new": nc, "old_covers_new": (oc >= nc) if oc and nc else None, "note": credit_note},
        "new_topics_not_covered": [new["topics"][i] for i in topics_missing],
        "new_outcomes_not_covered": [new["learning_outcomes"][i] for i in outcomes_missing],
        "topics_counts": {"old": len(old["topics"]), "new": len(new["topics"])},
    }