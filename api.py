"""Small test API + test page for the Course Equivalency agents.

    python -m uvicorn api:app --reload --port 8001
    then open http://localhost:8001

Endpoints
  POST /compare                 two syllabus files -> similarity (no database request needed)
  POST /upload/{request_id}     any number of syllabus files (side = old | new) into the Supabase bucket
  GET  /pipeline/{request_id}   Agent 8, then Agent 9 if the request is rejected
"""

import mimetypes
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.exceptions import AgentError
from app.pipeline import run_pipeline
from app.services.gemini import analyze_syllabi
from app.services.storage import upload_syllabus
from app.tools.document_reader import read_document
from app.tools.policy import load_equivalency_policy
from app.tools.syllabus_tools import compute_similarity, text_overlap_percent

MAX_BYTES = 10 * 1024 * 1024
app = FastAPI(title="Course Equivalency agents - test API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def page():
    return FileResponse(Path(__file__).parent / "test_page.html")


async def _read(f: UploadFile) -> bytes:
    data = await f.read()
    if len(data) > MAX_BYTES:
        raise HTTPException(413, f"{f.filename} is larger than 10 MB.")
    return data


@app.post("/compare")
async def compare(old_file: UploadFile = File(...), new_file: UploadFile = File(...),
                  old_credits: int | None = Form(None), new_credits: int | None = Form(None)):
    policy = load_equivalency_policy()
    try:
        old_text = read_document(await _read(old_file), old_file.filename or "old.txt", policy["max_syllabus_chars"])
        new_text = read_document(await _read(new_file), new_file.filename or "new.txt", policy["max_syllabus_chars"])
    except AgentError as exc:
        raise HTTPException(400, str(exc))
    res = analyze_syllabi(old_text, new_text)
    if res["analysis"] is None:
        raise HTTPException(503, f"Syllabus analysis failed: {res['error']}")
    sim = compute_similarity(res["analysis"], old_credits, new_credits, policy)
    band = sim["score_band"]
    return {
        "similarity": sim,
        "preview_label": {"EQUIVALENT_RANGE": "EQUIVALENT", "REVIEW_RANGE": "FURTHER_REVIEW", "BELOW_RANGE": "NON_EQUIVALENT"}.get(band),
        "text_overlap_percent": text_overlap_percent(old_text, new_text),
        "extracted": res["analysis"],
        "note": "Preview only. A real request also checks the transcript grade and is decided by the HOD/administrator.",
    }


@app.post("/upload/{request_id}")
async def upload(request_id: str, side: str = Form(...), files: list[UploadFile] = File(default=[]),
                 file: list[UploadFile] = File(default=[])):
    """Any number of syllabus files for one side. `files` and `file` are both accepted."""
    if side not in ("old", "new"):
        raise HTTPException(400, "side must be 'old' or 'new'.")
    uploads = list(files) + list(file)
    if not uploads:
        raise HTTPException(400, "Choose at least one file.")
    paths = []
    for f in uploads:
        data = await _read(f)
        try:
            paths.append(upload_syllabus(request_id, side, f.filename or "syllabus.pdf", data,
                                         f.content_type or mimetypes.guess_type(f.filename or "")[0] or "application/octet-stream"))
        except AgentError as exc:
            raise HTTPException(502, str(exc))
    return {"paths": paths, "path": paths[0]}


@app.get("/pipeline/{request_id}")
def pipeline(request_id: str):
    try:
        return run_pipeline(request_id)
    except AgentError as exc:
        raise HTTPException(400, str(exc))