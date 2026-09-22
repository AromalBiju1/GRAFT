"""Minimal FastAPI entry point for the GRAFT backend."""

from fastapi import FastAPI

from api.routes.indexing import router as indexing_router


app = FastAPI(title="GRAFT API")

app.include_router(indexing_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Report whether the backend application is healthy."""
    return {"status": "ok"}
