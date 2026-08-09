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


@contextmanager
def get_connection():
    """Context manager so every caller gets a connection that's always closed."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Create the query_logs table if it doesn't already exist. Safe to call
    every time the app starts — it's a no-op if the table is already there."""
    with get_connection() as conn:
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_query_logs_system_timestamp ON query_logs(system, timestamp)"
        )


def log_query(
    query_text: str,
    system: str,
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
    if accuracy_flag is not None and accuracy_flag not in {"correct", "incorrect", "unscored"}:
        raise ValueError(
            f"accuracy_flag must be one of: correct, incorrect, unscored (got {accuracy_flag!r})"
        )

    with get_connection() as conn:
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
        return cursor.lastrowid


def update_accuracy(log_id: int, accuracy_flag: str) -> None:
    """Update the accuracy_flag for an existing log row — useful when scoring
    happens after the fact during benchmarking, not at query time."""
    if accuracy_flag not in {"correct", "incorrect", "unscored"}:
        raise ValueError(
            f"accuracy_flag must be one of: correct, incorrect, unscored (got {accuracy_flag!r})"
        )

    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE query_logs SET accuracy_flag = ? WHERE id = ?",
            (accuracy_flag, log_id),
        )
        if cursor.rowcount == 0:
            raise KeyError(f"No query_logs row found with id={log_id}")


def get_logs(system: str = None, limit: int = 100) -> list[dict]:
    """
    Fetch logged rows, most recent first.

    Args:
        system: filter to one system ("graft" or "baseline"), or None for all
        limit: max rows to return

    Returns:
        list of dicts, one per row, with modules_fired parsed back into a list
    """
    with get_connection() as conn:
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

        rows = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if row_dict["modules_fired"]:
                row_dict["modules_fired"] = json.loads(row_dict["modules_fired"])
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

    assert len(logs) >= 1, "Expected at least one log row after insert"
    assert logs[0]["query_text"] == "What are the return policy terms?"
    assert logs[0]["modules_fired"] == ["fact_lookup", "contradiction_detection"]
    assert logs[0]["accuracy_flag"] == "correct"
    print("\nSmoke test passed.")