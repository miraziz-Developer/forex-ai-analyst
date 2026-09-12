"""PDF knowledge ingestion and retrieval stored in the existing Turso database."""
from __future__ import annotations

from hashlib import sha256
import io
import re
from datetime import datetime, timezone

import storage

_CREATE_DOCUMENTS = """CREATE TABLE IF NOT EXISTS knowledge_documents (
 id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_file_id TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
 file_name TEXT NOT NULL, page_count INTEGER NOT NULL, uploaded_by TEXT NOT NULL, created_at TEXT NOT NULL
);"""
_CREATE_CHUNKS = """CREATE TABLE IF NOT EXISTS knowledge_chunks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL, page_number INTEGER NOT NULL,
 chunk_index INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(document_id, page_number, chunk_index)
);"""


def init_db() -> None:
    storage._execute(_CREATE_DOCUMENTS)
    storage._execute(_CREATE_CHUNKS)


def _chunks(text: str, width: int = 1400) -> list[str]:
    clean = re.sub(r"\s+", " ", text).strip()
    return [clean[index:index + width] for index in range(0, len(clean), width) if clean[index:index + width].strip()]


def ingest_pdf(data: bytes, file_name: str, telegram_file_id: str, uploaded_by: str) -> dict:
    if not data.startswith(b"%PDF"):
        raise ValueError("faqat haqiqiy PDF fayl qabul qilinadi")
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("PDF 20 MB dan kichik bo‘lishi kerak")
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    digest, now = sha256(data).hexdigest(), datetime.now(timezone.utc).isoformat()
    existing = storage._rows_as_dicts(storage._execute("SELECT id FROM knowledge_documents WHERE sha256 = ?", [digest]))
    if existing:
        return {"id": existing[0]["id"], "duplicate": True, "pages": len(reader.pages), "chunks": 0}
    result = storage._execute("""INSERT INTO knowledge_documents
        (telegram_file_id, sha256, file_name, page_count, uploaded_by, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
        [telegram_file_id, digest, file_name[:255], len(reader.pages), uploaded_by, now])
    document_id = int(result.get("last_insert_rowid", 0))
    count = 0
    for page_number, page in enumerate(reader.pages, 1):
        for index, content in enumerate(_chunks(page.extract_text() or "")):
            storage._execute("""INSERT INTO knowledge_chunks (document_id, page_number, chunk_index, content, created_at)
                              VALUES (?, ?, ?, ?, ?)""", [document_id, page_number, index, content, now])
            count += 1
    return {"id": document_id, "duplicate": False, "pages": len(reader.pages), "chunks": count}


def search(query: str, limit: int = 5) -> list[dict]:
    terms = [word for word in re.findall(r"[\w-]{3,}", query.lower())[:8]]
    if not terms:
        return []
    clause = " OR ".join("lower(k.content) LIKE ?" for _ in terms)
    result = storage._execute(f"""SELECT d.id AS document_id, d.file_name, k.page_number, k.content
        FROM knowledge_chunks k JOIN knowledge_documents d ON d.id = k.document_id
        WHERE {clause} ORDER BY k.id DESC LIMIT ?""", [*[f"%{term}%" for term in terms], min(max(limit, 1), 10)])
    return storage._rows_as_dicts(result)


def documents(limit: int = 30) -> list[dict]:
    result = storage._execute("SELECT id, file_name, page_count, uploaded_by, created_at FROM knowledge_documents ORDER BY id DESC LIMIT ?", [limit])
    return storage._rows_as_dicts(result)