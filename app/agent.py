"""Academic Eligibility Evaluation Agent For Course Equivalency.

Python gathers and computes verified evidence; Gemini recommends; the HOD/administrator decides.

    Request ID
    -> request details (Request + Course_Equivalency)
    -> student information and transcript
    -> old / new course details, credit hours and academic level
    -> was the old course passed (approved grade policy)
    -> syllabus retrieval (Supabase bucket) + Document Reader
    -> Gemini extracts topics / outcomes / assessment and matches them (analyze_syllabi)
    -> Python calculates the similarity score (syllabus_tools.compute_similarity)
    -> Gemini recommends APPROVE / REJECT / MANUAL_REVIEW from the evidence and the decision rules
    -> guardrails keep an unsafe APPROVE from going out

Run with:  python -m app.agent <request_id> [--syllabi <folder>] [--no-json]
"""

import json
import sys
import textwrap

from app.exceptions import AgentError
from app.services.gemini import analyze_syllabi, get_gemini_decision
from app.tools.course_tools import get_old_and_new_course
from app.tools.plan_tools import get_program_change_context, get_program_info, resolve_study_plan, transfer_limit, transferred_credits
from app.tools.policy import classify_grade, load_equivalency_policy, meets_minimum_grade
from app.tools.request_tools import get_course_equivalency_request
from app.tools.student_tools import find_course_in_transcript, get_student_academic_info, get_student_transcript
from app.tools.syllabus_tools import compute_similarity, get_syllabi, syllabus_request_message, text_overlap_percent

_LABELS = {"APPROVE": "EQUIVALENT", "REJECT": "NON_EQUIVALENT", "MANUAL_REVIEW": "FURTHER_REVIEW"}


def _level_evidence(courses: dict) -> dict:
    o, n = courses["old"]["level"], courses["new"]["level"]
    return {"old_level": o, "new_level": n, "old_at_least_new": (o >= n) if o is not None and n is not None else None}


def _constraints(student: dict, transcript: list[dict], courses: dict, grade, similarity, policy: dict, new_course_id) -> dict:
    """The university's course equivalency constraints, each verified by code. None = could not be verified."""
    old_c, new_c = courses["old"]["credit_hours"], courses["new"]["credit_hours"]
    if similarity:
        old_c, new_c = similarity["credit_hours"]["old"], similarity["credit_hours"]["new"]
    plan, used = resolve_study_plan(student, new_course_id, get_program_change_context(student["student_id"]))
    program = get_program_info(used["program_id"], used["program_name"])
    lim = transfer_limit(program["college"], program["plan_total_credits"])
    already = transferred_credits(transcript)
    total_after = already + new_c if new_c else None
    if total_after is None:
        within = None
    elif total_after <= 60 or lim["college_known"]:
        within = total_after <= lim["cap_credit_hours"]
    else:  # above 60 and the college (60 or 70 hour limit) is unknown
        within = False if total_after > 70 else None
    return {
        "minimum_grade": {"required": load_grade_required(), "grade": grade, "meets": meets_minimum_grade(grade)},
        "study_plan": {"program_checked": plan.get("program_checked"), "in_plan": plan["in_plan"], "note": plan["note"],
                       "program_change": plan.get("program_change")},
        "credit_hours": {"old": old_c, "new": new_c, "old_covers_new": (old_c >= new_c) if old_c and new_c else None},
        "transfer_limit": {"already_transferred": already, "this_request_adds": new_c, "total_after": total_after,
                           "cap_credit_hours": lim["cap_credit_hours"], "college": lim["college"], "within_limit": within,
                           "note": lim["community_college_note"]},
        "gpa_impact": policy["constraints"]["gpa_impact"],
    }


def load_grade_required() -> str:
    from app.tools.policy import load_grade_policy
    return load_grade_policy()["equivalency_minimum_grade"]


