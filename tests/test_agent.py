"""Mocked tests: no database, storage or Gemini access needed. Run with:  python -m pytest"""

import io
import tempfile
from pathlib import Path

import pytest

from app import agent, pipeline, rejection_agent
from app.exceptions import DocumentReadError
from app.tools import document_reader, syllabus_tools
from app.tools.explanation_generator import allowed_numbers, template_reasons, validate_reasons
from app.tools.policy import classify_grade, load_equivalency_policy
from app.tools.syllabus_tools import compute_similarity

# The tests build their own small syllabus files, so they do not depend on the samples folder.
SAMPLES = Path(tempfile.mkdtemp(prefix="syllabi_"))
(SAMPLES / "equivalent_pair").mkdir()
(SAMPLES / "equivalent_pair" / "old_course.txt").write_text(
    "Course Title: Introduction to Programming\nCredit Hours: 3\nTopics: variables, loops, methods, arrays, strings, classes, file input and output.\n"
    "Learning Outcomes: write programs, use arrays, test and debug code.\nAssessment: Exams 60%, Assignments 40%.", encoding="utf-8")
(SAMPLES / "equivalent_pair" / "new_course.txt").write_text(
    "Course Title: Programming I\nCredit Hours: 3\nTopics: variables, selection, loops, methods, arrays, strings, classes, files.\n"
    "Learning Outcomes: write programs, use arrays, test and debug code.\nAssessment: Exams 60%, Assignments 40%.", encoding="utf-8")


def _analysis(n_new=4, full=4, partial=0, old_assess=None, new_assess=None, n_out=2, out_full=2):
    return {
        "old": {"title": "Old", "credit_hours": 3, "topics": [f"o{i}" for i in range(6)], "learning_outcomes": ["a", "b"],
                "assessment": old_assess or {"exams": 50, "assignments": 50}},
        "new": {"title": "New", "credit_hours": 3, "topics": [f"t{i}" for i in range(n_new)], "learning_outcomes": ["a"] * n_out,
                "assessment": new_assess or {"exams": 50, "assignments": 50}},
        "topic_matches": [{"new_index": i, "old_index": i, "strength": "full"} for i in range(full)]
                         + [{"new_index": full + i, "old_index": 0, "strength": "partial"} for i in range(partial)],
        "outcome_matches": [{"new_index": i, "old_index": i, "strength": "full"} for i in range(out_full)],
    }


# ---------- policy / grades ----------
def test_grade_policy():
    assert classify_grade("B+") is True
    assert classify_grade("F") is False
    assert classify_grade(None) is False
    assert classify_grade("Z") is None


def test_policy_loads_with_80_60():
    t = load_equivalency_policy()["similarity_thresholds"]
    assert (t["equivalent_min"], t["review_min"]) == (80, 60)


# ---------- scoring ----------
def test_identical_courses_score_100_and_equivalent_band():
    s = compute_similarity(_analysis(), 3, 3, load_equivalency_policy())
    assert s["overall_similarity_percent"] == 100.0
    assert s["score_band"] == "EQUIVALENT_RANGE"
    assert s["new_topics_not_covered"] == []


def test_uncovered_topics_are_listed_and_lower_the_score():
    s = compute_similarity(_analysis(n_new=4, full=2, out_full=0), 3, 3, load_equivalency_policy())
    assert s["new_topics_not_covered"] == ["t2", "t3"]
    assert s["components_percent"]["topics"] == 50.0
    assert s["score_band"] == "BELOW_RANGE"


def test_partial_matches_count_half():
    s = compute_similarity(_analysis(n_new=2, full=1, partial=1), 3, 3, load_equivalency_policy())
    assert s["components_percent"]["topics"] == 75.0


def test_invalid_match_indexes_are_ignored():
    a = _analysis(n_new=2, full=0)
    a["topic_matches"] = [{"new_index": 9, "old_index": 0, "strength": "full"}, {"new_index": -1, "old_index": 0, "strength": "full"}]
    assert compute_similarity(a, 3, 3, load_equivalency_policy())["components_percent"]["topics"] == 0.0


