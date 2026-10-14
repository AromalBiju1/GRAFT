from fastapi.testclient import TestClient
from api.main import app
client = TestClient(app)

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