def _build_evidence_package(request_id: str, syllabi_dir: str | None = None) -> dict:
    """Collects and computes all deterministic evidence (plus Gemini's syllabus matching,
    which is converted to numbers by Python). Does NOT decide a recommendation."""
    policy = load_equivalency_policy()
    request = get_course_equivalency_request(request_id)
    student = get_student_academic_info(request["student_id"])
    transcript = get_student_transcript(request["student_id"])
    courses = get_old_and_new_course(request["old_course_id"], request["new_course_id"])
    old_in_transcript = find_course_in_transcript(transcript, request["old_course_id"])
    grade, grade_source = old_in_transcript["grade"], "transcript"
    if grade is None and request.get("stated_grade"):
        grade, grade_source = request["stated_grade"], "request"
    elif grade is None:
        grade_source = None

    syllabi = get_syllabi(request_id, policy["max_syllabus_chars"], syllabi_dir)
    syllabus_request = syllabus_request_message(syllabi)

    analysis_status, analysis_error, similarity, overlap = "skipped", None, None, None
    if syllabus_request is None:
        res = analyze_syllabi(syllabi["old"]["text"], syllabi["new"]["text"])
        analysis_status, analysis_error = res["status"], res["error"]
        overlap = text_overlap_percent(syllabi["old"]["text"], syllabi["new"]["text"])
        if res["analysis"] is not None:
            similarity = compute_similarity(res["analysis"], courses["old"]["credit_hours"], courses["new"]["credit_hours"], policy)
            similarity["extracted"] = {k: {"title": res["analysis"][k]["title"], "credit_hours": res["analysis"][k]["credit_hours"]}
                                       for k in ("old", "new")}

    constraints = _constraints(student, transcript, courses, grade, similarity, policy, request["new_course_id"])

    return {
        "request": {"request_id": request_id, "reason": request["reason"], "request_date": request["request_date"],
                    "other_request_fields": request.get("extra_fields") or {}},
        "student": student,
        "old_course": {**courses["old"], "in_transcript": old_in_transcript["found"], "grade": grade, "grade_source": grade_source,
                       "passed": classify_grade(grade) if grade is not None else None, "term": old_in_transcript["term"]},
        "constraints": constraints,
        "new_course": courses["new"],
        "academic_level": _level_evidence(courses),
        "syllabi": {s: {k: syllabi[s][k] for k in ("available", "readable", "source", "filename", "file_count", "error")} for s in ("old", "new")},
        "syllabus_request": syllabus_request,
        "syllabus_analysis": {"status": analysis_status, "error": analysis_error},
        "similarity": similarity,
        "text_overlap_percent": overlap,
    }


def _apply_guardrails(evidence: dict, g: dict) -> dict:
    """Deterministic safety net for the university's hard constraints. A verified violation forces
    REJECT; an unverified constraint blocks APPROVE. It never makes the outcome less cautious."""
    rec, notes = g["recommendation"], []
    c, sim = evidence["constraints"], evidence["similarity"]
    if g["status"] != "ok":
        rec = "MANUAL_REVIEW"
        g["reasoning"] = (f"The AI service could not produce a recommendation ({g['error']}). "
                          "The collected evidence is attached for manual review.")
    violations = []
    if c["minimum_grade"]["meets"] is False:
        violations.append(f"the completed course grade ({c['minimum_grade']['grade']}) is below the required minimum of {c['minimum_grade']['required']}")
    if c["study_plan"]["in_plan"] is False:
        violations.append("the requested course is not in the student's study plan")
    if c["credit_hours"]["old_covers_new"] is False:
        violations.append(f"the completed course has {c['credit_hours']['old']} credit hours, fewer than the {c['credit_hours']['new']} of the requested course")
    if c["transfer_limit"]["within_limit"] is False:
        violations.append(f"the transfer would bring the total to {c['transfer_limit']['total_after']} credit hours, above the {c['transfer_limit']['cap_credit_hours']} limit")
    if violations:
        if rec != "REJECT":
            notes.append("This request cannot be accepted because " + "; ".join(violations) + ".")
        rec = "REJECT"
    elif rec == "APPROVE":
        unverified = [n for n, v in (("minimum grade", c["minimum_grade"]["meets"]), ("study plan", c["study_plan"]["in_plan"]),
                                     ("transfer credit limit", c["transfer_limit"]["within_limit"]),
                                     ("credit hours", c["credit_hours"]["old_covers_new"])) if v is not True]
        if unverified:
            rec = "MANUAL_REVIEW"
            notes.append("Automatic approval was withheld because these could not be verified: " + ", ".join(unverified) + ".")
        elif sim and sim.get("verification_ok") is False:
            rec = "MANUAL_REVIEW"
            notes.append("Automatic approval was withheld because many of the AI's topic matches could not be confirmed in the syllabus text.")
        elif sim is None or sim["score_band"] != "EQUIVALENT_RANGE":
            rec = "MANUAL_REVIEW"
            notes.append("Automatic approval was withheld because the similarity score does not meet the approval threshold.")
    if notes:
        g["reasoning"] = (g["reasoning"] + " " + " ".join(notes)).strip()
    g["recommendation"] = rec
    return g


def evaluate_course_equivalency_request(request_id: str, syllabi_dir: str | None = None) -> dict:
    evidence = _build_evidence_package(request_id, syllabi_dir)
    gemini = get_gemini_decision(evidence) if not (evidence["syllabus_analysis"]["status"] in ("unavailable", "error")) else {
        "status": evidence["syllabus_analysis"]["status"], "error": evidence["syllabus_analysis"]["error"],
        "recommendation": "MANUAL_REVIEW", "reasoning": "", "checks": [], "major_similarities": [], "major_differences": []}
    gemini = _apply_guardrails(evidence, gemini)
    return {
        "request_id": request_id,
        "evidence": evidence,
        "recommendation": gemini["recommendation"],
        "equivalency_label": _LABELS[gemini["recommendation"]],
        "reasoning": gemini["reasoning"],
        "checks": gemini["checks"],
        "major_similarities": gemini["major_similarities"],
        "major_differences": gemini["major_differences"],
        "syllabus_request": evidence["syllabus_request"],
        "approval_note": evidence["constraints"]["gpa_impact"] if gemini["recommendation"] == "APPROVE" else None,
        "gemini_status": gemini["status"],
        "gemini_error": gemini["error"],
    }


