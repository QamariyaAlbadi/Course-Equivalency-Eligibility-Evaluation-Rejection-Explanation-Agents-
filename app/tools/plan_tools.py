"""Study plan, program and transfer-credit lookups for the Course Equivalency constraints.

The schema of the study-plan table is discovered rather than assumed: it is the table that has
both Program_ID and Course_ID columns (or the one named in STUDY_PLAN_TABLE). When something
cannot be found the check returns `None` (unverified), which routes to manual review; it is
never treated as a pass or a fail.
"""

import json
import os
import re
from pathlib import Path

from sqlalchemy import text

from app.services.database import fetch_course, get_connection
from app.tools.policy import load_equivalency_policy, load_grade_policy

_NOT_PLAN_TABLES = {"Course", "Student", "Takes", "Section", "Request", "Course_Equivalency", "Request_History",
                    "Program_Change", "Program"}


_PLANS_PATH = Path(__file__).resolve().parent.parent / "knowledge_base" / "study_plans.json"


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z]+", " ", (name or "").lower()).strip()


def load_study_plan(program_name: str | None) -> dict | None:
    """The plan for the student's program from app/knowledge_base/study_plans.json (filled from the
    university website by app/tools/fetch_study_plans.py). Returns None when the file, the program,
    or the course list is missing, so an empty or missing plan is never read as 'course not in plan'."""
    if not program_name or not _PLANS_PATH.exists():
        return None
    try:
        data = json.loads(_PLANS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    target = _norm(program_name)
    for key, plan in (data.get("programs") or {}).items():
        names = [_norm(key)] + [_norm(n) for n in plan.get("match_names", [])]
        if any(n and (n in target or target in n) for n in names) and plan.get("courses"):
            return {"program": key, **plan, "source": data.get("source")}
    return None


def _plan_table() -> str | None:
    if os.getenv("STUDY_PLAN_TABLE"):
        return os.getenv("STUDY_PLAN_TABLE")
    q = text("SELECT table_name FROM information_schema.columns WHERE table_schema = 'public' "
             "AND column_name IN ('Program_ID', 'Course_ID') GROUP BY table_name "
             "HAVING COUNT(DISTINCT column_name) = 2")
    try:
        with get_connection() as conn:
            names = [r[0] for r in conn.execute(q).all()]
    except Exception:  # noqa: BLE001
        return None
    names = [n for n in names if n not in _NOT_PLAN_TABLES]
    return names[0] if len(names) == 1 else None


def get_program_info(program_id, program_name: str | None = None) -> dict:
    """Program row, college name (if it can be found) and the total credits of the study plan."""
    info = {"program_id": program_id, "college": None, "plan_total_credits": None, "plan_table": None}
    plan = load_study_plan(program_name)
    if plan:
        info["plan_table"] = "study_plans.json"
        info["plan_total_credits"] = plan.get("total_credits")
    if program_id is None:
        return info
    try:
        with get_connection() as conn:
            prog = conn.execute(text('SELECT * FROM "Program" WHERE "Program_ID"::text = :p'), {"p": str(program_id)}).mappings().first()
            prog = dict(prog) if prog else {}
            info["college"] = prog.get("College_Name") or prog.get("College")
            if info["college"] is None and prog.get("College_ID") is not None:
                try:
                    col = conn.execute(text('SELECT * FROM "College" WHERE "College_ID"::text = :c'), {"c": str(prog["College_ID"])}).mappings().first()
                    info["college"] = (dict(col).get("College_Name") or dict(col).get("Name")) if col else None
                except Exception:  # noqa: BLE001
                    pass
    except Exception:  # noqa: BLE001
        return info
    if plan:
        return info
    table = _plan_table()
    info["plan_table"] = table
    if table:
        try:
            q = text(f'SELECT COALESCE(SUM(c."Credit_Hours"), 0) FROM "{table}" sp JOIN "Course" c ON c."Course_ID"::text = sp."Course_ID"::text '
                     'WHERE sp."Program_ID"::text = :p')
            with get_connection() as conn:
                total = conn.execute(q, {"p": str(program_id)}).scalar()
            info["plan_total_credits"] = int(total) if total else None
        except Exception:  # noqa: BLE001
            pass
    return info


def course_in_study_plan(program_id, course_id, program_name: str | None = None) -> dict:
    out = {"in_plan": None, "table": None, "note": None}
    plan = load_study_plan(program_name)
    if plan:
        ids = {str(c["course_id"]) for c in plan["courses"]}
        out.update(in_plan=str(course_id) in ids, table="study_plans.json")
        return out
    table = _plan_table()
    if program_id is None or table is None:
        out["note"] = "The study plan table could not be identified, so this was not verified."
        return out
    out["table"] = table
    try:
        q = text(f'SELECT 1 FROM "{table}" WHERE "Program_ID"::text = :p AND "Course_ID"::text = :c LIMIT 1')
        with get_connection() as conn:
            out["in_plan"] = conn.execute(q, {"p": str(program_id), "c": str(course_id)}).first() is not None
    except Exception as exc:  # noqa: BLE001
        out["note"] = f"The study plan could not be read: {type(exc).__name__}."
    return out


def transferred_credits(transcript: list[dict]) -> int:
    """Credit hours already transferred (grade TR) in the student's transcript."""
    code = load_grade_policy()["transfer_grade_code"].upper()
    total = 0
    for row in transcript:
        if str(row.get("Grade") or "").strip().upper() == code:
            course = fetch_course(row["Course_ID"]) if row.get("Course_ID") is not None else None
            total += int(course["Credit_Hours"]) if course and course.get("Credit_Hours") else 0
    return total


def transfer_limit(college: str | None, plan_total_credits: int | None) -> dict:
    lim = load_equivalency_policy()["constraints"]["transfer_credit_limits"]
    extended = bool(college) and any(k in college.lower() for k in lim["extended_college_keywords"])
    cap = lim["extended_max_credit_hours"] if extended else lim["default_max_credit_hours"]
    half = int(plan_total_credits * lim["max_fraction_of_study_plan"]) if plan_total_credits else None
    return {"college": college, "college_known": college is not None, "cap_credit_hours": min(cap, half) if half else cap,
            "college_cap": cap, "half_of_plan": half, "community_college_note": lim["community_college_note"]}


_DONE = {"completed", "approved"}
_OPEN = {"in progress", "pending", "pending review"}
_REJECTED = {"rejected", "declined"}


def get_program_change_context(student_id: str) -> dict | None:
    """The student's Program Change requests. A student first applies for a program change and
    then for course equivalency, so the requested course must be checked against the program the
    student is moving to, not only the current one.

    Returns {"completed": {...} | None, "pending": {...} | None}, or None if the lookup fails.
    Each entry has new_program_id, new_program_name, current_program_name and status."""
    q = text('SELECT pc.*, r."Current_Status" AS "_status", r."Request_Date" AS "_date" '
             'FROM "Program_Change" pc JOIN "Request" r ON r."Request_ID" = pc."Request_ID" '
             'WHERE r."Student_ID" = :sid ORDER BY r."Request_Date" DESC, r."Request_ID" DESC')
    try:
        with get_connection() as conn:
            rows = [dict(r) for r in conn.execute(q, {"sid": student_id}).mappings().all()]
    except Exception:  # noqa: BLE001
        return None
    out = {"completed": None, "pending": None, "rejected": None}
    for row in rows:
        status = str(row.get("_status") or "").strip().lower()
        entry = {"new_program_id": row.get("New_Program_ID"), "new_program_name": row.get("New_Program_Name"),
                 "current_program_name": row.get("Current_Program_Name"), "status": row.get("_status")}
        if status in _DONE and out["completed"] is None:
            out["completed"] = entry
        elif status in _OPEN and out["pending"] is None:
            out["pending"] = entry
        elif status in _REJECTED and out["rejected"] is None:
            out["rejected"] = entry
    return out


def resolve_study_plan(student: dict, new_course_id, program_change: dict | None) -> tuple[dict, dict]:
    """Checks the requested course against the right program's study plan.

    - an approved program change: the plan of the new program is used;
    - a pending program change: if the course is only in the new program's plan, the check is
      unverified (None) because the change has not been approved yet;
    - a rejected program change: the student stays in the current program; if the course belongs
      to the plan of the program that was refused, that is stated in the note;
    - otherwise the student's current program is used.
    A program change is only attached to the result when it matters for this course.
    Returns (study plan result, program used for the lookups)."""
    pc = program_change or {}
    done, pending, rejected = pc.get("completed"), pc.get("pending"), pc.get("rejected")
    program = {"program_id": student.get("program_id"), "program_name": student.get("program_name")}
    if done:
        program = {"program_id": done["new_program_id"], "program_name": done["new_program_name"] or program["program_name"]}
    plan = course_in_study_plan(program["program_id"], new_course_id, program["program_name"])
    plan["program_checked"] = program["program_name"]
    plan["program_change"] = {**done, "result": "approved"} if done else None
    if plan["in_plan"] is False and not done:
        if pending and course_in_study_plan(pending["new_program_id"], new_course_id, pending["new_program_name"])["in_plan"] is True:
            plan["in_plan"] = None
            plan["program_change"] = {**pending, "result": "pending"}
            plan["note"] = (f"The course is in the {pending['new_program_name']} plan, but the student's program change "
                            "to that program has not been approved yet.")
        elif rejected and course_in_study_plan(rejected["new_program_id"], new_course_id, rejected["new_program_name"])["in_plan"] is True:
            plan["program_change"] = {**rejected, "result": "rejected"}
            plan["note"] = (f"The course is in the {rejected['new_program_name']} plan, but the student's program change "
                            f"to that program was rejected, so the student remains in {program['program_name']}.")
    return plan, program