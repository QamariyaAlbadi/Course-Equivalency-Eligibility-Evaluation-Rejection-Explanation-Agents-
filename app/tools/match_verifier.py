"""Checks Gemini's topic and outcome matches against the real syllabus text.

Gemini has to quote, for every match, the sentence of the OLD syllabus that shows the old course
covers the item and the text of the NEW syllabus where the item is listed. This module checks
that both quotes really appear in the uploaded documents. A match whose quote cannot be found
is dropped (counted as not covered), so an invented match can never raise the score.
"""

import re

MIN_QUOTE_CHARS = 8  # short topic names such as "Functions" or "Relations" are genuine quotes
WINDOW_COVERAGE = 0.9
_STOP = {"the", "and", "for", "with", "of", "in", "to", "a", "an", "on", "or", "by", "from", "their", "its"}


def _norm(text: str) -> str:
    """Lowercase, punctuation removed and white space collapsed, so line breaks, table cells and
    bullet characters in the document do not stop a genuine quote from being found."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def _words_close_together(q: str, haystack_norm: str) -> bool:
    """Fallback for a quote that is slightly reworded (a missing comma-separated word, a changed
    word order): its content words must nearly all occur within one short stretch of the document."""
    words = [w for w in q.split() if len(w) >= 3 and w not in _STOP]
    if len(words) < 2:
        return False
    hay = haystack_norm.split()
    size = max(2 * len(words) + 4, 12)
    need = max(2, int(WINDOW_COVERAGE * len(words) + 0.999))
    wanted = set(words)
    for i in range(max(1, len(hay) - size + 1)):
        if len(wanted & set(hay[i:i + size])) >= need:
            return True
    return False


def quote_in_text(quote: str, haystack_norm: str) -> bool:
    q = _norm(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return False
    return q in haystack_norm or _words_close_together(q, haystack_norm)


def _verify_list(matches: list[dict], n_old: int, n_new: int, old_norm: str, new_norm: str):
    kept, dropped = [], []
    for m in matches or []:
        ni, oi = m.get("new_index"), m.get("old_index")
        ok = (isinstance(ni, int) and isinstance(oi, int) and 0 <= ni < n_new and 0 <= oi < n_old
              and quote_in_text(m.get("old_quote", ""), old_norm) and quote_in_text(m.get("new_quote", ""), new_norm))
        if ok:
            kept.append(m)
        else:
            dropped.append(m)
    return kept, dropped


def verify_matches(analysis: dict, old_text: str, new_text: str) -> dict:
    """Returns the analysis with only the verified matches, plus a 'verification' summary and the
    evidence (the quote) for every kept match."""
    old_norm, new_norm = _norm(old_text), _norm(new_text)
    old, new = analysis["old"], analysis["new"]
    out = dict(analysis)
    summary, evidence, dropped_list = {}, [], []
    for kind, key, items in (("topic", "topic_matches", "topics"), ("outcome", "outcome_matches", "learning_outcomes")):
        claimed = analysis.get(key, [])
        kept, dropped = _verify_list(claimed, len(old[items]), len(new[items]), old_norm, new_norm)
        out[key] = kept
        summary[kind + "s"] = {"claimed": len(claimed), "verified": len(kept), "dropped": len(dropped)}
        for m in dropped:
            ni, oi = m.get("new_index"), m.get("old_index")
            dropped_list.append({"kind": kind,
                                 "requested_item": new[items][ni] if isinstance(ni, int) and 0 <= ni < len(new[items]) else None,
                                 "completed_item": old[items][oi] if isinstance(oi, int) and 0 <= oi < len(old[items]) else None,
                                 "quote_tried": (m.get("old_quote") or "").strip()})
        for m in kept:
            evidence.append({"kind": kind, "requested_item": new[items][m["new_index"]],
                             "completed_item": old[items][m["old_index"]], "strength": m["strength"],
                             "quote_from_completed_syllabus": m["old_quote"].strip()})
    claimed_total = sum(v["claimed"] for v in summary.values())
    dropped_total = sum(v["dropped"] for v in summary.values())
    out["verification"] = {**summary, "claimed_total": claimed_total, "dropped_total": dropped_total,
                           "dropped_percent": round(100 * dropped_total / claimed_total, 1) if claimed_total else 0.0,
                           "verified_matches": evidence, "dropped_matches": dropped_list}
    return out