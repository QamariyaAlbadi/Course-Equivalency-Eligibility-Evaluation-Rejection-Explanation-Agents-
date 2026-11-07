"""Document Reader Tool: turns an uploaded syllabus (PDF, DOCX, TXT/MD) into plain text."""

import io

from app.exceptions import DocumentReadError

SUPPORTED = ("pdf", "docx", "txt", "md")


def read_document(data: bytes, filename: str, max_chars: int = 60000) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "pdf":
        text = _read_pdf(data)
    elif ext == "docx":
        text = _read_docx(data)
    elif ext in ("txt", "md"):
        text = data.decode("utf-8", errors="replace")
    else:
        raise DocumentReadError(f"Unsupported syllabus format {ext!r}. Use PDF, DOCX or TXT.")
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(text) < 40:
        raise DocumentReadError(f"No readable text found in {filename!r} (it may be a scanned image).")
    return text[:max_chars]


def _read_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as exc:  # noqa: BLE001
        raise DocumentReadError(f"Could not read the PDF: {exc}") from exc


def _read_docx(data: bytes) -> str:
    try:
        import docx
        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for table in d.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text.strip() for c in row.cells))
        return "\n".join(parts)
    except Exception as exc:  # noqa: BLE001
        raise DocumentReadError(f"Could not read the DOCX: {exc}") from exc
