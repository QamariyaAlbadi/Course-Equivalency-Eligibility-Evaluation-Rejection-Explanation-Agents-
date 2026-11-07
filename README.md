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
 5. Syllabus retrieval       private Supabase bucket "course-syllabi" -> Document Reader (PDF / DOCX / TXT);
                             one or more files per side, combined
 6. Syllabus analysis        Gemini extracts topics, learning outcomes, credit hours, assessment structure
                             and says which topics/outcomes of the REQUESTED course the COMPLETED course covers,
                             quoting the syllabus line that supports each match
 7. Match verification       Python checks every quote really appears in the syllabus text; unconfirmed matches are dropped
 8. Similarity score         Python calculates the percentages (weights in equivalency_policy.json)
 9. Recommendation           Gemini applies the decision rules to the verified evidence
10. Guardrails               an unsafe APPROVE is turned into MANUAL_REVIEW / REJECT (never the other direction)
```

### University constraints (verified by code before any similarity is considered)

From the team's Course Equivalency Constraints document, kept in `app/knowledge_base/equivalency_policy.json` and `grade_policy.json`:

| Constraint | Result if not met |
|---|---|
| Completed course grade is at least **C+** | `REJECT` |
| Requested course is in the student's **study plan** | `REJECT` |
| Completed course credit hours are **equal to or greater than** the requested course | `REJECT` |
| Total transferred credits stay within the limit: **60** hours (**70** for Engineering, Health Sciences, Pharmacy) and no more than **50%** of the study plan | `REJECT` |

### Program Change first, then Course Equivalency

A student normally submits a Program Change and then a Course Equivalency for the new program.
The study plan checked depends on the student's Program Change requests:

| Program Change status | Study plan checked |
|---|---|
| Completed (approved) | the **new** program's plan |
| In Progress | cannot be verified yet, so `MANUAL_REVIEW` |
| Rejected | ignored: the student stays in the current program. If the course belongs only to the refused program, the request is rejected with the keyword `program_change_rejected` |
| none | the student's current program |

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

The score is directional (coverage of the requested course), so swapping the two syllabi can give a different percentage by design.
Gemini's topic splitting can move the result by a few points between runs; matches whose quotes cannot be
found in the syllabus are removed, and if more than `max_unverified_match_percent` (30%) are removed the
request goes to `MANUAL_REVIEW`.

If a component cannot be measured it is left out and the remaining weights are rescaled. If
the topics cannot be measured there is no score and the result is `MANUAL_REVIEW`.

### What goes to manual review (never guessed)

* a syllabus is missing or unreadable: the report says which syllabus upload (HOD/instructor/admin) is needed
* the completed course is not in the transcript, or its grade is not covered by the policy
* Gemini is unavailable (the evidence is attached). Gemini calls are retried and fall back through
  `GEMINI_FALLBACK_MODELS` when a model is overloaded (503/429)
* many of Gemini's topic matches could not be confirmed in the syllabus text
* the student has a Program Change still in progress

A failing grade on the completed course is always `REJECT`.

## Agent 9 workflow

Input: Agent 8's result. Anything other than `REJECT` returns `not_applicable`.

* **Verified keywords** (from code): `old_course_not_passed`, `similarity_below_threshold`,
  `credit_hours_lower`, `level_mismatch`, `missing_course_topics`, `program_change_rejected`
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
| `app/upload_syllabus.py` | Uploads one or more syllabus files to the bucket (what the HOD/instructor/admin upload does) |
| `app/services/gemini.py`, `rejection_gemini.py` | Gemini prompts and response schemas |
| `app/services/llm_client.py` | Retry and model fallback for Gemini calls |
| `app/tools/match_verifier.py` | Checks Gemini's quoted evidence against the syllabus text |
| `app/services/storage.py` | Supabase Storage access for syllabi |
| `app/services/database.py` | Read-only database connection |
| `app/tools/request_tools.py`, `student_tools.py`, `course_tools.py` | SQL query tools |
| `app/tools/document_reader.py` | Document Reader tool |
| `app/tools/syllabus_tools.py` | Syllabus retrieval and Syllabus Comparison (scoring) tool |
| `app/tools/policy.py` | Loads the equivalency and grade policies, minimum-grade check |
| `app/tools/plan_tools.py` | Study plan (with Program Change), college, and transferred-credit lookups |
| `app/tools/fetch_study_plans.py` | Reads the study plan tables from the university degree pages into `knowledge_base/study_plans.json` |
| `app/tools/rejection_keyword_tools.py`, `explanation_generator.py` | Keywords, validation, template reasons |
| `app/knowledge_base/` | `equivalency_policy.json`, `grade_policy.json`, `rejection_keywords.json` |
| `supabase_syllabus_storage.sql` | Creates the private bucket and its policies |
| `api.py`, `test_page.html` | Test API and page: upload two syllabi and compare, or run both agents for a request |
| `tests/` | 70 mocked tests |

## Setup

```
python -m pip install -r requirements.txt
copy .env.example .env      # fill in DATABASE_URL, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_ANON_KEY (optional: GEMINI_MODEL, GEMINI_FALLBACK_MODELS)
```

Load the study plans once (and again whenever the university changes them):

```
python -m app.tools.fetch_study_plans
```

This fills `app/knowledge_base/study_plans.json` from the Computer Science and Computer Engineering degree pages. A program whose plan has no courses loaded is treated as *not verified* (manual review), never as "course not in the plan". Programs are matched to the student's `Program_Name`; add more degree pages to `PAGES` in the script.

Run `supabase_syllabus_storage.sql` once in the Supabase SQL editor (once per Supabase project).

## Run

```
python -m app.upload_syllabus <request_id> old <file1> [file2 ...]   # completed course syllabus (up to 10 MB per file)
python -m app.upload_syllabus <request_id> new <file1> [file2 ...]   # requested course syllabus

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
* Thresholds and weights are draft values; confirm them with the department.
* Only Computer Science and Computer Engineering study plans are loaded.
* The academic level is taken from the course number (e.g. 1501116 is a 100-level course).
* The storage policies are open for development and should be restricted before the final demo.
* Not yet connected to the frontend.