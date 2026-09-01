"""SQLite logger for query/benchmark logging.

Canonical usage stays compatible with the feat/infra-sqlite branch:

    from db.logger import init_db, log_query, get_logs

    init_db()
    log_query(query_text="...", system="graft", latency_ms=842, ...)

DB path resolution:
  1) ``graft.config.settings.db_path`` if graft is importable and ``.env`` is present
  2) ``GRAFT_DB_PATH`` env var
  3) Fallback ``db/graft_logs.db`` relative to the repo root / this file
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Resolve DB path with fallback chain to avoid circular import issues
def _resolve_db_path() -> Path:
    # 1. Try graft.config.settings
    try:
        from graft.config import settings as graft_settings

        p = Path(graft_settings.db_path)
        # If settings gives a relative path, resolve relative to repo root (parent of db/)
        if not p.is_absolute():
            # repo root = parent of db/ parent if called from db/logger.py, or cwd
            candidate = Path(__file__).resolve().parent.parent / p
            # Prefer the settings path as-is (which may be relative to cwd); keep both viable.
            # Use settings path directly; Path will resolve relative to CWD at runtime.
            return Path(p)
        return p
    except Exception:
        pass

    # 2. Env var
    env_path = os.environ.get("GRAFT_DB_PATH")
    if env_path:
        return Path(env_path)

    # 3. Default: next to this file
    return Path(__file__).parent / "graft_logs.db"


DB_PATH = _resolve_db_path()


@contextmanager
def get_connection(db_path: Path | str | None = None):
    """Yield a SQLite connection that is always committed and closed."""
    path = Path(db_path) if db_path is not None else DB_PATH
    # Ensure parent dir exists
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> None:
    """Create ``query_logs`` table if it doesn't exist. Safe to call repeatedly."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS query_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_text TEXT NOT NULL,
                system TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                latency_ms REAL,
                modules_fired TEXT,
                retrieval_depth TEXT,
                accuracy_flag TEXT
            )
            """
        )


def log_query(
    query_text: str,
    system: str,
    latency_ms: float | None = None,
    modules_fired: list[str] | None = None,
    retrieval_depth: str | None = None,
    accuracy_flag: str | None = None,
    db_path: Path | str | None = None,
) -> int:
    """Insert one query log row.

    Args:
        query_text: raw question text.
        system: which system handled it — e.g. ``"graft"`` or ``"baseline"``.
        latency_ms: total response time in milliseconds.
        modules_fired: list of specialist module names that activated.
        retrieval_depth: tree level retrieval reached, e.g. ``"0"``, ``"leaf"``.
        accuracy_flag: ``"correct"`` / ``"incorrect"`` / ``"unscored"`` — usually
            filled later during benchmarking.
        db_path: override DB location (useful for tests).

    Returns:
        Row id of the inserted log entry.
    """
    if not isinstance(query_text, str) or not query_text.strip():
        raise ValueError("query_text must be a non-empty string")
    if not isinstance(system, str) or not system.strip():
        raise ValueError("system must be a non-empty string")

    with get_connection(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO query_logs
                (query_text, system, timestamp, latency_ms,
                 modules_fired, retrieval_depth, accuracy_flag)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                query_text,
                system,
                datetime.now(timezone.utc).isoformat(),
                latency_ms,
                json.dumps(modules_fired) if modules_fired is not None else None,
                retrieval_depth,
                accuracy_flag,
            ),
        )
        return int(cursor.lastrowid)  # type: ignore[arg-type]


def update_accuracy(log_id: int, accuracy_flag: str, db_path: Path | str | None = None) -> None:
    """Update accuracy_flag for an existing log row."""
    if not isinstance(log_id, int) or log_id < 1:
        raise ValueError("log_id must be a positive integer")
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE query_logs SET accuracy_flag = ? WHERE id = ?",
            (accuracy_flag, log_id),
        )


def get_logs(
    system: str | None = None,
    limit: int = 100,
    db_path: Path | str | None = None,
) -> list[dict]:
    """Fetch logged rows, most recent first.

    Args:
        system: filter to one system (``"graft"`` or ``"baseline"``), or None for all.
        limit: max rows to return.
        db_path: override DB location.

    Returns:
        List of dicts, one per row, with ``modules_fired`` parsed back into a list.
    """
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    with get_connection(db_path) as conn:
        if system:
            cursor = conn.execute(
                """
                SELECT * FROM query_logs
                WHERE system = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (system, limit),
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM query_logs ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )

        rows: list[dict] = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if row_dict.get("modules_fired"):
                try:
                    row_dict["modules_fired"] = json.loads(row_dict["modules_fired"])  # type: ignore[arg-type]
                except Exception:
                    pass
            rows.append(row_dict)
        return rows


if __name__ == "__main__":
    init_db()
    new_id = log_query(
        query_text="What are the return policy terms?",
        system="graft",
        latency_ms=842.5,
        modules_fired=["fact_lookup", "contradiction_detection"],
        retrieval_depth="leaf",
    )
    print(f"Inserted log row with id={new_id}")
    update_accuracy(new_id, "correct")
    print("Updated accuracy flag")
    logs = get_logs(system="graft")
    print(f"Fetched {len(logs)} log(s):")
    for log in logs:
        print(f"  {log}")
    assert len(logs) >= 1
    assert logs[0]["query_text"] == "What are the return policy terms?"
    assert logs[0]["modules_fired"] == ["fact_lookup", "contradiction_detection"]
    assert logs[0]["accuracy_flag"] == "correct"
    print("\nSmoke test passed.")
