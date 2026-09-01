import asyncio
import logging
import math
import os
import sqlite3
import time
import uuid
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
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"
MEDIA_CACHE_DIR = Path(os.getenv("MEDIA_CACHE_DIR", "/tmp/tg-uploader-media-cache"))
MEDIA_CACHE_MAX_BYTES = max(64, int(os.getenv("MEDIA_CACHE_MAX_MB", "1024"))) * 1024 * 1024
MEDIA_CACHE_BLOCK_SIZE = 512 * 1024
MEDIA_CACHE_MAX_AGE = max(60, int(os.getenv("MEDIA_CACHE_MAX_AGE", "86400")))
MEDIA_PREFETCH_BLOCKS = max(1, min(int(os.getenv("MEDIA_PREFETCH_BLOCKS", "8")), 64))

telegram: TelegramClient | None = None
stream_slots = asyncio.Semaphore(MAX_STREAMS)
thumbnail_slots = asyncio.Semaphore(6)
media_cache_inflight: dict[tuple[int, int, int], asyncio.Future[bytes]] = {}
media_prefetch_tasks: set[asyncio.Task] = set()
media_cache_cleanup_lock = asyncio.Lock()
last_media_cache_cleanup = 0.0


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
        if media_prefetch_tasks:
            tasks = tuple(media_prefetch_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
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
            duration = float(attribute.duration or 0)
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
    return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/app.js", include_in_schema=False)
async def design_app_script():
    return FileResponse(FRONTEND_DIR / "app.js", media_type="text/javascript")


@app.get("/style.css", include_in_schema=False)
async def design_app_style():
    return FileResponse(FRONTEND_DIR / "style.css", media_type="text/css")


@app.get("/streamers", include_in_schema=False)
@app.get("/favorites", include_in_schema=False)
async def design_page():
    return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/streamer/{path:path}", include_in_schema=False)
async def streamer_page(path: str):
    return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True, "telegram": bool(telegram and telegram.is_connected())}


@app.get("/api/streamers")
async def streamers():
    sessions = group_sessions(_catalog())
    grouped: dict[str, dict] = {}
    for (streamer, _platform, date, time), parts in sessions.items():
        marker = (date, time)
        item = grouped.setdefault(
            streamer,
            {
                "name": streamer,
                "session_count": 0,
                "part_count": 0,
                "latest_date": date,
                "cover_message_id": parts[0].message_id,
                "_cover_marker": marker,
            },
        )
        item["session_count"] += 1
        item["part_count"] += len(parts)
        if marker > item["_cover_marker"]:
            item["latest_date"] = date
            item["cover_message_id"] = parts[0].message_id
            item["_cover_marker"] = marker
    for item in grouped.values():
        item.pop("_cover_marker")
    return sorted(grouped.values(), key=lambda item: item["name"].casefold())


@app.get("/api/dates")
async def dates(streamer: str = Query(min_length=1)):
    sessions = group_sessions([part for part in _catalog() if part.streamer == streamer])
    grouped: dict[str, dict] = {}
    for (_streamer, _platform, date, time), parts in sessions.items():
        item = grouped.setdefault(
            date,
            {
                "date": date,
                "session_count": 0,
                "part_count": 0,
                "cover_message_id": parts[0].message_id,
                "_cover_time": time,
            },
        )
        item["session_count"] += 1
        item["part_count"] += len(parts)
        if time > item["_cover_time"]:
            item["cover_message_id"] = parts[0].message_id
            item["_cover_time"] = time
    for item in grouped.values():
        item.pop("_cover_time")
    return sorted(grouped.values(), key=lambda item: item["date"], reverse=True)


@app.get("/api/thumbnail/{message_id}")
async def thumbnail(message_id: int):
    message = await _get_message(message_id)
    async with thumbnail_slots:
        content = await telegram.download_media(message, file=bytes, thumb=-1)
    if not content:
        raise HTTPException(status_code=404, detail="录像没有缩略图")
    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=604800, immutable"},
    )


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


def _media_cache_path(message_id: int, file_size: int, block_index: int) -> Path:
    return MEDIA_CACHE_DIR / f"{message_id}-{file_size}" / f"{block_index:08d}.block"


def _read_cache_block(path: Path, expected_size: int) -> bytes | None:
    try:
        if path.stat().st_size != expected_size:
            path.unlink(missing_ok=True)
            return None
        data = path.read_bytes()
        os.utime(path, None)
        return data
    except OSError:
        return None


