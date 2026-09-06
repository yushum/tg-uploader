import asyncio
import json
import logging
import signal
from collections import deque
import math
import os
import random
import re
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.tl import functions
from telethon.tl.types import DocumentAttributeVideo, InputGroupCall

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
    global telegram, live_task, live_process
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
    _init_favorites_db()
    logger.info("Telegram streaming client connected")
    try:
        yield
    finally:
        if media_prefetch_tasks:
            tasks = tuple(media_prefetch_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        # 关闭推流：杀 FFmpeg + 取消 worker，避免残留进程
        live_stop_event.set()
        if live_process is not None:
            try:
                live_process.kill()
            except Exception:
                pass
        if live_task is not None:
            live_task.cancel()
            try:
                await live_task
            except (asyncio.CancelledError, Exception):
                pass
            live_task = None
        live_state.update(status="IDLE", current_message_id=None, current_label="")
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


def _init_favorites_db():
    try:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS favorites (
                    message_id INTEGER PRIMARY KEY,
                    channel TEXT NOT NULL,
                    date TEXT NOT NULL,
                    time TEXT DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    except sqlite3.Error as exc:
        logger.error("Could not initialize favorites table: %s", exc)


class FavoriteItem(BaseModel):
    message_id: int
    channel: str
    date: str
    time: str = ""


class BatchFavoritesItem(BaseModel):
    items: list[FavoriteItem]


class LiveStartRequest(BaseModel):
    channel: str
    message_ids: list[int]
    mode: str = "once"


live_state = {
    "status": "IDLE",  # IDLE / STREAMING
    "channel": "",
    "rtmp": "",
    "message_ids": [],
    "index": 0,
    "mode": "once",  # once 单次 / loop 循环 / shuffle 随机
    "round": 0,  # 当前第几轮（循环/随机模式下递增）
    "bytes_sent": 0,  # 当前片段已喂给 ffmpeg 的字节数
    "picture": "",  # copy 原画直推 / transcode 适配转码
    "current_message_id": None,
    "current_label": "",
    "error": "",
}
LIVE_PLAY_MODES = ("once", "loop", "shuffle")
live_task: asyncio.Task | None = None
live_process: asyncio.subprocess.Process | None = None
live_stop_event = asyncio.Event()


def _live_label(message_id: int) -> str:
    """从录像目录解析 message_id 对应的『主播 日期 时间 场次』描述。"""
    try:
        for part in _catalog():
            if part.message_id == message_id:
                return f"{part.streamer} {part.date} {part.time[:5]} {part.part_label}"
    except Exception:
        pass
    return f"id={message_id}"


async def _ensure_group_call_live(channel_peer) -> None:
    """在目标频道开播（已开播则忽略异常），需要管理视频聊天权限。"""
    try:
        # random_id 是 32 位整型，超范围会在本地序列化失败，直播间实际建不起来。
        await telegram(functions.phone.CreateGroupCallRequest(
            peer=channel_peer, rtmp_stream=True, random_id=random.randint(1, 2**31 - 1),
        ))
    except Exception as exc:
        name = type(exc).__name__.upper()
        msg = f"{name}: {exc}".upper()
        # 已有直播则视为成功
        if "ALREADY" in msg or "ANONYM" in msg:
            logger.info("Group call already active, continue: %s", exc)
            return
        # 权限/频道类错误直接抛给调用方，status 会显示明确原因
        if any(key in msg for key in ("ADMIN", "FORBIDDEN", "RIGHTS", "PRIVACY", "NOTMEMBER", "NO_MEMBERS")):
            raise
        # 未知错误绝不能当作“已开播”放行，否则会出现网页显示推流中、
        # 频道却没有直播的假象。
        logger.error("CreateGroupCall failed: %s: %s", type(exc).__name__, exc)
        raise


LIVE_STREAM_FETCH_TIMEOUT = 60
# ffmpeg 输出进度超过此时长毫无增长，判定为停滞并跳过，避免无限假推流。
LIVE_STREAM_PROGRESS_TIMEOUT = 120
MAX_CONSECUTIVE_STALLS = 3
WEB_PORT = int(os.getenv("WEB_PORT", "31527"))


async def _drain_ffmpeg_stderr(proc, log: deque) -> None:
    """把 ffmpeg 的 stderr 收进环形缓冲，出问题时才能看到错因（原来直接丢黑洞）。"""
    try:
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            log.append(line.decode("utf-8", "replace").rstrip())
    except Exception:
        pass


def _ffmpeg_log_tail(log: deque) -> str:
    return " | ".join(log) if log else "(无输出)"


LIVE_PROBE_TIMEOUT = 30
LIVE_CANVAS_W, LIVE_CANVAS_H = 1920, 1080


def _parse_ratio(text, default=1.0):
    """解析 ffprobe 的 'N:M' 比例，缺失/非法时回 1.0。"""
    try:
        num, den = str(text or "").split(":")
        num, den = float(num), float(den)
        if num > 0 and den > 0:
            return num / den
    except (ValueError, AttributeError):
        pass
    return default


def _stream_rotation(stream: dict) -> int:
    """取视频流旋转角度（tags.rotate 或 H.264 SEI 显示矩阵），归一到 0/90/180/270。"""
    candidates = []
    try:
        candidates.append(float((stream.get("tags") or {}).get("rotate", 0) or 0))
    except (ValueError, TypeError):
        pass
    for side in stream.get("side_data_list") or []:
        try:
            if "rotation" in side:
                candidates.append(float(side["rotation"]))
        except (ValueError, TypeError):
            pass
    for value in candidates:
        normalized = int(round(value)) % 360
        if normalized:
            return normalized
    return 0


def _parse_probe_streams(data: dict):
    """从 ffprobe JSON 取首个视频流信息，拿不到返回 None（调用方回退 copy）。"""
    for stream in (data or {}).get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        try:
            width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        except (ValueError, TypeError):
            continue
        if width <= 0 or height <= 0:
            continue
        return {
            "codec": str(stream.get("codec_name") or "").lower(),
            "width": width,
            "height": height,
            "sar": _parse_ratio(stream.get("sample_aspect_ratio")),
            "rotation": _stream_rotation(stream),
        }
    return None


async def _probe_live_video(media_url: str):
    """探测待播视频的编码/尺寸/SAR/旋转，失败返回 None（回退 copy，不断播）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-show_streams", "-of", "json", media_url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=LIVE_PROBE_TIMEOUT)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                pass
            return None
        if proc.returncode != 0:
            return None
        return _parse_probe_streams(json.loads((out or b"").decode("utf-8", "replace") or "{}"))
    except Exception as exc:
        logger.warning("live probe failed: %s: %s", type(exc).__name__, exc)
        return None


def _live_picture_plan(info):
    """返回 (vf_or_None, 说明)。Telegram 直播按 16:9 输出，只有标准横屏 H.264 可 copy；
    其他一律保持比例转码垫到 1920x1080，绝不拉伸。"""
    if not info:
        return None, "探测失败，原画直推"
    width, height = info["width"], info["height"]
    rotation = info.get("rotation") or 0
    sar = info.get("sar") or 1.0
    if rotation % 180 != 0:
        width, height = height, width  # 解码端 autorotate 后的有效尺寸
    dar = (width * sar) / height
    clean = (info.get("codec") == "h264" and abs(sar - 1.0) < 0.01
             and rotation % 360 == 0 and width % 2 == 0 and height % 2 == 0
             and abs(dar - 16 / 9) / (16 / 9) < 0.02)
    if clean:
        return None, "原画直推"
    # 背景用同一画面放大裁剪+重度虚化代替纯黑边，前景保持比例居中。虚化在
    # 缩略图上做完再放大，回放成本可忽略，主体编码开销与纯黑边垫播相当。
    head = []
    if abs(sar - 1.0) >= 0.01:
        head.append(f"scale=iw*{sar:.4f}:ih")
    base = ",".join(head) + "," if head else ""
    vf = (
        f"{base}split=2[bg][fg];"
        "[bg]scale=1920:1080:force_original_aspect_ratio=increase,"
        "crop=1920:1080,scale=160:90,gblur=sigma=30,scale=1920:1080[bg];"
        "[fg]scale=1920:1080:force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
    )
    reasons = []
    if info.get("codec") != "h264":
        reasons.append(info.get("codec") or "未知编码")
    if rotation % 360:
        reasons.append(f"旋转{rotation}°")
    if abs(dar - 16 / 9) / (16 / 9) >= 0.02:
        reasons.append(f"{width}x{height}非16:9")
    if abs(sar - 1.0) >= 0.01:
        reasons.append("像素非正方形")
    return vf, "适配转码(" + "、".join(reasons) + ")"


async def _track_ffmpeg_progress(proc, progress: dict) -> None:
    """解析 ffmpeg -progress 输出，把真实输出字节数写回状态。只在增长时刷新心跳。"""
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if text.startswith("total_size="):
                try:
                    size = int(text.split("=", 1)[1])
                except ValueError:
                    continue
                if size > progress["bytes"]:
                    progress["bytes"] = size
                    progress["tick"] = time.monotonic()
                    live_state.update(bytes_sent=size)
    except Exception:
        pass


async def _watch_ffmpeg_progress(proc, progress: dict, timeout: float | None = None) -> None:
    """输出进度长期不涨就杀掉 ffmpeg，主流程据 stalled 标记跳过本片段。"""
    if timeout is None:
        timeout = LIVE_STREAM_PROGRESS_TIMEOUT
    try:
        interval = min(10.0, timeout)
        while proc.returncode is None:
            await asyncio.sleep(interval)
            if proc.returncode is not None:
                break
            if time.monotonic() - progress["tick"] > timeout:
                progress["stalled"] = True
                try:
                    proc.kill()
                except Exception:
                    pass
                return
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def _get_rtmp_url(channel_peer) -> str:
    result = await telegram(functions.phone.GetGroupCallStreamRtmpUrlRequest(peer=channel_peer, revoke=False))
    url = getattr(result, "url", "") or ""
    key = getattr(result, "key", "") or ""
    if not url:
        raise RuntimeError("Telegram 未返回 RTMP 地址（请确认账号拥有管理视频聊天权限）")
    return f"{url.rstrip('/')}/{key}" if key else url


def _kill_stray_rtmp_ffmpeg(proc_dir: str = "/proc") -> int:
    """兜底：杀掉所有命令行含 rtmps:// 的 ffmpeg（推流进程）。
    缩略图/转封装只写本地文件，从不碰网络，不会误伤。proc_dir 仅供单测注入。"""
    try:
        pids = [p for p in os.listdir(proc_dir) if p.isdigit()]
    except Exception as exc:
        logger.warning("stray ffmpeg sweep: cannot list %s: %s", proc_dir, exc)
        return 0
    killed = 0
    for pid in pids:
        try:
            with open(os.path.join(proc_dir, pid, "cmdline"), "rb") as fh:
                cmdline = fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except Exception:
            continue
        if "ffmpeg" not in cmdline or "rtmps://" not in cmdline:
            continue
        try:
            os.kill(int(pid), signal.SIGKILL)
            killed += 1
            logger.warning("stray ffmpeg sweep: killed pid=%s (%.120s)", pid, cmdline)
        except Exception:
            continue
    if killed:
        logger.warning("stray ffmpeg sweep: killed %d process(es)", killed)
    return killed


async def _discard_group_call(channel: str) -> bool:
    """尽力挂断频道的群组通话，让直播间彻底消失。失败只记录不抛异常，绝不破坏停止流程。"""
    if not channel or telegram is None:
        return False
    try:
        entity = await telegram.get_entity(channel)
    except Exception as exc:
        logger.warning("discard group call: resolve %s failed: %s", channel, exc)
        return False
    try:
        full = await telegram(functions.channels.GetFullChannelRequest(channel=entity))
        call_ref = getattr(getattr(full, "full_chat", None), "call", None)
        call_id = getattr(call_ref, "id", None)
        access_hash = getattr(call_ref, "access_hash", None)
        if not call_id or access_hash is None:
            logger.info("discard group call: no active call on %s", channel)
            return False
    except Exception as exc:
        logger.warning("discard group call: query %s failed: %s: %s",
                       channel, type(exc).__name__, exc)
        return False
    try:
        await telegram(functions.phone.DiscardGroupCallRequest(
            call=InputGroupCall(call_id, access_hash)))
        logger.info("discard group call: call %s on %s discarded", call_id, channel)
        return True
    except Exception as exc:
        logger.warning("discard group call failed: %s: %s", type(exc).__name__, exc)
        return False


async def _live_worker(channel: str, message_ids: list[int], mode: str = "once") -> None:
    global live_process
    live_stop_event.clear()
    try:
        entity = await telegram.get_entity(channel)
    except Exception as exc:
        live_state.update(status="IDLE", error=f"目标频道无效: {exc}")
        return
    try:
        await _ensure_group_call_live(entity)
        rtmp = await _get_rtmp_url(entity)
    except Exception as exc:
        logger.error("RTMP setup failed: %s", exc)
        live_state.update(status="IDLE", error=str(exc))
        return
    live_state.update(rtmp=rtmp, status="STREAMING", error="")
    logger.info("Live streaming to %s (%d videos, mode=%s)", channel, len(message_ids), mode)
    order = list(message_ids)
    if mode == "shuffle":
        random.shuffle(order)
    round_no = 0
    stalls = 0
    probe_cache: dict[int, object] = {}
    while True:
        round_no += 1
        live_state.update(round=round_no)
        for position, message_id in enumerate(order):
            if live_stop_event.is_set():
                break
            live_state.update(index=position + 1, current_message_id=message_id,
                               current_label=_live_label(message_id))
            try:
                message = await asyncio.wait_for(
                    telegram.get_messages(CHANNEL_ID, ids=message_id),
                    timeout=LIVE_STREAM_FETCH_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error("Stream video %s: 获取录像信息超时，已跳过", message_id)
                live_state.update(error=f"片段 {message_id} 获取超时，已跳过")
                continue
            except Exception as exc:
                logger.error("Stream video %s lookup failed: %s: %s",
                             message_id, type(exc).__name__, exc)
                live_state.update(error=f"片段 {message_id} 读取失败，已跳过")
                continue
            if not message or not getattr(message, "document", None):
                logger.warning("Skip missing message %s", message_id)
                continue
            live_state.update(bytes_sent=0)
            stderr_task = None
            progress_task = None
            watch_task = None
            progress = {"bytes": 0, "tick": time.monotonic(), "stalled": False}
            ffmpeg_log: deque[str] = deque(maxlen=30)
            try:
                # 直读本地媒体接口（支持 Range seek），moov 在文件尾也能播；
                # 管道喂数据遇到 moov 在尾的 MP4 会卡死在探针阶段。
                media_url = f"http://127.0.0.1:{WEB_PORT}/api/media/{message_id}"
                if message_id not in probe_cache:
                    probe_cache[message_id] = await _probe_live_video(media_url)
                vf, picture_note = _live_picture_plan(probe_cache[message_id])
                picture = "copy" if vf is None else "transcode"
                live_state.update(picture=picture)
                logger.info("Stream video %s: %s", message_id, picture_note)
                video_args = ["-c:v", "copy"] if vf is None else [
                    "-filter_complex", vf, "-c:v", "libx264", "-preset", "veryfast",
                    "-tune", "zerolatency", "-crf", "23", "-g", "60",
                ]
                live_process = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-hide_banner", "-loglevel", "warning", "-re",
                    "-i", media_url,
                    *video_args, "-c:a", "aac",
                    "-f", "flv", "-flvflags", "no_duration_filesize",
                    "-progress", "pipe:1",
                    rtmp,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                logger.info("Stream video %s: ffmpeg pid=%s started", message_id,
                            getattr(live_process, "pid", "?"))
                if live_stop_event.is_set():
                    # 停止恰好落在产卵瞬间：立刻处决刚出生的进程，绝不留下无人追踪的孤儿。
                    logger.warning("Stop arrived during ffmpeg spawn, killing pid=%s at once",
                                   getattr(live_process, "pid", "?"))
                    try:
                        live_process.kill()
                    except Exception:
                        pass
                    live_process = None
                    break
                stderr_task = asyncio.create_task(_drain_ffmpeg_stderr(live_process, ffmpeg_log))
                progress_task = asyncio.create_task(_track_ffmpeg_progress(live_process, progress))
                watch_task = asyncio.create_task(_watch_ffmpeg_progress(live_process, progress))
            except Exception as exc:
                logger.error("ffmpeg spawn failed: %s", exc)
                live_state.update(error=str(exc))
                continue
            try:
                await live_process.wait()
                if live_stop_event.is_set():
                    break
                if progress["stalled"]:
                    stalls += 1
                    logger.error("Stream video %s: 输出停滞超过 %ss（连续 %s 次），ffmpeg 说：%s",
                                 message_id, LIVE_STREAM_PROGRESS_TIMEOUT, stalls,
                                 _ffmpeg_log_tail(ffmpeg_log))
                    if stalls >= MAX_CONSECUTIVE_STALLS:
                        live_state.update(error=f"连续{stalls}个片段推流停滞，已停止推流")
                        live_stop_event.set()
                    else:
                        live_state.update(error=f"片段 {message_id} 推流停滞，已跳过")
                    continue
                if live_process.returncode != 0:
                    logger.error("Stream video %s: ffmpeg 异常退出（code=%s），ffmpeg 说：%s",
                                 message_id, live_process.returncode,
                                 _ffmpeg_log_tail(ffmpeg_log))
                    live_state.update(error=f"片段 {message_id} 推流异常退出，已跳过")
                    continue
                stalls = 0
            except Exception as exc:
                logger.error("Stream video %s failed: %s: %s, ffmpeg 说：%s",
                             message_id, type(exc).__name__, exc, _ffmpeg_log_tail(ffmpeg_log))
                try:
                    live_process.kill()
                except Exception:
                    pass
            finally:
                for task in (stderr_task, progress_task, watch_task):
                    if task is not None and not task.done():
                        task.cancel()
                for task in (stderr_task, progress_task, watch_task):
                    if task is not None:
                        try:
                            await task
                        except (asyncio.CancelledError, Exception):
                            pass
                live_process = None
        if live_stop_event.is_set() or mode == "once":
            break
        if mode == "shuffle":
            random.shuffle(order)
            logger.info("Live reshuffled for round %d", round_no + 1)
        else:
            logger.info("Live looping round %d", round_no + 1)
    live_state.update(status="IDLE", current_message_id=None, current_label="", round=0,
                        picture="")
    logger.info("Live worker finished")


@app.post("/api/live/start")
async def live_start(body: LiveStartRequest):
    global live_task
    channel = (body.channel or "").strip()
    if not channel:
        raise HTTPException(status_code=400, detail="请填写目标频道")
    if not body.message_ids:
        raise HTTPException(status_code=400, detail="请至少勾选一个录像")
    if telegram is None:
        raise HTTPException(status_code=503, detail="Telegram 尚未连接")
    if live_state["status"] == "STREAMING":
        raise HTTPException(status_code=409, detail="已有推流任务，请先停止")
    mode = (body.mode or "once").strip().lower()
    if mode not in LIVE_PLAY_MODES:
        raise HTTPException(status_code=400, detail=f"播放模式错误: {body.mode}（可选 once/loop/shuffle）")
    message_ids = [int(m) for m in body.message_ids]
    live_stop_event.clear()
    # 开播前先清场：干掉历史上泄漏的幽灵推流进程，防止新旧流打架。
    _kill_stray_rtmp_ffmpeg()
    live_state.update(status="STREAMING", channel=channel, message_ids=message_ids, index=0,
                        mode=mode, round=0,
                        current_message_id=message_ids[0], current_label=_live_label(message_ids[0]), error="")
    live_task = asyncio.create_task(_live_worker(channel, message_ids, mode))
    return {"ok": True, "channel": channel, "count": len(message_ids), "mode": mode}


@app.post("/api/live/stop")
async def live_stop():
    global live_task, live_process
    live_stop_event.set()
    if live_process is not None:
        try:
            logger.info("live stop: killing tracked ffmpeg pid=%s",
                        getattr(live_process, "pid", "?"))
            live_process.kill()
        except Exception as exc:
            logger.warning("live stop: kill tracked ffmpeg failed: %s", exc)
    else:
        logger.info("live stop: no tracked ffmpeg running")
    # 即使追踪丢了，扫一遍也把幽灵推流掐死，不给“自己重开”留机会。
    _kill_stray_rtmp_ffmpeg()
    if live_task is not None:
        live_task.cancel()
        try:
            await live_task
        except (asyncio.CancelledError, Exception):
            pass
        live_task = None
    # 挂断群组通话：即使有漏网的推送，直播间也不复活。
    await _discard_group_call(live_state.get("channel") or "")
    live_state.update(status="IDLE", current_message_id=None, current_label="",
                        mode="once", round=0, picture="")
    return {"ok": True}


MAX_LIVE_CANDIDATES = 10000
# 超过此数量时跳过 Telegram 逐一核验，直接返回本地目录（开播时会自动跳过失效片段），
# 避免数千个候选的串行核验把查找请求拖住几分钟。
LIVE_VERIFY_THRESHOLD = 800
LIVE_VERIFY_CONCURRENCY = 4
LIVE_VERIFY_BATCH_TIMEOUT = 25


async def _verify_live_batch(ids: list[int]) -> dict[int, dict] | None:
    """核验一批录像是否仍在频道中。失败返回 None（未知），成功返回已确认有效的信息。"""
    try:
        messages = await asyncio.wait_for(
            telegram.get_messages(CHANNEL_ID, ids=ids), timeout=LIVE_VERIFY_BATCH_TIMEOUT)
    except Exception as exc:
        logger.warning("live candidates batch lookup failed (%d ids): %s: %s",
                         len(ids), type(exc).__name__, exc)
        return None
    if not isinstance(messages, list):
        messages = [messages]
    verified: dict[int, dict] = {}
    for message in messages:
        if message is None or not getattr(message, "document", None):
            continue
        details = _video_details(message)
        if details:
            verified[message.id] = details
    return verified


@app.get("/api/live/candidates")
async def live_candidates(
    streamer: list[str] = Query(default=[]),
    from_date: str = "",
    to_date: str = "",
    exclude: list[str] = Query(default=[]),
):
    """按主播（多选，为空=全部）+日期范围（为空=不限）+排除日期，一次列出可推流录像。"""
    names = {name.strip() for chunk in streamer for name in chunk.split(",") if name.strip()}
    excluded = {day.strip() for chunk in exclude for day in chunk.replace("，", ",").split(",") if day.strip()}
    for value in (from_date, to_date, *excluded):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise HTTPException(status_code=400, detail=f"日期格式错误: {value}")
    parts = sorted(
        [part for part in _catalog()
         if (not names or part.streamer in names)
         and (not from_date or part.date >= from_date)
         and (not to_date or part.date <= to_date)
         and part.date not in excluded],
        key=lambda part: (part.date, part.time, part.message_id),
    )[:MAX_LIVE_CANDIDATES]
    details_by_id: dict[int, dict] = {}
    verified_ids: set[int] = set()
    check_availability = bool(parts) and telegram is not None and len(parts) <= LIVE_VERIFY_THRESHOLD
    if check_availability:
        batches = [[part.message_id for part in parts][offset:offset + 200]
                   for offset in range(0, len(parts), 200)]
        semaphore = asyncio.Semaphore(LIVE_VERIFY_CONCURRENCY)

        async def _checked(batch: list[int]):
            async with semaphore:
                return batch, await _verify_live_batch(batch)

        for batch, result in await asyncio.gather(*(_checked(batch) for batch in batches)):
            if result is None:
                continue
            verified_ids.update(batch)
            details_by_id.update(result)
    items = []
    for part in parts:
        verified = part.message_id in verified_ids
        items.append({
            "message_id": part.message_id,
            "streamer": part.streamer,
            "date": part.date,
            "time": part.time[:5],
            "part_label": part.part_label,
            "label": f"{part.date} {part.time[:5]} {part.part_label}",
            "duration": details_by_id.get(part.message_id, {}).get("duration", 0),
            # 未核验（结果太多跳过核验 / 该批核验失败 / Telegram 未连接）时保持可选，
            # 开播 worker 遇到已删除的片段会自动跳过。
            "available": (part.message_id in details_by_id) if verified else True,
            "verified": verified,
        })
    return items


@app.get("/api/live/status")
async def live_status():
    return {
        "status": live_state["status"],
        "channel": live_state["channel"],
        "current_message_id": live_state["current_message_id"],
        "current": live_state["current_label"],
        "total": len(live_state["message_ids"]),
        "index": live_state["index"],
        "bytes_sent": live_state.get("bytes_sent", 0),
        "picture": live_state.get("picture", ""),
        "mode": live_state.get("mode", "once"),
        "round": live_state.get("round", 0),
        "error": live_state["error"],
    }


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


@app.get("/live.js", include_in_schema=False)
async def live_studio_script():
    return FileResponse(FRONTEND_DIR / "live.js", media_type="text/javascript")


@app.get("/live.css", include_in_schema=False)
async def live_studio_style():
    return FileResponse(FRONTEND_DIR / "live.css", media_type="text/css")


PWA_ICONS = frozenset({"icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png"})


@app.get("/manifest.webmanifest", include_in_schema=False)
async def pwa_manifest():
    return FileResponse(FRONTEND_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def pwa_service_worker():
    # SW 自身绝不能被缓存，否则发版后客户端收不到更新。
    return FileResponse(
        FRONTEND_DIR / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/icons/{filename}", include_in_schema=False)
async def pwa_icon(filename: str):
    if filename not in PWA_ICONS:
        raise HTTPException(status_code=404, detail="图标不存在")
    return FileResponse(
        FRONTEND_DIR / "icons" / filename,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400"},
    )


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


@app.get("/api/favorites")
async def get_favorites():
    _init_favorites_db()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT message_id, channel, date, time, created_at FROM favorites ORDER BY created_at DESC, message_id DESC"
            ).fetchall()
            return [
                {
                    "message_id": row["message_id"],
                    "channel": row["channel"],
                    "date": row["date"],
                    "time": row["time"] or "",
                    "created_at": row["created_at"] or "",
                }
                for row in rows
            ]
    except sqlite3.Error as exc:
        logger.error("Could not read favorites: %s", exc)
        raise HTTPException(status_code=500, detail="读取收藏失败") from exc


@app.post("/api/favorites")
async def save_favorite(item: FavoriteItem):
    _init_favorites_db()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO favorites (message_id, channel, date, time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    channel = excluded.channel,
                    date = excluded.date,
                    time = excluded.time
                """,
                (item.message_id, item.channel, item.date, item.time),
            )
            conn.commit()
            return {"ok": True, "message_id": item.message_id}
    except sqlite3.Error as exc:
        logger.error("Could not save favorite: %s", exc)
        raise HTTPException(status_code=500, detail="保存收藏失败") from exc


@app.post("/api/favorites/batch")
async def batch_save_favorites(batch: BatchFavoritesItem):
    _init_favorites_db()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.executemany(
                """
                INSERT INTO favorites (message_id, channel, date, time)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    channel = excluded.channel,
                    date = excluded.date,
                    time = excluded.time
                """,
                [(item.message_id, item.channel, item.date, item.time) for item in batch.items],
            )
            conn.commit()
            return {"ok": True, "count": len(batch.items)}
    except sqlite3.Error as exc:
        logger.error("Could not batch save favorites: %s", exc)
        raise HTTPException(status_code=500, detail="批量保存收藏失败") from exc


@app.delete("/api/favorites/{message_id}")
async def delete_favorite(message_id: int):
    _init_favorites_db()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM favorites WHERE message_id = ?", (message_id,))
            conn.commit()
            return {"ok": True, "message_id": message_id}
    except sqlite3.Error as exc:
        logger.error("Could not delete favorite: %s", exc)
        raise HTTPException(status_code=500, detail="取消收藏失败") from exc


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
