from pathlib import Path

from fastapi.testclient import TestClient

import api
from tests.test_agent import SAMPLES as SAMPLE_ROOT
from tests.test_agent import _analysis

SAMPLES = SAMPLE_ROOT / "equivalent_pair"
client = TestClient(api.app)


def test_page_is_served():
    r = client.get("/")
    assert r.status_code == 200 and "Course Equivalency agents" in r.text


def test_compare_two_files(monkeypatch):
    monkeypatch.setattr(api, "analyze_syllabi", lambda o, n: {"status": "ok", "error": None, "analysis": _analysis()})
    files = {"old_file": ("old_course.txt", (SAMPLES / "old_course.txt").read_bytes()),
             "new_file": ("new_course.txt", (SAMPLES / "new_course.txt").read_bytes())}
    r = client.post("/compare", files=files, data={"old_credits": "3", "new_credits": "3"})
    assert r.status_code == 200
    j = r.json()
    assert j["similarity"]["overall_similarity_percent"] == 100.0 and j["preview_label"] == "EQUIVALENT"


def test_compare_rejects_unreadable_file():
    files = {"old_file": ("a.exe", b"x"), "new_file": ("b.txt", b"x" * 100)}
    assert client.post("/compare", files=files).status_code == 400


def test_compare_reports_gemini_failure(monkeypatch):
    monkeypatch.setattr(api, "analyze_syllabi", lambda o, n: {"status": "unavailable", "error": "no key", "analysis": None})
    files = {"old_file": ("o.txt", b"x" * 100), "new_file": ("n.txt", b"y" * 100)}
    r = client.post("/compare", files=files)
    assert r.status_code == 503 and "no key" in r.json()["detail"]


def test_upload_validates_side():
    r = client.post("/upload/R1", data={"side": "middle"}, files={"file": ("a.txt", b"x")})
    assert r.status_code == 400
