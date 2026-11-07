from sqlalchemy import text

from app.exceptions import NotCourseEquivalencyRequestError, RequestNotFoundError
from app.services.database import get_connection

_OLD = ("Old_Course_ID", "Previous_Course_ID", "Completed_Course_ID", "Source_Course_ID")
_NEW = ("New_Course_ID", "Requested_Course_ID", "Target_Course_ID")
_REASON = ("Reason", "Justification", "Description")
_GRADE = ("Old_Grade", "Course_Grade", "Previous_Grade", "Grade")


def _pick(row: dict, candidates: tuple[str, ...]):
    for c in candidates:
        if c in row:
            return row[c]
    return None


def get_course_equivalency_request(request_id: str) -> dict:
    """Fetches the Request row and its Course_Equivalency row, and normalizes the
    old/new course columns so a small difference in column naming does not break the agent."""
    with get_connection() as conn:
        req = conn.execute(text('SELECT * FROM "Request" WHERE "Request_ID" = :rid'), {"rid": request_id}).mappings().first()
        if req is None:
            raise RequestNotFoundError(f"No request found with Request_ID={request_id!r}.")
        ce = conn.execute(text('SELECT * FROM "Course_Equivalency" WHERE "Request_ID" = :rid'), {"rid": request_id}).mappings().first()
    if ce is None:
        raise NotCourseEquivalencyRequestError(f"Request {request_id!r} exists but is not a Course Equivalency request.")
    req, ce = dict(req), dict(ce)
    old_id, new_id = _pick(ce, _OLD), _pick(ce, _NEW)
    if old_id is None or new_id is None:
        raise NotCourseEquivalencyRequestError(
            f"Course_Equivalency has no recognizable old/new course columns. Columns found: {sorted(ce)}. "
            f"Update _OLD/_NEW in app/tools/request_tools.py."
        )
    return {
        "request_id": request_id,
        "student_id": req.get("Student_ID"),
        "request_date": req.get("Request_Date"),
        "status": req.get("Current_Status") or req.get("Status"),
        "old_course_id": old_id,
        "new_course_id": new_id,
        "reason": _pick(ce, _REASON),
        "stated_grade": _pick(ce, _GRADE),
        # Any other Course_Equivalency columns (for example the external university) are passed along as context.
        "extra_fields": {k: v for k, v in ce.items() if k not in (*_OLD, *_NEW, *_REASON, *_GRADE, "Request_ID", "id", "created_at")},
    }


def list_course_equivalency_request_ids(student_id: str) -> list[str]:
    q = text('SELECT ce."Request_ID" FROM "Course_Equivalency" ce JOIN "Request" r ON r."Request_ID" = ce."Request_ID" '
             'WHERE r."Student_ID" = :sid ORDER BY ce."Request_ID"')
    with get_connection() as conn:
        return [r[0] for r in conn.execute(q, {"sid": student_id}).all()]