def test_credit_hours_old_lower_than_new():
    s = compute_similarity(_analysis(), 2, 3, load_equivalency_policy())
    assert s["credit_hours"]["old_covers_new"] is False
    assert s["components_percent"]["credit_hours"] == 66.7


def test_credit_mismatch_between_catalog_and_syllabus_is_noted():
    s = compute_similarity(_analysis(), 4, 3, load_equivalency_policy())
    assert "catalog says 4" in s["credit_hours"]["note"]


def test_assessment_similarity():
    s = compute_similarity(_analysis(old_assess={"exams": 100}, new_assess={"projects": 100}), 3, 3, load_equivalency_policy())
    assert s["components_percent"]["assessment_structure"] == 0.0


def test_no_topics_means_no_score():
    a = _analysis(n_new=0, full=0)
    assert compute_similarity(a, 3, 3, load_equivalency_policy())["overall_similarity_percent"] is None


def test_text_overlap_baseline():
    assert syllabus_tools.text_overlap_percent("java arrays loops", "java arrays loops") == 100.0
    assert syllabus_tools.text_overlap_percent("java arrays", "sql joins") == 0.0


# ---------- document reader ----------
def test_reads_txt_docx_pdf(tmp_path):
    text = "Course Title: Test\nTopics: loops, arrays, recursion, sorting, and searching algorithms."
    assert "loops" in document_reader.read_document(text.encode(), "s.txt")

    import docx
    d = docx.Document()
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    assert "recursion" in document_reader.read_document(buf.getvalue(), "s.docx")

    try:  # reportlab only builds a test PDF; the PDF part is skipped if it is not installed
        from reportlab.pdfgen import canvas
    except ImportError:
        return
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 750, text[:80])
    c.drawString(72, 730, text[80:])
    c.save()
    assert "Course Title" in document_reader.read_document(buf.getvalue(), "s.pdf")


def test_unsupported_or_empty_document_raises():
    with pytest.raises(DocumentReadError):
        document_reader.read_document(b"x", "s.exe")
    with pytest.raises(DocumentReadError):
        document_reader.read_document(b"  ", "s.txt")


# ---------- syllabus retrieval ----------
def test_local_syllabi_found():
    s = syllabus_tools.get_syllabi("X", local_dir=str(SAMPLES / "equivalent_pair"))
    assert s["old"]["readable"] and s["new"]["readable"]
    assert syllabus_tools.syllabus_request_message(s) is None


def test_missing_syllabus_creates_upload_request(tmp_path):
    (tmp_path / "old_course.txt").write_text("Course Title: Something with enough text to be readable by the reader tool.")
    s = syllabus_tools.get_syllabi("X", local_dir=str(tmp_path))
    msg = syllabus_tools.syllabus_request_message(s)
    assert s["new"]["available"] is False
    assert "requested course" in msg and "previously completed" not in msg


