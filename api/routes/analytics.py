"""Query analytics endpoints (issue #64).

Exposes the query logs recorded by ``db/logger.py`` through three
read-only endpoints:

- ``GET /analytics/queries?limit=50`` — most recently processed queries.
- ``GET /analytics/summary`` — totals, average latency, per-module counts.
- ``GET /analytics/slow?threshold_ms=1000`` — queries slower than a threshold.

All reads go through the existing ``db.logger`` SQLite store so logging
and analytics can never diverge on schema or path.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from fastapi import APIRouter, Query

import db.logger as db_logger

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _parse_modules_fired(value: Any) -> list[str]:
    """Normalise the ``modules_fired`` column to a list of names."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        if not value:
            return []
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [str(v) for v in parsed]
        return []
    return []


def _row_to_entry(row: dict[str, Any] | Any) -> dict[str, Any]:
    """Project a ``query_logs`` row to the public analytics entry shape."""
    if not isinstance(row, dict):
        try:
            row = dict(row)
        except Exception:
            row = {}
    return {
        "query_text": row.get("query_text"),
        "latency_ms": row.get("latency_ms"),
        "modules_fired": _parse_modules_fired(row.get("modules_fired")),
        "retrieval_depth": row.get("retrieval_depth"),
        "timestamp": row.get("timestamp"),
    }


@router.get("/queries")
def list_queries(
    limit: int = Query(default=50, ge=1, le=1000, description="Max rows to return"),
) -> dict[str, Any]:
    """Return the most recently processed queries, newest first."""
    db_logger.init_db()
    rows = db_logger.get_logs(limit=limit)
    entries = [_row_to_entry(r) for r in rows]
    return {"queries": entries, "count": len(entries), "limit": limit}


@router.get("/summary")
def get_summary() -> dict[str, Any]:
    """Return totals, average latency and per-module activation counts."""
    db_logger.init_db()
    with db_logger.get_connection() as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) AS n, AVG(latency_ms) AS avg_latency FROM query_logs"
        )
        agg = cursor.fetchone()
        total = int(agg["n"] or 0)
        avg_latency = float(agg["avg_latency"]) if agg["avg_latency"] is not None else 0.0

        cursor = conn.execute(
            "SELECT modules_fired FROM query_logs WHERE modules_fired IS NOT NULL"
        )
        counter: Counter[str] = Counter()
        for row in cursor.fetchall():
            for mod in _parse_modules_fired(row["modules_fired"]):
                counter[mod] += 1

    return {
        "total_queries": total,
        "avg_latency_ms": avg_latency,
        "module_usage_counts": dict(counter),
    }


@router.get("/slow")
def list_slow_queries(
    threshold_ms: float = Query(
        default=1000.0, ge=0.0, description="Return queries slower than this (ms)"
    ),
    limit: int = Query(default=100, ge=1, le=1000, description="Max rows to return"),
) -> dict[str, Any]:
    """Return queries whose ``latency_ms`` exceeded ``threshold_ms``.

    Ordered newest-first, consistent with ``GET /analytics/queries``.
    """
    db_logger.init_db()
    with db_logger.get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT * FROM query_logs
            WHERE latency_ms IS NOT NULL AND latency_ms > ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (threshold_ms, limit),
        )
        rows = [dict(r) for r in cursor.fetchall()]
    entries = [_row_to_entry(r) for r in rows]
    return {"queries": entries, "count": len(entries), "threshold_ms": threshold_ms}
