"""Tests for the analytics endpoints (issue #64).

Covers:
- GET /analytics/queries — recent queries with the required fields
- GET /analytics/summary — totals, average latency, module usage counts
- GET /analytics/slow — threshold filtering
- New rows appear in subsequent analytics reads
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import db.logger as db_logger
from api import main as api_main


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the SQLite log store to a temp file for test isolation."""
    db_file = tmp_path / "test_logs.db"
    monkeypatch.setattr(db_logger, "DB_PATH", db_file)
    db_logger.init_db()
    return db_file


@pytest.fixture
def client(isolated_db: Path) -> Iterator[TestClient]:
    with TestClient(api_main.app) as c:
        yield c


@pytest.fixture
def seeded(client: TestClient) -> TestClient:
    db_logger.log_query(
        query_text="What is GRAFT?",
        system="graft",
        latency_ms=200.0,
        modules_fired=["fact_lookup"],
        retrieval_depth="0",
    )
    db_logger.log_query(
        query_text="Compare the two documents in detail",
        system="graft",
        latency_ms=2500.0,
        modules_fired=["fact_lookup", "multi_hop"],
        retrieval_depth="2",
    )
    return client


class TestRecentQueries:
    def test_returns_seeded_queries(self, seeded: TestClient) -> None:
        resp = seeded.get("/analytics/queries")
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert "queries" in payload
        assert payload["count"] == 2
        assert len(payload["queries"]) == 2

    def test_entries_contain_required_fields(self, seeded: TestClient) -> None:
        queries = seeded.get("/analytics/queries").json()["queries"]
        required = {"query_text", "latency_ms", "modules_fired", "retrieval_depth", "timestamp"}
        for entry in queries:
            assert required <= set(entry), f"missing fields in {entry}"
            assert isinstance(entry["modules_fired"], list)

    def test_limit_is_respected(self, seeded: TestClient) -> None:
        payload = seeded.get("/analytics/queries", params={"limit": 1}).json()
        assert payload["count"] == 1
        assert len(payload["queries"]) == 1

    def test_default_limit(self, client: TestClient) -> None:
        # Empty DB still returns a well-formed envelope.
        payload = client.get("/analytics/queries").json()
        assert payload["queries"] == []
        assert payload["count"] == 0

    def test_new_records_appear(self, seeded: TestClient) -> None:
        before = seeded.get("/analytics/queries").json()["count"]
        db_logger.log_query(
            query_text="A brand new question?",
            system="graft",
            latency_ms=100.0,
            modules_fired=[],
            retrieval_depth="0",
        )
        after = seeded.get("/analytics/queries").json()
        assert after["count"] == before + 1
        texts = [q["query_text"] for q in after["queries"]]
        assert "A brand new question?" in texts

    def test_invalid_limit_rejected(self, client: TestClient) -> None:
        assert client.get("/analytics/queries", params={"limit": 0}).status_code == 422


class TestSummary:
    def test_summary_counts_match(self, seeded: TestClient) -> None:
        summary = seeded.get("/analytics/summary").json()
        assert summary["total_queries"] == 2
        # (200 + 2500) / 2 = 1350
        assert summary["avg_latency_ms"] == pytest.approx(1350.0)
        assert summary["module_usage_counts"] == {"fact_lookup": 2, "multi_hop": 1}

    def test_summary_empty_db(self, client: TestClient) -> None:
        summary = client.get("/analytics/summary").json()
        assert summary["total_queries"] == 0
        assert summary["avg_latency_ms"] == 0.0
        assert summary["module_usage_counts"] == {}

    def test_summary_updates_with_new_rows(self, seeded: TestClient) -> None:
        db_logger.log_query(
            query_text="Another one",
            system="graft",
            latency_ms=500.0,
            modules_fired=["numeric_reasoning"],
            retrieval_depth="1",
        )
        summary = seeded.get("/analytics/summary").json()
        assert summary["total_queries"] == 3
        assert summary["module_usage_counts"]["numeric_reasoning"] == 1


class TestSlowQueries:
    def test_threshold_filters(self, seeded: TestClient) -> None:
        payload = seeded.get("/analytics/slow", params={"threshold_ms": 1000}).json()
        assert payload["count"] == 1
        assert len(payload["queries"]) == 1
        slow = payload["queries"][0]
        assert slow["query_text"] == "Compare the two documents in detail"
        assert slow["latency_ms"] > 1000

    def test_high_threshold_returns_nothing(self, seeded: TestClient) -> None:
        payload = seeded.get("/analytics/slow", params={"threshold_ms": 10000}).json()
        assert payload["queries"] == []
        assert payload["count"] == 0

    def test_zero_threshold_returns_everything(self, seeded: TestClient) -> None:
        payload = seeded.get("/analytics/slow", params={"threshold_ms": 0}).json()
        assert payload["count"] == 2

    def test_slow_entries_have_required_fields(self, seeded: TestClient) -> None:
        queries = seeded.get("/analytics/slow", params={"threshold_ms": 1000}).json()["queries"]
        required = {"query_text", "latency_ms", "modules_fired", "retrieval_depth", "timestamp"}
        for entry in queries:
            assert required <= set(entry)
