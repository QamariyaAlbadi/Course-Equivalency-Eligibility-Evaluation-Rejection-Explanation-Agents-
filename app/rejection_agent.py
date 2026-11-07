"""Rejection Explanation Agent For Course Equivalency.

Input: the eligibility agent's result. It only works on REJECT; anything else returns
`not_applicable`. Verified keywords come from code, judged keywords from Gemini's closed list,
and three suggested reasons (formal, concise, supportive) are validated before display.

Statuses: ok | fallback | unavailable | needs_manual_explanation | not_applicable

Run with:  python -m app.rejection_agent <request_id | result.json>
"""

import json
import sys
import textwrap
from pathlib import Path

from app.exceptions import AgentError
from app.services.rejection_gemini import get_rejection_suggestions
from app.tools.explanation_generator import allowed_numbers, template_reasons, validate_reasons
from app.tools.rejection_keyword_tools import load_keywords, verified_facts


def explain_rejection_workflow(agent8_result: dict) -> dict:
    base = {"request_id": agent8_result.get("request_id"), "status": "not_applicable", "keywords": [],
            "suggested_reasons": [], "notes": []}
    if agent8_result.get("recommendation") != "REJECT":
        base["notes"].append("The request was not recommended for rejection, so no rejection explanation is needed.")
        return base

    kw = load_keywords()
    verified = verified_facts(agent8_result)
    ev = agent8_result["evidence"]
    sim = ev["similarity"]
    context = {
        "request_id": agent8_result["request_id"],
        "completed_course": ev["old_course"]["name"] or ev["old_course"]["course_id"],
        "requested_course": ev["new_course"]["name"],
        "similarity_percent": sim["overall_similarity_percent"] if sim else None,
        "constraints": ev["constraints"],
        "component_percent": sim["components_percent"] if sim else None,
        "deterministic_keywords": verified,
        "judgment_keyword_options": kw["judged"],
        "eligibility_reasoning": agent8_result.get("reasoning"),
        "major_differences": agent8_result.get("major_differences"),
        "check_explanations": agent8_result.get("checks"),
    }
    suggestions = get_rejection_suggestions(context)
    judged = [k for k in suggestions["judgment_keywords"] if k in kw["judged"]]
    if suggestions["conflict_note"]:
        base["notes"].append(f"Conflict noted: {suggestions['conflict_note']}")

    if not verified and not judged:
        base.update(status="needs_manual_explanation")
        base["notes"].append("No rejection keyword could be verified from the evidence. The staff member should write the explanation.")
        return base

    valid = set(verified) | set(judged)
    kept, dropped = ([], []) if suggestions["status"] != "ok" else validate_reasons(
        suggestions["suggested_reasons"], valid, allowed_numbers(json.dumps(context, default=str)))
    base["notes"].extend(dropped)
    if suggestions["status"] == "ok" and kept:
        base.update(status="ok", suggested_reasons=kept)
    else:
        base.update(status="unavailable" if suggestions["status"] == "unavailable" else "fallback",
                    suggested_reasons=template_reasons(verified, judged))
        if suggestions["error"]:
            base["notes"].append(f"Gemini: {suggestions['error']}")
    base["keywords"] = [{"id": k, "type": "verified", "description": kw["verified"][k], "facts": v} for k, v in verified.items()] + \
                       [{"id": k, "type": "judged", "description": kw["judged"][k]} for k in judged]
    return base


def _print_report(res: dict) -> None:
    bar = "=" * 72
    print(bar)
    print("  COURSE EQUIVALENCY - REJECTION EXPLANATION")
    print(bar)
    print(f"  Request : {res['request_id']}")
    print(f"  Status  : {res['status']}")
    if res["status"] == "not_applicable":
        print("  " + res["notes"][0])
        print(bar)
        return
    if res["keywords"]:
        print("  Keywords:")
        for k in res["keywords"]:
            print(f"    - {k['id']} ({k['type']}): {k['description']}")
    for i, r in enumerate(res["suggested_reasons"], 1):
        print(f"\n  Suggested reason {i} [{r['tone']}]")
        print(textwrap.fill(r["text"], 88, initial_indent="    ", subsequent_indent="    "))
    for n in res["notes"]:
        print(f"\n  Note: {n}")
    print("\n  The staff member chooses or edits the final wording.")
    print(bar)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m app.rejection_agent <request_id | result.json>")
        sys.exit(1)
    arg = sys.argv[1]
    try:
        if Path(arg).suffix == ".json":
            agent8 = json.loads(Path(arg).read_text(encoding="utf-8"))
        else:
            from app.agent import evaluate_course_equivalency_request
            agent8 = evaluate_course_equivalency_request(arg)
        res = explain_rejection_workflow(agent8)
    except AgentError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    _print_report(res)


if __name__ == "__main__":
    main()