# ---------- evaluation workflow (all external calls mocked) ----------
def _patch(monkeypatch, grade="B", in_plan=True, program_change=None, plans=None, transferred=0, college="College of Sciences", plan_total=132, stated_grade=None, old_credit=3, new_credit=3, syllabi_dir=None, analysis=None, decision=None, old_id=1501110, new_id=1501116):
    monkeypatch.setattr(agent, "get_course_equivalency_request", lambda r: {
        "request_id": r, "student_id": "U1", "request_date": "2026-10-01", "old_course_id": old_id, "new_course_id": new_id, "reason": "Completed elsewhere",
        "stated_grade": stated_grade, "extra_fields": {}})
    monkeypatch.setattr(agent, "get_program_info", lambda p, n=None: {"program_id": p, "college": college, "plan_total_credits": plan_total, "plan_table": "Study_Plan"})
    from app.tools import plan_tools
    monkeypatch.setattr(agent, "get_program_change_context", lambda sid: program_change)
    monkeypatch.setattr(plan_tools, "course_in_study_plan",
                        plans or (lambda p, c, n=None: {"in_plan": in_plan, "table": "Study_Plan", "note": None}))
    monkeypatch.setattr(agent, "transferred_credits", lambda t: transferred)
    monkeypatch.setattr(agent, "get_student_academic_info", lambda s: {"student_id": s, "name": "Test Student", "cgpa": 3.0, "program_id": 7, "program_name": "Computer Science"})
    monkeypatch.setattr(agent, "get_student_transcript", lambda s: [{"Course_ID": old_id, "Grade": grade, "Term": 202510}] if grade != "none" else [])
    monkeypatch.setattr(agent, "get_old_and_new_course", lambda o, n: {
        "old": {"course_id": o, "name": "Old Course", "credit_hours": old_credit, "level": 1, "in_catalog": True},
        "new": {"course_id": n, "name": "New Course", "credit_hours": new_credit, "level": 1, "in_catalog": True}})
    monkeypatch.setattr(agent, "analyze_syllabi", lambda o, n: {"status": "ok", "error": None, "analysis": analysis or _analysis()})
    monkeypatch.setattr(agent, "get_gemini_decision", lambda ev: decision or {
        "status": "ok", "error": None, "recommendation": "APPROVE", "reasoning": "Courses match.", "checks": [],
        "major_similarities": [], "major_differences": []})
    return str(SAMPLES / "equivalent_pair") if syllabi_dir is None else syllabi_dir


def test_equivalent_request_is_approved(monkeypatch):
    d = _patch(monkeypatch)
    r = agent.evaluate_course_equivalency_request("R1", d)
    assert r["recommendation"] == "APPROVE" and r["equivalency_label"] == "EQUIVALENT"
    assert r["evidence"]["similarity"]["overall_similarity_percent"] == 100.0


def test_failing_grade_forces_reject_even_if_gemini_approves(monkeypatch):
    d = _patch(monkeypatch, grade="F")
    r = agent.evaluate_course_equivalency_request("R1", d)
    assert r["recommendation"] == "REJECT"


def test_approve_downgraded_when_score_not_in_equivalent_range(monkeypatch):
    d = _patch(monkeypatch, analysis=_analysis(n_new=4, full=2, out_full=1))
    r = agent.evaluate_course_equivalency_request("R1", d)
    assert r["recommendation"] == "MANUAL_REVIEW"


def test_approve_downgraded_when_course_not_in_transcript(monkeypatch):
    d = _patch(monkeypatch, grade="none")
    assert agent.evaluate_course_equivalency_request("R1", d)["recommendation"] == "MANUAL_REVIEW"


def test_missing_syllabus_asks_for_upload_and_never_approves(monkeypatch, tmp_path):
    (tmp_path / "old_course.txt").write_text("Course Title: Old course with a readable amount of text in the file.")
    _patch(monkeypatch)
    r = agent.evaluate_course_equivalency_request("R1", str(tmp_path))
    assert r["recommendation"] == "MANUAL_REVIEW"
    assert "upload" in r["syllabus_request"]
    assert r["evidence"]["similarity"] is None


def test_gemini_unavailable_gives_manual_review(monkeypatch):
    d = _patch(monkeypatch, decision={"status": "unavailable", "error": "no key", "recommendation": "MANUAL_REVIEW",
                                       "reasoning": "", "checks": [], "major_similarities": [], "major_differences": []})
    r = agent.evaluate_course_equivalency_request("R1", d)
    assert r["recommendation"] == "MANUAL_REVIEW" and "could not produce" in r["reasoning"]


def test_syllabus_analysis_failure_gives_manual_review(monkeypatch):
    d = _patch(monkeypatch)
    monkeypatch.setattr(agent, "analyze_syllabi", lambda o, n: {"status": "unavailable", "error": "no key", "analysis": None})
    r = agent.evaluate_course_equivalency_request("R1", d)
    assert r["recommendation"] == "MANUAL_REVIEW"


