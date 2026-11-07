"""Runs the eligibility agent and, only when it recommends REJECT, the rejection explanation agent.

    python -m app.pipeline <request_id> [--syllabi <folder>] [--no-json]
    python -m app.pipeline --student <Student_ID> [--no-json]
"""

import json
import sys

from app.agent import _print_report as print_eligibility
from app.agent import evaluate_course_equivalency_request
from app.exceptions import AgentError
from app.rejection_agent import _print_report as print_rejection
from app.rejection_agent import explain_rejection_workflow


def run_pipeline(request_id: str, syllabi_dir: str | None = None) -> dict:
    eligibility = evaluate_course_equivalency_request(request_id, syllabi_dir)
    rejection = explain_rejection_workflow(eligibility)
    return {"request_id": request_id, "eligibility": eligibility, "rejection_explanation": rejection}


def run_pipeline_for_student(student_id: str) -> list[dict]:
    from app.tools.request_tools import list_course_equivalency_request_ids
    ids = list_course_equivalency_request_ids(student_id)
    if not ids:
        raise AgentError(f"No Course Equivalency requests found for student {student_id!r}.")
    return [run_pipeline(i) for i in ids]


def _print_one(out: dict) -> None:
    print_eligibility(out["eligibility"])
    if out["eligibility"]["recommendation"] == "REJECT":
        print()
        print_rejection(out["rejection_explanation"])


def main() -> None:
    argv = sys.argv[1:]
    show_json = "--no-json" not in argv
    syllabi_dir = argv[argv.index("--syllabi") + 1] if "--syllabi" in argv else None
    try:
        if "--student" in argv:
            outs = run_pipeline_for_student(argv[argv.index("--student") + 1])
        else:
            rest = [a for a in argv if not a.startswith("--") and a != syllabi_dir]
            if len(rest) != 1:
                print(__doc__)
                sys.exit(1)
            outs = [run_pipeline(rest[0], syllabi_dir)]
    except AgentError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    for o in outs:
        _print_one(o)
        if show_json:
            print(json.dumps(o, indent=2, default=str))


if __name__ == "__main__":
    main()
