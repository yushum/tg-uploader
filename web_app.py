import asyncio
import logging
import math
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo

from web_catalog import group_sessions, load_recordings, parse_http_range


logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(levelname)s][web] %(message)s",
)
logger = logging.getLogger("tg-uploader-web")

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "/app/session/uploader.db")
UPLOADER_SESSION_NAME = os.getenv("UPLOADER_SESSION_NAME", "/app/session/uploader")
WEB_SESSION_NAME = os.getenv("WEB_SESSION_NAME", "/app/session/web")
MAX_STREAMS = max(1, min(int(os.getenv("MAX_STREAMS", "4")), 16))
STATIC_DIR = Path(__file__).resolve().parent / "web"

telegram: TelegramClient | None = None
stream_slots = asyncio.Semaphore(MAX_STREAMS)


def _session_path(name: str) -> Path:
    return Path(name if name.endswith(".session") else f"{name}.session")


def ensure_web_session() -> None:
    destination = _session_path(WEB_SESSION_NAME)
    if destination.exists():
        return
    source = _session_path(UPLOADER_SESSION_NAME)
    if not source.exists():
        raise RuntimeError(f"Telegram source session does not exist: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    logger.info("Created independent web Telegram session at %s", destination)


def _proxy_config():
    proxy_type = os.getenv("PROXY_TYPE", "")
    proxy_host = os.getenv("PROXY_HOST", "")
    proxy_port = os.getenv("PROXY_PORT", "")
    if proxy_type and proxy_host and proxy_port:
        return {"proxy_type": proxy_type.lower(), "addr": proxy_host, "port": int(proxy_port)}
    return None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global telegram
    if not all((API_ID, API_HASH, CHANNEL_ID)):
        raise RuntimeError("Missing API_ID, API_HASH or CHANNEL_ID")
    ensure_web_session()
    telegram = TelegramClient(
        WEB_SESSION_NAME,
        API_ID,
        API_HASH,
        connection_retries=None,
        auto_reconnect=True,
        device_model="TG-Uploader-Web",
        proxy=_proxy_config(),
    )
    await telegram.connect()
    if not await telegram.is_user_authorized():
        raise RuntimeError("The cloned Telegram web session is not authorized")
    logger.info("Telegram streaming client connected")
    try:
        yield
    finally:
        await telegram.disconnect()
        telegram = None


app = FastAPI(title="直播档案", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _catalog():
    try:
        return load_recordings(DB_PATH)
    except sqlite3.Error as exc:
        logger.error("Could not read uploader database: %s", exc)
        raise HTTPException(status_code=503, detail="录像目录暂时不可用") from exc


def _video_details(message):
    document = getattr(message, "document", None) if message else None
    if not document:
        return None
    duration = 0
    width = 0
    height = 0
    for attribute in document.attributes:
        if isinstance(attribute, DocumentAttributeVideo):
            duration = int(attribute.duration or 0)
            width = int(attribute.w or 0)
            height = int(attribute.h or 0)
            break
    return {
        "duration": duration,
        "size": int(document.size or 0),
        "mime_type": document.mime_type or "video/mp4",
        "width": width,
        "height": height,
    }


async def _get_message(message_id: int):
    if telegram is None:
        raise HTTPException(status_code=503, detail="Telegram 尚未连接")
    message = await telegram.get_messages(CHANNEL_ID, ids=message_id)
    if not message or not getattr(message, "document", None):
        raise HTTPException(status_code=404, detail="录像不存在或已从频道删除")
    return message


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True, "telegram": bool(telegram and telegram.is_connected())}


@app.get("/api/streamers")
async def streamers():
    sessions = group_sessions(_catalog())
    grouped: dict[str, dict] = {}
    for (streamer, _platform, date, _time), parts in sessions.items():
        item = grouped.setdefault(
            streamer,
            {"name": streamer, "session_count": 0, "part_count": 0, "latest_date": date},
        )
        item["session_count"] += 1
        item["part_count"] += len(parts)
        item["latest_date"] = max(item["latest_date"], date)
    return sorted(grouped.values(), key=lambda item: item["name"].casefold())


@app.get("/api/dates")
async def dates(streamer: str = Query(min_length=1)):
    sessions = group_sessions([part for part in _catalog() if part.streamer == streamer])
    grouped: dict[str, dict] = {}
    for (_streamer, _platform, date, _time), parts in sessions.items():
        item = grouped.setdefault(date, {"date": date, "session_count": 0, "part_count": 0})
        item["session_count"] += 1
        item["part_count"] += len(parts)
    return sorted(grouped.values(), key=lambda item: item["date"], reverse=True)


@app.get("/api/sessions")
async def sessions(streamer: str = Query(min_length=1), date: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$")):
    selected = [part for part in _catalog() if part.streamer == streamer and part.date == date]
    grouped = group_sessions(selected)
    message_ids = [part.message_id for parts in grouped.values() for part in parts]
    if telegram is None:
        raise HTTPException(status_code=503, detail="Telegram 尚未连接")
    messages = await telegram.get_messages(CHANNEL_ID, ids=message_ids) if message_ids else []
    by_id = {message.id: message for message in messages if message}

    result = []
    for (_streamer, platform, _date, time), parts in sorted(grouped.items(), key=lambda item: item[0][3]):
        result_parts = []
        for position, part in enumerate(parts, start=1):
            details = _video_details(by_id.get(part.message_id))
            result_parts.append(
                {
                    "message_id": part.message_id,
                    "position": position,
                    "label": part.part_label,
                    "filename": Path(part.filepath).name,
                    "available": details is not None,
                    **(details or {"duration": 0, "size": 0, "mime_type": "video/mp4", "width": 0, "height": 0}),
                }
            )
        result.append(
            {
                "time": time,
                "platform": platform,
                "part_count": len(result_parts),
                "total_duration": sum(part["duration"] for part in result_parts),
                "parts": result_parts,
            }
        )
    return result


def _media_headers(details: dict, start: int, end: int, partial: bool) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Type": details["mime_type"],
        "Cache-Control": "private, no-store",
        "Content-Encoding": "identity",
        "X-Accel-Buffering": "no",
    }
    if partial:
        headers["Content-Range"] = f'bytes {start}-{end}/{details["size"]}'
    return headers


async def _resolve_media(message_id: int, range_header: str | None):
    message = await _get_message(message_id)
    details = _video_details(message)
    if not details or details["size"] <= 0:
        raise HTTPException(status_code=404, detail="消息中没有可播放视频")
    try:
        start, end, partial = parse_http_range(range_header, details["size"])
    except (ValueError, TypeError):
        return message, details, None
    return message, details, (start, end, partial)


@app.head("/api/media/{message_id}")
async def media_head(message_id: int):
    _message, details, byte_range = await _resolve_media(message_id, None)
    start, end, partial = byte_range
    return Response(status_code=200, headers=_media_headers(details, start, end, partial))


@app.get("/api/media/{message_id}")
async def media(message_id: int, request: Request):
    message, details, byte_range = await _resolve_media(message_id, request.headers.get("range"))
    if byte_range is None:
        return Response(
            status_code=416,
            headers={"Content-Range": f'bytes */{details["size"]}', "Accept-Ranges": "bytes"},
        )
    start, end, partial = byte_range
    remaining = end - start + 1
    request_size = 4096 if remaining <= 4096 else 512 * 1024

    async def body():
        bytes_left = remaining
        iterator = None
        async with stream_slots:
            try:
                iterator = telegram.iter_download(
                    message.document,
                    offset=start,
                    limit=math.ceil(remaining / request_size),
                    chunk_size=request_size,
                    request_size=request_size,
                    file_size=details["size"],
                )
                async for chunk in iterator:
                    if bytes_left <= 0:
                        break
                    data = bytes(chunk[:bytes_left])
                    if not data:
                        break
                    bytes_left -= len(data)
                    yield data
            finally:
                if iterator is not None:
                    await iterator.close()

    return StreamingResponse(
        body(),
        status_code=206 if partial else 200,
        media_type=details["mime_type"],
        headers=_media_headers(details, start, end, partial),
    )
