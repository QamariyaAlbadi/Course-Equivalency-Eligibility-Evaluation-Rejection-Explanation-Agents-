# Academic Eligibility Evaluation & Rejection Explanation Agents For Course Equivalency

Two agents that help the HOD/administrator review a Course Equivalency request:

| Agent | Job |
|---|---|
| **Agent 8 - Academic Eligibility Evaluation** | Reads the request, the student's transcript and the two course syllabi, calculates a similarity score, and recommends `APPROVE` (equivalent), `REJECT` (non-equivalent) or `MANUAL_REVIEW` (further review). |
| **Agent 9 - Rejection Explanation** | Runs only when Agent 8 recommends `REJECT`. Produces rejection keywords and three suggested reasons based on the similarity percentage, the missing topics and the credit-hour difference. |

The agents only recommend. **The final decision is made by the HOD/administrator.**

## Design principle

> Code gathers and calculates the evidence. Gemini reads the syllabi and judges. The staff member decides.

Gemini never queries the database or the web. It receives text and evidence and returns
structured JSON (pydantic `response_schema`). Every number shown to the staff member is
calculated by Python.

## Agent 8 workflow

```
Request ID
 1. Request details          Request + Course_Equivalency (old course, new course, reason)
 2. Student + transcript     Student, Takes
 3. Course details           Course (name, credit hours); academic level from the course number
 4. Old course passed?       approved grade policy (D or higher passes, NULL = not completed)
 5. Syllabus retrieval       private Supabase bucket "course-syllabi" -> Document Reader (PDF / DOCX / TXT)
 6. Syllabus analysis        Gemini extracts topics, learning outcomes, credit hours, assessment structure
                             and says which topics/outcomes of the REQUESTED course the COMPLETED course covers
 7. Similarity score         Python calculates the percentages (weights in equivalency_policy.json)
 8. Recommendation           Gemini applies the decision rules to the verified evidence
 9. Guardrails               an unsafe APPROVE is turned into MANUAL_REVIEW / REJECT (never the other direction)
```

### University constraints (verified by code before any similarity is considered)

From the team's Course Equivalency Constraints document, kept in `app/knowledge_base/equivalency_policy.json` and `grade_policy.json`:

| Constraint | Result if not met |
|---|---|
| Completed course grade is at least **C+** | `REJECT` |
| Requested course is in the student's **study plan** | `REJECT` |
| Completed course credit hours are **equal to or greater than** the requested course | `REJECT` |
| Total transferred credits stay within the limit: **60** hours (**70** for Engineering, Health Sciences, Pharmacy) and no more than **50%** of the study plan | `REJECT` |

If one of these cannot be verified (for example the study plan table cannot be found), the request
goes to `MANUAL_REVIEW`; it is never approved automatically. An approved course is recorded as **TR**
and does not count in the CGPA (shown in the report). Community-college limits (40 to 50 hours) are
not checked because the institution type is not in the database.

The grade is taken from the transcript; if the course is not there (for example an external course),
a grade written in the request is used and labelled `grade_source: request`.

### Similarity score

The completed course must cover what the requested course teaches, so coverage is measured
over the **requested** course's topics and learning outcomes.

| Component | Weight | How it is calculated |
|---|---|---|
| Topics | 50% | requested topics covered by the completed course (full = 1, partial = 0.5) |
| Learning outcomes | 25% | same method |
| Assessment structure | 10% | 100 minus half the total difference in assessment percentages |
| Credit hours | 15% | `min(completed / requested, 1)` |

Bands (editable in `app/knowledge_base/equivalency_policy.json`; **draft values, confirm with the department**):

* 80% or more: `EQUIVALENT_RANGE` (approval expected)
* 60% to 79%: `REVIEW_RANGE` (further review)
* below 60%: `BELOW_RANGE` (rejection expected)

If a component cannot be measured it is left out and the remaining weights are rescaled. If
the topics cannot be measured there is no score and the result is `MANUAL_REVIEW`.

### What goes to manual review (never guessed)

* a syllabus is missing or unreadable: the report says which professor upload is needed
* the completed course is not in the transcript, or its grade is not covered by the policy
* Gemini is unavailable (the evidence is attached)

