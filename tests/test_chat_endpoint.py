import json
import time

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def parse_sse_events(response_text: str) -> list[tuple[str, dict]]:
    events = []
    blocks = response_text.strip().split("\n\n")
    for block in blocks:
        if not block.strip():
            continue
        lines = block.split("\n")
        event_name = None
        data = None
        for line in lines:
            if line.startswith("event: "):
                event_name = line[len("event: ") :].strip()
            elif line.startswith("data: "):
                raw_data = line[len("data: ") :].strip()
                try:
                    data = json.loads(raw_data)
                except Exception:
                    data = raw_data
        if event_name:
            events.append((event_name, data))
    return events


def test_chat_corpus_query():
    r = client.post("/api/chat", json={"prompt": "What is GRAFT?", "file_ids": []})
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]

    events = parse_sse_events(r.text)
    event_names = [e[0] for e in events]

    assert "meta" in event_names
    assert "token" in event_names
    assert "done" in event_names

    meta_event = next(e[1] for e in events if e[0] == "meta")
    assert "routing" in meta_event
    assert "complexity" in meta_event["routing"]
    assert "evidence" in meta_event
    assert isinstance(meta_event["evidence"], list)

    tokens = [e[1]["text"] for e in events if e[0] == "token"]
    assert len(tokens) > 0
    full_answer = "".join(tokens)
    assert len(full_answer.strip()) > 0


def test_chat_unknown_file():
    r = client.post("/api/chat", json={"prompt": "Summarize this", "file_ids": ["does_not_exist_123"]})
    assert r.status_code == 200

    events = parse_sse_events(r.text)
    event_names = [e[0] for e in events]
    assert "error" in event_names
    err_event = next(e[1] for e in events if e[0] == "error")
    assert "Could not process document" in err_event["message"]


def test_chat_document_query():
    # 1. Upload a sample document
    upload_resp = client.post(
        "/api/files",
        files={"file": ("sample_test_doc.txt", b"GRAFT is an adaptive RAG system that constructs hierarchical tree nodes.", "text/plain")},
    )
    assert upload_resp.status_code == 201
    fid = upload_resp.json()["id"]

    # 2. Wait for indexing to complete (status == 'ready')
    for _ in range(50):
        stat_resp = client.get(f"/api/files/{fid}")
        assert stat_resp.status_code == 200
        status = stat_resp.json()["status"]
        if status == "ready":
            break
        if status == "failed":
            raise AssertionError(f"Indexing failed: {stat_resp.json().get('error')}")
        time.sleep(0.2)
    else:
        raise TimeoutError("File processing took too long")

    # 3. Query with the document ID
    chat_resp = client.post("/api/chat", json={"prompt": "What does GRAFT construct?", "file_ids": [fid]})
    assert chat_resp.status_code == 200

    events = parse_sse_events(chat_resp.text)
    event_names = [e[0] for e in events]
    assert "meta" in event_names
    assert "token" in event_names
    assert "done" in event_names

    meta_event = next(e[1] for e in events if e[0] == "meta")
    evidence = meta_event.get("evidence", [])
    assert len(evidence) > 0
    assert any("sample_test_doc.txt" in str(ev.get("source", "")) for ev in evidence)
