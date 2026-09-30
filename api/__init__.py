"""FastAPI application for GRAFT.

The app itself lives in :mod:`api.main`. Re-exported here so
``from api import app`` works for the server entrypoint and for tests.
"""

from api.main import app
from version import __version__

__all__ = ["__version__", "app"]
