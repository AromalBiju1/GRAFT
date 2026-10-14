import asyncio, json, uuid
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import UPLOAD_DIR, MAX_UPLOAD_MB, ALLOWED_EXT, ALLOWED_MIME

router = APIRouter(prefix="/api")
FILES: dict[str, dict] = {}  # in-memory for now; persist later via db/logger.py


def _sniff_ok(head: bytes, ext: str) -> bool:
    if ext == ".pdf":
        return head.startswith(b"%PDF")
    if ext == ".docx":
        return head.startswith(b"PK")       # docx is a zip container
    return b"\x00" not in head              # txt: no NUL bytes


@router.post("/files", status_code=201)
async def upload(bg: BackgroundTasks, file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT or file.content_type not in ALLOWED_MIME:
        raise HTTPException(415, "Only PDF, DOCX or TXT files are supported")

    fid = uuid.uuid4().hex
    dest = UPLOAD_DIR / f"{fid}{ext}"       # never use the client filename on disk
    dest.parent.mkdir(parents=True, exist_ok=True)

    limit = MAX_UPLOAD_MB * 1024 * 1024
    size, head = 0, b""
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            if not head:
                head = chunk[:8]
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB")
            out.write(chunk)

    if size == 0 or not _sniff_ok(head, ext):
        dest.unlink(missing_ok=True)
        raise HTTPException(415, "File is empty or its content doesn't match its type")

    FILES[fid] = {"id": fid, "name": Path(file.filename).name, "size": size,
                  "path": str(dest), "status": "uploaded", "error": None}
    bg.add_task(_process, fid)
    return FILES[fid]


async def _process(fid: str):
    rec = FILES[fid]
    rec["status"] = "parsing"
    try:
        from indexing.ingest import ingest_file       # ADAPT: your real parse function
        from indexing.pipeline import index_text      # ADAPT: your real pipeline entry
        text = await asyncio.to_thread(ingest_file, rec["path"])
        await asyncio.to_thread(index_text, text, fid)  # fid = document_id
        rec["status"] = "ready"
    except Exception as e:
        rec["status"], rec["error"] = "failed", str(e)


@router.get("/files/{fid}")
def file_status(fid: str):
    if fid not in FILES:
        raise HTTPException(404, "Unknown file")
    return FILES[fid]


class ChatReq(BaseModel):
    prompt: str
    file_ids: list[str] = []


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def run_query(prompt: str, doc_ids: list[str]) -> dict:
    # ADAPT: call the same logic /query uses (router -> retrieval -> modules -> generation),
    # restricted to doc_ids. Return {"answer": str, "routing": {...}, "evidence": [...]}.
    raise NotImplementedError


@router.post("/chat")
async def chat(req: ChatReq):
    async def events():
        try:
            for fid in req.file_ids:
                while FILES.get(fid, {}).get("status") in ("uploaded", "parsing"):
                    yield _sse("status", {"file": fid, "status": FILES[fid]["status"]})
                    await asyncio.sleep(0.5)
                if FILES.get(fid, {}).get("status") != "ready":
                    reason = FILES.get(fid, {}).get("error") or "unknown file"
                    yield _sse("error", {"message": f"Could not process document: {reason}"})
                    return

            result = await asyncio.to_thread(run_query, req.prompt, req.file_ids)
            yield _sse("meta", {"routing": result.get("routing"),
                                "evidence": result.get("evidence")})
            for word in result["answer"].split(" "):        # simulated streaming
                yield _sse("token", {"t": word + " "})
                await asyncio.sleep(0.01)
            yield _sse("done", {})
        except Exception as e:
            yield _sse("error", {"message": str(e)})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})