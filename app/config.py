import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
# Models tried in order when the preferred one is overloaded (503), rate limited (429) or not found (404).
GEMINI_FALLBACK_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_FALLBACK_MODELS", "gemini-3.8-flash,gemini-3.7-flash,gemini-3.5-flash").split(",") if m.strip()]
GEMINI_MODELS = [GEMINI_MODEL] + [m for m in GEMINI_FALLBACK_MODELS if m != GEMINI_MODEL]

# Supabase Storage (course syllabi). The public "anon" key is used by default, so the
# bucket policies must allow it (see supabase_syllabus_storage.sql). If
# SUPABASE_SERVICE_ROLE_KEY is also set it is used instead; that key bypasses row-level
# security, is BACKEND-ONLY, and must never be put in the frontend or committed to git.
SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SYLLABUS_BUCKET = os.getenv("SYLLABUS_BUCKET", "course-syllabi")