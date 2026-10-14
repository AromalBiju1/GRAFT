from fastapi.testclient import TestClient
from api.main import app
client = TestClient(app)

def test_rejects_wrong_type():
    """Reject an upload with an unsupported extension and MIME type."""
    r = client.post("/api/files", files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415

def test_rejects_spoofed_pdf():
    """Reject a PDF upload whose content lacks the PDF signature."""
    r = client.post("/api/files", files={"file": ("a.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 415

def test_rejects_too_large(monkeypatch):
    """Reject an upload that exceeds the configured size limit."""
    import api.uploads as u
    monkeypatch.setattr(u, "MAX_UPLOAD_MB", 0)
    r = client.post("/api/files", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 413

def test_accepts_txt():
    """Accept a text upload and preserve its original name in the response."""
    r = client.post("/api/files", files={"file": ("a.txt", b"hello world", "text/plain")})
    assert r.status_code == 201 and r.json()["name"] == "a.txt"