# ---------- university constraints ----------
def test_minimum_grade_is_c_plus():
    from app.tools.policy import meets_minimum_grade
    assert [meets_minimum_grade(g) for g in ("A", "B", "C+", "C", "D", "F", None, "Z")] == [True, True, True, False, False, False, None, None]


def test_grade_c_is_passing_but_below_minimum_so_rejected(monkeypatch):
    d = _patch(monkeypatch, grade="C")
    r = agent.evaluate_course_equivalency_request("R1", d)
    assert r["recommendation"] == "REJECT" and "below the required minimum of C+" in r["reasoning"]


def test_course_not_in_study_plan_is_rejected(monkeypatch):
    d = _patch(monkeypatch, in_plan=False)
    r = agent.evaluate_course_equivalency_request("R1", d)
    assert r["recommendation"] == "REJECT" and "study plan" in r["reasoning"]


def test_old_course_with_fewer_credit_hours_is_rejected(monkeypatch):
    d = _patch(monkeypatch, old_credit=2, new_credit=3)
    assert agent.evaluate_course_equivalency_request("R1", d)["recommendation"] == "REJECT"


def test_equal_or_greater_credit_hours_pass(monkeypatch):
    d = _patch(monkeypatch, old_credit=4, new_credit=3)
    assert agent.evaluate_course_equivalency_request("R1", d)["recommendation"] == "APPROVE"


def test_transfer_limit_60_default_and_70_for_engineering(monkeypatch):
    d = _patch(monkeypatch, transferred=58, new_credit=3)
    assert agent.evaluate_course_equivalency_request("R1", d)["recommendation"] == "REJECT"
    d = _patch(monkeypatch, transferred=58, new_credit=3, college="College of Engineering")
    assert agent.evaluate_course_equivalency_request("R1", d)["recommendation"] == "APPROVE"


def test_half_of_study_plan_limit(monkeypatch):
    d = _patch(monkeypatch, transferred=45, new_credit=3, plan_total=90)
    r = agent.evaluate_course_equivalency_request("R1", d)
    assert r["recommendation"] == "REJECT"
    assert r["evidence"]["constraints"]["transfer_limit"]["cap_credit_hours"] == 45


def test_unverified_study_plan_blocks_approval(monkeypatch):
    d = _patch(monkeypatch, in_plan=None)
    assert agent.evaluate_course_equivalency_request("R1", d)["recommendation"] == "MANUAL_REVIEW"


def test_grade_stated_in_request_is_used_and_labelled(monkeypatch):
    d = _patch(monkeypatch, grade="none", stated_grade="A")
    r = agent.evaluate_course_equivalency_request("R1", d)
    assert r["evidence"]["old_course"]["grade_source"] == "request"
    assert r["recommendation"] == "APPROVE"


def test_approval_note_mentions_tr_and_cgpa(monkeypatch):
    r = agent.evaluate_course_equivalency_request("R1", _patch(monkeypatch))
    assert "TR" in r["approval_note"] and "CGPA" in r["approval_note"]


# ---------- rejection explanation agent ----------
def _rejected(monkeypatch, **kw):
    d = _patch(monkeypatch, decision={"status": "ok", "error": None, "recommendation": "REJECT", "reasoning": "Low similarity.",
                                      "checks": [], "major_similarities": [], "major_differences": ["Trees not covered"]},
               analysis=_analysis(n_new=4, full=1, out_full=0), **kw)
    return agent.evaluate_course_equivalency_request("R1", d)


def test_not_applicable_when_not_rejected(monkeypatch):
    d = _patch(monkeypatch)
    res = rejection_agent.explain_rejection_workflow(agent.evaluate_course_equivalency_request("R1", d))
    assert res["status"] == "not_applicable"


