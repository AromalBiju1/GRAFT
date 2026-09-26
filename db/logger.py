<<<<<<< HEAD
"""Usage:
    from db.logger import init_db, log_query, get_logs

    init_db()  # run once at startup
    log_query(
        query_text="What are the return policy terms?",
        system="graft",
        latency_ms=842,
        modules_fired=["fact_lookup", "contradiction_detection"],
        retrieval_depth="leaf",
        accuracy_flag=None,  # filled in later during benchmarking
    )
    rows = get_logs(system="graft")
"""
import sqlite3
import json
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "graft_logs.db"
VALID_ACCURACY_FLAGS = {"correct", "incorrect", "unscored"}


def _validate_accuracy_flag(accuracy_flag: str) -> None:
    if accuracy_flag is not None and accuracy_flag not in VALID_ACCURACY_FLAGS:
        raise ValueError(
            f"accuracy_flag must be one of {sorted(VALID_ACCURACY_FLAGS)} or None"
        )


@contextmanager
def get_connection():
    """Context manager so every caller gets a connection that's always closed."""
    conn = sqlite3.connect(DB_PATH)
=======
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
>>>>>>> origin/main
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


<<<<<<< HEAD
def init_db():
    """Create the query_logs table if it doesn't already exist. Safe to call
    every time the app starts — it's a no-op if the table is already there."""
    with get_connection() as conn:
=======
def init_db(db_path: Path | str | None = None) -> None:
    """Create ``query_logs`` table if it doesn't exist. Safe to call repeatedly."""
    with get_connection(db_path) as conn:
>>>>>>> origin/main
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
<<<<<<< HEAD
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_query_logs_system_timestamp
            ON query_logs (system, timestamp DESC)
            """
        )
=======
>>>>>>> origin/main


def log_query(
    query_text: str,
    system: str,
<<<<<<< HEAD
    latency_ms: float = None,
    modules_fired: list[str] = None,
    retrieval_depth: str = None,
    accuracy_flag: str = None,
) -> int:
    """
    Insert one query log row.
    Args:
        query_text: the raw question that was asked
        system: which system handled it — e.g. "graft" or "baseline"
        latency_ms: total response time in milliseconds
        modules_fired: list of specialist module names that activated,
                        e.g. ["fact_lookup", "multi_hop"]
        retrieval_depth: which tree level retrieval reached, e.g. "root",
                          "summary", "leaf" (None until indexing exists)
        accuracy_flag: "correct" / "incorrect" / "unscored" — usually filled
                        in later during benchmarking, not at query time
    Returns:
        the id of the inserted row
    """
    _validate_accuracy_flag(accuracy_flag)
    with get_connection() as conn:
=======
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
>>>>>>> origin/main
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
<<<<<<< HEAD
        return cursor.lastrowid


def update_accuracy(log_id: int, accuracy_flag: str) -> None:
    """Update the accuracy_flag for an existing log row — useful when scoring
    happens after the fact during benchmarking, not at query time."""
    _validate_accuracy_flag(accuracy_flag)
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE query_logs SET accuracy_flag = ? WHERE id = ?",
            (accuracy_flag, log_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(f"No query_logs row found for id={log_id}")


def get_logs(system: str = None, limit: int = 100) -> list[dict]:
    """
    Fetch logged rows, most recent first.

    Args:
        system: filter to one system ("graft" or "baseline"), or None for all
        limit: max rows to return

    Returns:
        list of dicts, one per row, with modules_fired parsed back into a list
    """
    if limit <= 0:
        raise ValueError("limit must be a positive integer")

    with get_connection() as conn:
=======
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
>>>>>>> origin/main
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

<<<<<<< HEAD
        rows = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if row_dict["modules_fired"]:
                row_dict["modules_fired"] = json.loads(row_dict["modules_fired"])
=======
        rows: list[dict] = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if row_dict.get("modules_fired"):
                try:
                    row_dict["modules_fired"] = json.loads(row_dict["modules_fired"])  # type: ignore[arg-type]
                except Exception:
                    pass
>>>>>>> origin/main
            rows.append(row_dict)
        return rows


if __name__ == "__main__":
    init_db()
<<<<<<< HEAD

=======
>>>>>>> origin/main
    new_id = log_query(
        query_text="What are the return policy terms?",
        system="graft",
        latency_ms=842.5,
        modules_fired=["fact_lookup", "contradiction_detection"],
        retrieval_depth="leaf",
    )
    print(f"Inserted log row with id={new_id}")
<<<<<<< HEAD

    update_accuracy(new_id, "correct")
    print("Updated accuracy flag")

=======
    update_accuracy(new_id, "correct")
    print("Updated accuracy flag")
>>>>>>> origin/main
    logs = get_logs(system="graft")
    print(f"Fetched {len(logs)} log(s):")
    for log in logs:
        print(f"  {log}")
<<<<<<< HEAD

    assert len(logs) >= 1, "Expected at least one log row after insert"
    assert logs[0]["query_text"] == "What are the return policy terms?"
    assert logs[0]["modules_fired"] == ["fact_lookup", "contradiction_detection"]
    assert logs[0]["accuracy_flag"] == "correct"
    print("\nSmoke test passed.")
=======
    assert len(logs) >= 1
    assert logs[0]["query_text"] == "What are the return policy terms?"
    assert logs[0]["modules_fired"] == ["fact_lookup", "contradiction_detection"]
    assert logs[0]["accuracy_flag"] == "correct"
    print("\nSmoke test passed.")
>>>>>>> origin/main
