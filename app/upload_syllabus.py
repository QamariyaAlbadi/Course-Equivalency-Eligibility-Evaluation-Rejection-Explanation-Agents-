"""Uploads syllabus files for a request to the Supabase bucket (what the professor's upload will do).
Any number of files can be uploaded for each side.

    python -m app.upload_syllabus <request_id> old|new <file1> [file2 file3 ...]
"""

import mimetypes
import sys
from pathlib import Path

from app.exceptions import AgentError
from app.services.storage import upload_syllabus

MAX_BYTES = 10 * 1024 * 1024  # per file, the same limit as the storage bucket


def main() -> None:
    if len(sys.argv) < 4 or sys.argv[2] not in ("old", "new"):
        print(__doc__)
        sys.exit(1)
    rid, side, files = sys.argv[1], sys.argv[2], [Path(f) for f in sys.argv[3:]]
    failed = 0
    for file in files:
        try:
            data = file.read_bytes()
            if len(data) > MAX_BYTES:
                raise AgentError("File is larger than 10 MB.")
            path = upload_syllabus(rid, side, file.name, data, mimetypes.guess_type(file.name)[0] or "application/octet-stream")
            print(f"Uploaded {file.name} -> {path}")
        except (AgentError, OSError) as exc:
            failed += 1
            print(f"ERROR ({file.name}): {exc}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()