def test_verified_keywords_and_gemini_reasons(monkeypatch):
    r = _rejected(monkeypatch, old_credit=2)
    sim = r["evidence"]["similarity"]["overall_similarity_percent"]
    good = {"tone": "formal", "text": f"The syllabus comparison shows {sim}% similarity, so the request cannot be approved.",
            "addresses_keywords": ["similarity_below_threshold", "missing_course_topics"]}
    bad_number = {"tone": "concise", "text": "The courses are 99.9% similar.", "addresses_keywords": ["similarity_below_threshold"]}
    no_kw = {"tone": "supportive", "text": "Sorry.", "addresses_keywords": ["made_up"]}
    monkeypatch.setattr(rejection_agent, "get_rejection_suggestions", lambda ctx: {
        "status": "ok", "error": None, "judgment_keywords": ["learning_outcomes_gap", "unknown_kw"],
        "suggested_reasons": [good, bad_number, no_kw], "conflict_note": ""})
    res = rejection_agent.explain_rejection_workflow(r)
    ids = {k["id"] for k in res["keywords"]}
    assert {"similarity_below_threshold", "missing_course_topics", "credit_hours_lower", "learning_outcomes_gap"} <= ids
    assert "unknown_kw" not in ids
    assert res["status"] == "ok" and len(res["suggested_reasons"]) == 1
    assert len(res["notes"]) == 2


def test_fallback_templates_when_gemini_unavailable(monkeypatch):
    r = _rejected(monkeypatch)
    monkeypatch.setattr(rejection_agent, "get_rejection_suggestions", lambda ctx: {
        "status": "unavailable", "error": "no key", "judgment_keywords": [], "suggested_reasons": [], "conflict_note": ""})
    res = rejection_agent.explain_rejection_workflow(r)
    assert res["status"] == "unavailable"
    assert [x["tone"] for x in res["suggested_reasons"]] == ["formal", "concise", "supportive"]
    assert "%" in res["suggested_reasons"][0]["text"]


def test_constraint_keywords(monkeypatch):
    d = _patch(monkeypatch, grade="C", in_plan=False, transferred=60)
    r = agent.evaluate_course_equivalency_request("R1", d)
    monkeypatch.setattr(rejection_agent, "get_rejection_suggestions", lambda ctx: {
        "status": "unavailable", "error": "x", "judgment_keywords": [], "suggested_reasons": [], "conflict_note": ""})
    ids = {k["id"] for k in rejection_agent.explain_rejection_workflow(r)["keywords"]}
    assert {"grade_below_minimum", "course_not_in_study_plan", "transfer_credit_limit_exceeded"} <= ids


def test_failing_grade_keyword(monkeypatch):
    r = _rejected(monkeypatch, grade="F")
    monkeypatch.setattr(rejection_agent, "get_rejection_suggestions", lambda ctx: {
        "status": "unavailable", "error": "x", "judgment_keywords": [], "suggested_reasons": [], "conflict_note": ""})
    res = rejection_agent.explain_rejection_workflow(r)
    assert "old_course_not_passed" in {k["id"] for k in res["keywords"]}


def test_validate_reasons_helpers():
    ctx = allowed_numbers("similarity 42.5 required 60")
    kept, dropped = validate_reasons([{"tone": "formal", "text": "42.5 of 60", "addresses_keywords": ["a"]},
                                       {"tone": "formal", "text": "77", "addresses_keywords": ["a"]}], {"a"}, ctx)
    assert len(kept) == 1 and len(dropped) == 1
    assert template_reasons({}, []) == []


# ---------- pipeline ----------
def test_pipeline_runs_rejection_only_for_reject(monkeypatch):
    d = _patch(monkeypatch)
    ok = pipeline.run_pipeline("R1", d)
    assert ok["eligibility"]["recommendation"] == "APPROVE"
    assert ok["rejection_explanation"]["status"] == "not_applicable"


# ---------- database lookups ----------
def test_course_lookup_sends_text_and_casts_column(monkeypatch):
    """Course_ID is stored as text in the database, so the lookup must compare as text."""
    from app.services import database

    seen = {}

    class _Result:
        def mappings(self):
            return self

        def first(self):
            return {"Course_ID": "1501279", "Course_Name": "Discrete Structures", "Credit_Hours": 3}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, query, params):
            seen["sql"], seen["params"] = str(query), params
            return _Result()

    monkeypatch.setattr(database, "get_connection", lambda: _Conn())
    assert database.fetch_course(1501279)["Course_Name"] == "Discrete Structures"
    assert seen["params"] == {"cid": "1501279"} and '"Course_ID"::text' in seen["sql"]


