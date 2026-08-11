"""Minimal FastAPI entry point for the GRAFT backend."""

from fastapi import FastAPI


app = FastAPI(title="GRAFT API")


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the backend application is healthy."""
    return {"status": "ok"}
