"""Compatibility shim — the canonical query logger lives at ``db.logger``.

Re-exported so ``graft.db.logger`` resolves to the same functions (and the
same SQLite file) as ``db.logger`` rather than becoming a second logger.
"""

from db.logger import DB_PATH, get_connection, get_logs, init_db, log_query, update_accuracy

__all__ = ["DB_PATH", "get_connection", "get_logs", "init_db", "log_query", "update_accuracy"]