# ---------- study plan from the university website ----------
_HTML = """<table><tr><th>Course #</th><th>Title</th><th>Cr. Hr.</th></tr>
<tr><td>1501116</td><td>Programming I</td><td>4</td></tr>
<tr><td>1502111</td><td>Discrete Mathematics for Engineers</td><td>3</td><td>1440133</td></tr>
<tr><td>Total</td><td></td><td>7</td></tr></table>
<table><tr><td>1501116</td><td>Programming I</td><td>4</td></tr><tr><td>0201102</td><td>Arabic Language</td><td>3</td></tr></table>
<p>The degree requires a total of 132 Cr. Hrs.</p>"""


def test_parser_reads_every_course_row_once():
    from app.tools.fetch_study_plans import parse_courses, parse_total_credits
    courses = parse_courses(_HTML)
    assert [c["course_id"] for c in courses] == ["1501116", "1502111", "0201102"]
    assert courses[1]["credits"] == 3 and courses[1]["name"].startswith("Discrete")
    assert parse_total_credits(_HTML) == 132


def test_plan_lookup_from_json(monkeypatch, tmp_path):
    import json
    from app.tools import plan_tools
    f = tmp_path / "plans.json"
    f.write_text(json.dumps({"source": "x", "programs": {
        "Computer Engineering": {"match_names": ["Computer Engineering"], "total_credits": 132, "courses": [{"course_id": "1502111"}]},
        "Computer Science": {"match_names": ["Computer Science"], "total_credits": 123, "courses": []}}}))
    monkeypatch.setattr(plan_tools, "_PLANS_PATH", f)
    ce = "Bachelor of Science in Computer Engineering"
    assert plan_tools.course_in_study_plan(1, "1502111", ce)["in_plan"] is True
    assert plan_tools.course_in_study_plan(1, "1501116", ce)["in_plan"] is False
    # a plan with no courses loaded is "unverified", never "not in plan"
    assert plan_tools.load_study_plan("Computer Science") is None
    assert plan_tools.load_study_plan("Biology") is None


# ---------- program change first, then course equivalency ----------
def _plans_by_program(courses_by_program):
    return lambda p, c, n=None: {"in_plan": str(c) in courses_by_program.get(n, set()), "table": "x", "note": None}


def test_approved_program_change_uses_the_new_programs_plan(monkeypatch):
    change = {"completed": {"new_program_id": 9, "new_program_name": "Computer Engineering", "current_program_name": "Computer Science",
                            "status": "Completed"}, "pending": None}
    d = _patch(monkeypatch, program_change=change, plans=_plans_by_program({"Computer Engineering": {"1501116"}}))
    r = agent.evaluate_course_equivalency_request("R1", d)
    sp = r["evidence"]["constraints"]["study_plan"]
    assert sp["in_plan"] is True and sp["program_checked"] == "Computer Engineering"
    assert r["recommendation"] == "APPROVE"


def test_course_in_old_program_only_is_rejected_after_the_change(monkeypatch):
    change = {"completed": {"new_program_id": 9, "new_program_name": "Computer Engineering", "current_program_name": "Computer Science",
                            "status": "Completed"}, "pending": None}
    d = _patch(monkeypatch, program_change=change, plans=_plans_by_program({"Computer Science": {"1501116"}, "Computer Engineering": set()}))
    assert agent.evaluate_course_equivalency_request("R1", d)["recommendation"] == "REJECT"