def _write_cache_block(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _cleanup_media_cache_sync() -> tuple[int, int]:
    now = time.time()
    entries: list[tuple[float, int, Path]] = []
    total = 0
    removed = 0
    try:
        MEDIA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for path in MEDIA_CACHE_DIR.glob("*/*.block"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if now - stat.st_mtime > MEDIA_CACHE_MAX_AGE:
                try:
                    path.unlink()
                    removed += stat.st_size
                except OSError:
                    pass
                continue
            total += stat.st_size
            entries.append((stat.st_mtime, stat.st_size, path))
        if total > MEDIA_CACHE_MAX_BYTES:
            for _mtime, size, path in sorted(entries):
                if total <= MEDIA_CACHE_MAX_BYTES:
                    break
                try:
                    path.unlink()
                    total -= size
                    removed += size
                except OSError:
                    pass
        for directory in MEDIA_CACHE_DIR.iterdir():
            if directory.is_dir():
                try:
                    directory.rmdir()
                except OSError:
                    pass
    except OSError as exc:
        logger.warning("Media cache cleanup failed: %s", exc)
    return total, removed


async def _maybe_cleanup_media_cache() -> None:
    global last_media_cache_cleanup
    now = time.monotonic()
    if now - last_media_cache_cleanup < 60 or media_cache_cleanup_lock.locked():
        return
    async with media_cache_cleanup_lock:
        now = time.monotonic()
        if now - last_media_cache_cleanup < 60:
            return
        last_media_cache_cleanup = now
        total, removed = await asyncio.to_thread(_cleanup_media_cache_sync)
        if removed:
            logger.info("Media cache pruned %.1f MiB; %.1f MiB remain", removed / 1024**2, total / 1024**2)


def _consume_future_exception(future: asyncio.Future) -> None:
    """Retrieve background prefetch failures when no request awaited that block."""
    if future.cancelled():
        return
    try:
        future.exception()
    except asyncio.CancelledError:
        pass


def _finish_prefetch_task(task: asyncio.Task) -> None:
    media_prefetch_tasks.discard(task)
    if task.cancelled():
        return
    try:
        task.exception()
    except asyncio.CancelledError:
        pass


def _claim_media_batch(message_id: int, file_size: int, first_block: int):
    """Atomically reserve a contiguous read-ahead window on the event loop."""
    first_key = (message_id, file_size, first_block)
    existing = media_cache_inflight.get(first_key)
    if existing is not None:
        return existing, []

    loop = asyncio.get_running_loop()
    total_blocks = math.ceil(file_size / MEDIA_CACHE_BLOCK_SIZE)
    claims = []
    for block_index in range(first_block, min(total_blocks, first_block + MEDIA_PREFETCH_BLOCKS)):
        key = (message_id, file_size, block_index)
        # Do not overlap another contiguous downloader. The first block was
        # checked above; encountering an in-flight read here simply shortens
        # this prefetch window.
        if key in media_cache_inflight:
            break
        future = loop.create_future()
        future.add_done_callback(_consume_future_exception)
        media_cache_inflight[key] = future
        claims.append((key, block_index, future))
    return claims[0][2], claims


async def _download_media_batch(message, details: dict, message_id: int, claims) -> None:
    """Download several adjacent cache blocks through one Telegram iterator."""
    iterator = None
    buffered = bytearray()
    claim_position = 0
    first_block = claims[0][1]
    expected_total = sum(
        min(MEDIA_CACHE_BLOCK_SIZE, details["size"] - block_index * MEDIA_CACHE_BLOCK_SIZE)
        for _key, block_index, _future in claims
    )
    received = 0

    try:
        async with stream_slots:
            try:
                iterator = telegram.iter_download(
                    message.document,
                    offset=first_block * MEDIA_CACHE_BLOCK_SIZE,
                    limit=len(claims),
                    chunk_size=MEDIA_CACHE_BLOCK_SIZE,
                    request_size=MEDIA_CACHE_BLOCK_SIZE,
                    file_size=details["size"],
                )
                async for chunk in iterator:
                    chunk = bytes(chunk)
                    received += len(chunk)
                    buffered.extend(chunk)

                    while claim_position < len(claims):
                        key, block_index, future = claims[claim_position]
                        expected_size = min(
                            MEDIA_CACHE_BLOCK_SIZE,
                            details["size"] - block_index * MEDIA_CACHE_BLOCK_SIZE,
                        )
                        if len(buffered) < expected_size:
                            break
                        data = bytes(buffered[:expected_size])
                        del buffered[:expected_size]
                        path = _media_cache_path(message_id, details["size"], block_index)
                        await asyncio.to_thread(_write_cache_block, path, data)
                        if not future.done():
                            future.set_result(data)
                        if media_cache_inflight.get(key) is future:
                            media_cache_inflight.pop(key, None)
                        claim_position += 1
            finally:
                if iterator is not None:
                    await iterator.close()

        if claim_position != len(claims):
            raise RuntimeError(
                f"Telegram returned {received} of {expected_total} bytes "
                f"for message {message_id} blocks {first_block}-{claims[-1][1]}"
            )
        await _maybe_cleanup_media_cache()
    except asyncio.CancelledError:
        for _key, _block_index, future in claims[claim_position:]:
            if not future.done():
                future.cancel()
        raise
    except Exception as exc:
        logger.warning(
            "Media prefetch failed message=%s blocks=%s-%s: %s",
            message_id,
            first_block,
            claims[-1][1],
            exc,
        )
        for _key, _block_index, future in claims[claim_position:]:
            if not future.done():
                future.set_exception(exc)
        raise
    finally:
        for key, _block_index, future in claims:
            if media_cache_inflight.get(key) is future:
                media_cache_inflight.pop(key, None)


async def _media_block(message, details: dict, message_id: int, block_index: int) -> tuple[bytes, bool]:
    block_start = block_index * MEDIA_CACHE_BLOCK_SIZE
    expected_size = min(MEDIA_CACHE_BLOCK_SIZE, details["size"] - block_start)
    path = _media_cache_path(message_id, details["size"], block_index)
    cached = await asyncio.to_thread(_read_cache_block, path, expected_size)
    if cached is not None:
        return cached, True

    requested, claims = _claim_media_batch(message_id, details["size"], block_index)
    if not claims:
        # A concurrent request or an earlier read-ahead operation owns this
        # block. Shield it so cancelling one HTTP request does not cancel the
        # shared Telegram download.
        return await asyncio.shield(requested), True

    task = asyncio.create_task(_download_media_batch(message, details, message_id, claims))
    media_prefetch_tasks.add(task)
    task.add_done_callback(_finish_prefetch_task)
    return await asyncio.shield(requested), False


def _media_headers(message_id: int, details: dict, start: int, end: int, partial: bool) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1),
        "Content-Type": details["mime_type"],
        "Cache-Control": "private, max-age=3600",
        "Content-Encoding": "identity",
        "ETag": f'"tg-{message_id}-{details["size"]}"',
        "Vary": "Range",
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
    return Response(status_code=200, headers=_media_headers(message_id, details, start, end, partial))


@app.get("/api/media/{message_id}")
async def media(message_id: int, request: Request):
    started_at = time.monotonic()
    message, details, byte_range = await _resolve_media(message_id, request.headers.get("range"))
    if byte_range is None:
        return Response(
            status_code=416,
            headers={"Content-Range": f'bytes */{details["size"]}', "Accept-Ranges": "bytes"},
        )
    start, end, partial = byte_range
    remaining = end - start + 1

    async def body():
        delivered = 0
        cache_hits = 0
        cache_misses = 0
        first_block = start // MEDIA_CACHE_BLOCK_SIZE
        last_block = end // MEDIA_CACHE_BLOCK_SIZE
        try:
            for block_index in range(first_block, last_block + 1):
                data, cache_hit = await _media_block(message, details, message_id, block_index)
                cache_hits += int(cache_hit)
                cache_misses += int(not cache_hit)
                block_start = block_index * MEDIA_CACHE_BLOCK_SIZE
                slice_start = max(start, block_start) - block_start
                slice_end = min(end + 1, block_start + len(data)) - block_start
                selected = data[slice_start:slice_end]
                if selected:
                    delivered += len(selected)
                    yield selected
        finally:
            logger.info(
                "Media range message=%s bytes=%s-%s delivered=%s cache=%s/%s elapsed=%.3fs",
                message_id,
                start,
                end,
                delivered,
                cache_hits,
                cache_hits + cache_misses,
                time.monotonic() - started_at,
            )

    return StreamingResponse(
        body(),
        status_code=206 if partial else 200,
        media_type=details["mime_type"],
        headers=_media_headers(message_id, details, start, end, partial),
    )