def _wrap(text: str, indent: str = "    ") -> str:
    return "\n".join(textwrap.fill(p, 88, initial_indent=indent, subsequent_indent=indent) for p in str(text).splitlines() if p.strip())


def _print_report(result: dict) -> None:
    ev = result["evidence"]
    bar = "=" * 72
    old, new, sim = ev["old_course"], ev["new_course"], ev["similarity"]
    print(bar)
    print("  COURSE EQUIVALENCY - ACADEMIC ELIGIBILITY REPORT")
    print(bar)
    print(f"  Request   : {result['request_id']}")
    print(f"  Student   : {ev['student']['name']} ({ev['student']['student_id']})")
    print(f"  Completed : {old['course_id']} {old['name'] or '(not in catalog)'}  | grade: {old['grade'] or 'not found'}")
    print(f"  Requested : {new['course_id']} {new['name']}")
    print(f"  Reason    : {ev['request']['reason']}")
    print("-" * 72)
    cn = ev["constraints"]
    def mark(v):
        return "[ PASS ]" if v is True else "[ FAIL ]" if v is False else "[ ???? ]"
    print("  University constraints (verified by code; ???? = could not be verified)")
    print(f"  {mark(cn['minimum_grade']['meets'])} minimum grade {cn['minimum_grade']['required']} (grade: {cn['minimum_grade']['grade'] or 'not found'}, source: {old['grade_source'] or 'none'})")
    print(f"  {mark(cn['study_plan']['in_plan'])} requested course is in the study plan ({cn['study_plan']['program_checked'] or 'program unknown'})")
    if cn["study_plan"]["note"]:
        print(_wrap(cn["study_plan"]["note"], "           "))
    print(f"  {mark(cn['credit_hours']['old_covers_new'])} completed credit hours ({cn['credit_hours']['old']}) >= requested ({cn['credit_hours']['new']})")
    tl = cn["transfer_limit"]
    print(f"  {mark(tl['within_limit'])} transfer limit: {tl['total_after']} of {tl['cap_credit_hours']} credit hours")
    print("-" * 72)
    tag = {"PASS": "[ PASS ]", "FAIL": "[ FAIL ]", "N/A": "[ N/A  ]"}
    if result["checks"]:
        for c in result["checks"]:
            print(f"  {tag.get(c['status'], '[  ?   ]')} {c['name']}")
            print(_wrap(c["explanation"], "           "))
    if sim:
        print("-" * 72)
        print(f"  Overall similarity : {sim['overall_similarity_percent']}%  ({sim['score_band']})")
        for k, v in sim["components_percent"].items():
            print(f"    {k:<22}: {'n/a' if v is None else str(v) + '%'}")
        mv = sim.get("match_verification")
        if mv:
            print(f"  Matches confirmed in the syllabus text: {mv['claimed_total'] - mv['dropped_total']} of {mv['claimed_total']}"
                  + (f"  ({mv['dropped_total']} dropped: quote not found)" if mv["dropped_total"] else ""))
        if sim["new_topics_not_covered"]:
            print("  Requested-course topics not covered:")
            for t in sim["new_topics_not_covered"]:
                print(f"    - {t}")
    if result["major_similarities"]:
        print("  Major similarities:")
        for t in result["major_similarities"]:
            print(f"    + {t}")
    if result["major_differences"]:
        print("  Major differences:")
        for t in result["major_differences"]:
            print(f"    - {t}")
    if result["syllabus_request"]:
        print("-" * 72)
        print("  ACTION NEEDED:")
        print(_wrap(result["syllabus_request"]))
    print(bar)
    print(f"  RECOMMENDATION : {result['recommendation']}  ({result['equivalency_label']})")
    print(_wrap(result["reasoning"]))
    if result.get("approval_note"):
        print(_wrap("If approved: " + result["approval_note"]))
    print("  The final decision is made by the HOD/administrator.")
    print(bar)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    syllabi_dir = sys.argv[sys.argv.index("--syllabi") + 1] if "--syllabi" in sys.argv else None
    if syllabi_dir in args:
        args.remove(syllabi_dir)
    if len(args) != 1:
        print("Usage: python -m app.agent <request_id> [--syllabi <folder>] [--no-json]")
        sys.exit(1)
    try:
        result = evaluate_course_equivalency_request(args[0], syllabi_dir)
    except AgentError as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
    _print_report(result)
    if "--no-json" not in sys.argv:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()