def test_pending_program_change_sends_the_request_to_manual_review(monkeypatch):
    change = {"completed": None, "pending": {"new_program_id": 9, "new_program_name": "Computer Engineering",
                                            "current_program_name": "Computer Science", "status": "In Progress"}}
    d = _patch(monkeypatch, program_change=change, plans=_plans_by_program({"Computer Science": set(), "Computer Engineering": {"1501116"}}))
    r = agent.evaluate_course_equivalency_request("R1", d)
    sp = r["evidence"]["constraints"]["study_plan"]
    assert sp["in_plan"] is None and "not been approved" in sp["note"]
    assert r["recommendation"] == "MANUAL_REVIEW"


def test_no_program_change_and_course_outside_plan_is_rejected(monkeypatch):
    d = _patch(monkeypatch, program_change={"completed": None, "pending": None}, plans=_plans_by_program({"Computer Science": set()}))
    assert agent.evaluate_course_equivalency_request("R1", d)["recommendation"] == "REJECT"


def test_rejected_program_change_rejects_with_its_own_reason(monkeypatch):
    change = {"completed": None, "pending": None,
              "rejected": {"new_program_id": 9, "new_program_name": "Computer Engineering",
                           "current_program_name": "Computer Science", "status": "Rejected"}}
    d = _patch(monkeypatch, program_change=change, plans=_plans_by_program({"Computer Science": set(), "Computer Engineering": {"1501116"}}))
    r = agent.evaluate_course_equivalency_request("R1", d)
    sp = r["evidence"]["constraints"]["study_plan"]
    assert r["recommendation"] == "REJECT"
    assert sp["in_plan"] is False and sp["program_change"]["result"] == "rejected" and "was rejected" in sp["note"]
    from app.tools.rejection_keyword_tools import verified_facts
    assert "program_change_rejected" in verified_facts(r)


def test_unrelated_pending_change_is_not_attached(monkeypatch):
    change = {"completed": None, "rejected": None,
              "pending": {"new_program_id": 5, "new_program_name": "Artificial Intelligence",
                          "current_program_name": "Computer Science", "status": "In Progress"}}
    d = _patch(monkeypatch, program_change=change, plans=_plans_by_program({"Computer Science": set(), "Artificial Intelligence": set()}))
    sp = agent.evaluate_course_equivalency_request("R1", d)["evidence"]["constraints"]["study_plan"]
    assert sp["in_plan"] is False and sp["program_change"] is None


# ---------- any number of syllabus files per side ----------
def test_several_files_for_one_side_are_combined(tmp_path):
    from app.tools import syllabus_tools
    (tmp_path / "old_course_part1.txt").write_text("Course topics: sets, logic, proofs, relations and functions")
    (tmp_path / "old_course_part2.txt").write_text("Assessment: one midterm exam, a final exam and weekly quizzes")
    (tmp_path / "new_course.txt").write_text("Course topics: sets and logic, taught in lectures")
    s = syllabus_tools.get_syllabi("X", local_dir=str(tmp_path))
    assert s["old"]["file_count"] == 2 and s["old"]["readable"] is True
    assert "proofs" in s["old"]["text"] and "midterm" in s["old"]["text"]
    assert s["new"]["file_count"] == 1


def test_one_unreadable_file_does_not_hide_the_others(tmp_path):
    from app.tools import syllabus_tools
    (tmp_path / "old_course_a.txt").write_text("Course topics: graphs, trees and counting techniques")
    (tmp_path / "old_course_b.txt").write_bytes(b"")
    (tmp_path / "new_course.txt").write_text("Course topics: graphs, trees and counting techniques")
    s = syllabus_tools.get_syllabi("X", local_dir=str(tmp_path))
    assert s["old"]["readable"] is True and "graphs" in s["old"]["text"]


def test_storage_paths_differ_per_file_name():
    from app.services import storage
    a = storage.object_path("R1", "old", "Syllabus part 1.pdf")
    b = storage.object_path("R1", "old", "Syllabus part 2.pdf")
    assert a != b and a.startswith("R1/old_course_") and a.endswith(".pdf")
    assert storage.object_path("R1", "new", "x.docx") == "R1/new_course_x.docx"