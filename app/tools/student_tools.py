from sqlalchemy import text

from app.exceptions import StudentNotFoundError
from app.services.database import get_connection
from app.tools.policy import classify_grade


def get_student_academic_info(student_id: str) -> dict:
    with get_connection() as conn:
        row = conn.execute(text('SELECT * FROM "Student" WHERE "Student_ID" = :sid'), {"sid": student_id}).mappings().first()
    if row is None:
        raise StudentNotFoundError(f"No student found with Student_ID={student_id!r}.")
    row = dict(row)
    name = row.get("Name") or row.get("Student_Name") or row.get("Full_Name")
    if not name:
        first, last = row.get("First_Name"), row.get("Last_Name")
        name = " ".join(x for x in (first, last) if x) or None
    return {"student_id": student_id, "name": name, "cgpa": row.get("CGPA"),
            "completed_hours": row.get("Completed_Hours"), "program_id": row.get("Program_ID"),
            "program_name": row.get("Program_Name")}


def get_student_transcript(student_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(text('SELECT * FROM "Takes" WHERE "Student_ID" = :sid'), {"sid": student_id}).mappings().all()
    return [dict(r) for r in rows]


def find_course_in_transcript(transcript: list[dict], course_id) -> dict:
    """Looks for the old course in the transcript and classifies its grade with the approved policy."""
    matches = [r for r in transcript if str(r.get("Course_ID")) == str(course_id)]
    if not matches:
        return {"found": False, "grade": None, "passed": None, "term": None}
    # If the course was taken more than once, a passing attempt counts.
    best = next((m for m in matches if classify_grade(m.get("Grade")) is True), matches[0])
    return {"found": True, "grade": best.get("Grade"), "passed": classify_grade(best.get("Grade")),
            "term": best.get("Term"), "attempts": len(matches)}