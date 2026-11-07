from app.exceptions import CourseNotFoundError
from app.services.database import fetch_course


def course_level(course_id) -> int | None:
    """Level taken from the course number, e.g. 1501116 -> 1 (100-level), 1501459 -> 4 (400-level)."""
    s = str(course_id)
    return int(s[-3]) if len(s) >= 3 and s[-3].isdigit() else None


def get_old_and_new_course(old_course_id, new_course_id) -> dict:
    out = {}
    for label, cid in (("old", old_course_id), ("new", new_course_id)):
        row = fetch_course(cid) if cid is not None else None
        if row is None:
            # The completed course may come from outside the catalog; do not fail, mark it unverified.
            if label == "new":
                raise CourseNotFoundError(f"Requested course {cid} was not found in the Course table.")
            out[label] = {"course_id": cid, "name": None, "credit_hours": None, "level": course_level(cid), "in_catalog": False}
        else:
            out[label] = {"course_id": row["Course_ID"], "name": row["Course_Name"], "credit_hours": row["Credit_Hours"],
                          "level": course_level(row["Course_ID"]), "in_catalog": True}
    return out