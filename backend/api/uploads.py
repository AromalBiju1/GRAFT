"""POST /chat — multipart prompt + files, streamed back as Server-Sent Events.

Events: status {file, state}, token {text}, error {message}, done {}.
Wire-up points marked TODO(graft) adapt to your existing modules.
"""
import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

router = APIRouter()

UPLOAD_DIR = Path("data/uploads")          # move to config.py
MAX_BYTES = 10 * 1024 * 1024               # 10 MB, configurable
MAX_FILES = 5
ALLOWED = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
}


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name or "file").name)[:100]


async def save_upload(f: UploadFile) -> Path:
    """Validate type + size, stream to disk in chunks (never fully in memory)."""
    if f.content_type not in ALLOWED:
        raise ValueError(f"{safe_name(f.filename)}: unsupported type. Use PDF, DOCX or TXT.")
    dest = UPLOAD_DIR / f"{uuid.uuid4().hex}_{safe_name(f.filename)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as out:
        while chunk := await f.read(1024 * 256):
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise ValueError(f"{safe_name(f.filename)}: larger than {MAX_BYTES // 2**20} MB.")
            out.write(chunk)
    return dest


def ingest_file(path: Path) -> str:
    """Blocking parse + index. Runs in a worker thread.
    TODO(graft): replace with your real calls, e.g.
        text = indexing.ingest.<parse_fn>(path)
        indexing.pipeline.<index_fn>(text, source=path.name)
    """
    from indexing import ingest, pipeline  # noqa
    raise NotImplementedError("connect indexing.ingest / indexing.pipeline here")


async def answer_stream(question: str):
    """TODO(graft): call your router -> retrieval -> modules -> generation flow
    and yield text pieces. If generation isn't token-streaming yet, yield the
    final string in word-sized pieces."""
    raise NotImplementedError("connect api.main /query orchestration here")
    yield ""  # pragma: no cover


@router.post("/chat")
async def chat(
    prompt: Annotated[str, Form()] = "",
    files: Annotated[list[UploadFile], File()] | None = None,
):
    files = files or []
    if not prompt.strip() and not files:
        return JSONResponse({"detail": "Send a question or attach a document."}, status_code=400)
    if len(files) > MAX_FILES:
        return JSONResponse({"detail": f"Attach at most {MAX_FILES} files."}, status_code=413)

    # Validate + store before streaming so bad input returns a real HTTP error.
    saved: list[Path] = []
    try:
        for f in files:
            saved.append(await save_upload(f))
    except ValueError as e:
        for p in saved:
            p.unlink(missing_ok=True)
        return JSONResponse({"detail": str(e)}, status_code=415 if "type" in str(e) else 413)

    async def events():
        try:
            for p in saved:
                yield sse("status", {"file": p.name, "state": "processing"})
                await asyncio.to_thread(ingest_file, p)   # keeps the event loop free
                yield sse("status", {"file": p.name, "state": "ready"})
            if prompt.strip():
                async for piece in answer_stream(prompt):
                    yield sse("token", {"text": piece})
        except asyncio.CancelledError:
            raise  # client pressed Stop
        except Exception as e:  # report failure back into the chat
            yield sse("error", {"message": f"Something went wrong: {e}"})
        finally:
            yield sse("done", {})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

# api/main.py:  from api.uploads import router as uploads_router; app.include_router(uploads_router)
# Requires: pip install python-multipart
