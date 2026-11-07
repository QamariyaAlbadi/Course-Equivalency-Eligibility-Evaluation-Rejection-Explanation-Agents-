"""Supabase Storage access for course syllabi.

Files live in a private bucket at <Request_ID>/old_course_<name>.<ext> and
<Request_ID>/new_course_<name>.<ext> (any number per side; plain old_course.<ext> still works). Uses the Storage HTTP API, so no extra dependency is needed
and the agent's read-only SQL connection is left untouched.
"""

import re
from urllib.parse import quote

import requests

from app import config
from app.exceptions import StorageError

_TIMEOUT = 20


def _key() -> str | None:
    return config.SUPABASE_SERVICE_ROLE_KEY or config.SUPABASE_ANON_KEY


def is_configured() -> bool:
    return bool(config.SUPABASE_URL and _key())


def _require():
    if not is_configured():
        raise StorageError("SUPABASE_URL and SUPABASE_ANON_KEY must be set in .env to use syllabus storage.")


def _headers(extra: dict | None = None) -> dict:
    key = _key()
    h = {"apikey": key}
    # Legacy anon/service_role keys are JWTs and are also sent as a Bearer token. The new
    # sb_publishable_ / sb_secret_ keys are NOT JWTs, so they go in the apikey header only.
    if key and not key.startswith("sb_"):
        h["Authorization"] = f"Bearer {key}"
    h.update(extra or {})
    return h


def safe_extension(filename: str) -> str:
    ext = re.sub(r"[^a-z0-9]", "", (filename.rsplit(".", 1)[-1] if "." in filename else "").lower())
    return ext or "bin"


def object_path(request_id: str, side: str, filename: str) -> str:
    """<Request_ID>/<side>_course_<file name>.<ext>. Any number of files can be stored for each
    side: a different file name gives a different path, the same file name replaces that file."""
    if side not in ("old", "new"):
        raise StorageError("side must be 'old' or 'new'.")
    stem = filename.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_")[:60]
    return f"{request_id}/{side}_course_{stem}.{safe_extension(filename)}" if stem else f"{request_id}/{side}_course.{safe_extension(filename)}"


def _object_url(path: str) -> str:
    return f"{config.SUPABASE_URL}/storage/v1/object/{config.SYLLABUS_BUCKET}/{quote(path)}"


def upload_syllabus(request_id: str, side: str, filename: str, data: bytes, mime_type: str) -> str:
    _require()
    path = object_path(request_id, side, filename)
    try:
        r = requests.post(_object_url(path), data=data, timeout=_TIMEOUT,
                          headers=_headers({"Content-Type": mime_type, "x-upsert": "true"}))
    except requests.RequestException as exc:
        raise StorageError(f"Could not reach Supabase Storage: {exc}") from exc
    if r.status_code >= 300:
        raise StorageError(f"Upload of {filename!r} failed ({r.status_code}): {r.text[:200]}")
    return path


def list_syllabi(request_id: str) -> list[str]:
    """Names of the files stored under <Request_ID>/ ."""
    _require()
    url = f"{config.SUPABASE_URL}/storage/v1/object/list/{config.SYLLABUS_BUCKET}"
    try:
        r = requests.post(url, json={"prefix": request_id, "limit": 1000}, headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageError(f"Could not reach Supabase Storage: {exc}") from exc
    if r.status_code >= 300:
        raise StorageError(f"Could not list syllabi ({r.status_code}): {r.text[:200]}")
    return [f"{request_id}/{o['name']}" for o in r.json() if o.get("name") and o.get("id")]


def download_syllabus(path: str) -> bytes:
    _require()
    try:
        r = requests.get(_object_url(path), headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageError(f"Could not reach Supabase Storage: {exc}") from exc
    if r.status_code >= 300:
        raise StorageError(f"Download of {path!r} failed ({r.status_code}): {r.text[:200]}")
    return r.content


def signed_url(path: str, expires_in: int = 3600) -> str:
    _require()
    url = f"{config.SUPABASE_URL}/storage/v1/object/sign/{config.SYLLABUS_BUCKET}/{quote(path)}"
    try:
        r = requests.post(url, json={"expiresIn": expires_in}, headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageError(f"Could not reach Supabase Storage: {exc}") from exc
    if r.status_code >= 300:
        raise StorageError(f"Could not sign {path!r} ({r.status_code}): {r.text[:200]}")
    return f"{config.SUPABASE_URL}/storage/v1{r.json()['signedURL']}"