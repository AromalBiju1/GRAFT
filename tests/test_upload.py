import asyncio
from unittest.mock import Mock

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_pdf_upload_indexes_raw_text_once(tmp_path, monkeypatch):
    import api.uploads as u
    import indexing.ingest as ingest
    import indexing.pipeline as pipeline

    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n")
    fid = "test_pdf_upload"
    u.FILES[fid] = {
        "id": fid,
        "name": "doc.pdf",
        "size": path.stat().st_size,
        "path": str(path),
        "status": "uploaded",
        "error": None,
    }
    seen = {}

    monkeypatch.setattr(ingest, "parse_document", lambda _path: "raw source text")
    monkeypatch.setattr(
        ingest,
        "ingest_document",
        Mock(side_effect=AssertionError("PDF text must not be chunked before indexing")),
    )

    def fake_index(text, *, document_id, source=None):
        seen["text"] = text
        seen["document_id"] = document_id
        seen["source"] = source

    monkeypatch.setattr(pipeline, "index_document", fake_index)

    try:
        asyncio.run(u._process(fid))
        assert u.FILES[fid]["status"] == "ready"
    finally:
        u.FILES.pop(fid, None)

    assert seen == {
        "text": "raw source text",
        "document_id": fid,
        "source": "doc.pdf",
    }


def test_rejects_wrong_type():
    r = client.post("/api/files", files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415

def test_rejects_spoofed_pdf():
    r = client.post("/api/files", files={"file": ("a.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 415

def test_rejects_too_large(monkeypatch):
    import api.uploads as u
    monkeypatch.setattr(u, "MAX_UPLOAD_MB", 0)
    r = client.post("/api/files", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 413

def test_accepts_txt():
    r = client.post("/api/files", files={"file": ("a.txt", b"hello world", "text/plain")})
    assert r.status_code == 201 and r.json()["name"] == "a.txt"