A failing grade on the completed course is always `REJECT`.

## Agent 9 workflow

Input: Agent 8's result. Anything other than `REJECT` returns `not_applicable`.

* **Verified keywords** (from code): `old_course_not_passed`, `similarity_below_threshold`,
  `credit_hours_lower`, `level_mismatch`, `missing_course_topics`
* **Judged keywords** (Gemini, closed list): `learning_outcomes_gap`,
  `assessment_structure_differs`, `different_course_focus`, `insufficient_syllabus_detail`
* Three suggested reasons: formal, concise, supportive
* Validation drops unknown keywords, reasons without a valid keyword, and reasons that contain
  a number not present in the evidence. If nothing valid remains, template reasons are used.

Statuses: `ok`, `fallback`, `unavailable`, `needs_manual_explanation`, `not_applicable`.

## Files

| File | Purpose |
|---|---|
| `app/agent.py` | Agent 8 workflow, guardrails, report |
| `app/rejection_agent.py` | Agent 9 workflow, report |
| `app/pipeline.py` | Runs Agent 8, then Agent 9 when the request is rejected |
| `app/upload_syllabus.py` | Uploads a syllabus to the bucket (what the professor's upload does) |
| `app/services/gemini.py`, `rejection_gemini.py` | Gemini prompts and response schemas |
| `app/services/storage.py` | Supabase Storage access for syllabi |
| `app/services/database.py` | Read-only database connection |
| `app/tools/request_tools.py`, `student_tools.py`, `course_tools.py` | SQL query tools |
| `app/tools/document_reader.py` | Document Reader tool |
| `app/tools/syllabus_tools.py` | Syllabus retrieval and Syllabus Comparison (scoring) tool |
| `app/tools/policy.py` | Loads the equivalency and grade policies, minimum-grade check |
| `app/tools/plan_tools.py` | Study plan, college, and transferred-credit lookups |
| `app/tools/fetch_study_plans.py` | Reads the study plan tables from the university degree pages into `knowledge_base/study_plans.json` |
| `app/tools/rejection_keyword_tools.py`, `explanation_generator.py` | Keywords, validation, template reasons |
| `app/knowledge_base/` | `equivalency_policy.json`, `grade_policy.json`, `rejection_keywords.json` |
| `supabase_syllabus_storage.sql` | Creates the private bucket and its policies |
| `api.py`, `test_page.html` | Test API and page: upload two syllabi and compare, or run both agents for a request |
| `tests/` | 47 mocked tests |

## Setup

```
python -m pip install -r requirements.txt
copy .env.example .env      # fill in DATABASE_URL, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_ANON_KEY
```

Load the study plans once (and again whenever the university changes them):

```
python -m app.tools.fetch_study_plans
```

This fills `app/knowledge_base/study_plans.json` from the Computer Science and Computer Engineering degree pages. A program whose plan has no courses loaded is treated as *not verified* (manual review), never as "course not in the plan". Programs are matched to the student's `Program_Name`; add more degree pages to `PAGES` in the script.

Run `supabase_syllabus_storage.sql` once in the Supabase SQL editor (once per Supabase project).

## Run

```
python -m app.upload_syllabus <request_id> old <path to the completed course syllabus>
python -m app.upload_syllabus <request_id> new <path to the requested course syllabus>

python -m app.pipeline <request_id>                 # Agent 8, then Agent 9 if rejected
python -m app.pipeline --student <Student_ID>       # all of the student's requests
python -m app.pipeline <request_id> --syllabi <folder>   # folder with old_course.* and new_course.*
python -m app.agent <request_id>                    # Agent 8 only
python -m app.rejection_agent <request_id>          # Agent 9 only
python -m uvicorn api:app --reload --port 8001      # test page at http://localhost:8001
python -m pytest                                    # tests (no database or Gemini needed)
```

## Limitations

* Similarity depends on Gemini's reading of the syllabi; a very short or scanned (image-only) syllabus is sent to manual review.
* Thresholds and weights are draft values.
* The academic level is taken from the course number (e.g. 1501116 is a 100-level course).
* The storage policies are open for development and should be restricted before the final demo.
* Not yet connected to the frontend.