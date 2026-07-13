import os
import asyncio
import sqlite3
import logging
import signal
import re
import json
import math
import shutil
import uuid
from pathlib import Path
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import DocumentAttributeVideo
# ---------------- 日志配置 ----------------
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s][%(levelname)s][%(module)s] %(message)s'
)
# Suppress overly verbose logs from Telethon
logging.getLogger('telethon').setLevel(logging.WARNING)
logger = logging.getLogger('tg-uploader')

def get_positive_int_env(name: str, default: int, *, minimum: int = 1, maximum: int = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(f"{name}={raw} invalid, using default {default}")
        return default
    if value < minimum:
        logger.warning(f"{name}={value} must be >= {minimum}, using default {default}")
        return default
    if maximum is not None and value > maximum:
        logger.warning(f"{name}={value} must be <= {maximum}, using default {default}")
        return default
    return value

# ---------------- Configuration ----------------
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
WATCH_DIR = os.getenv('WATCH_DIR', '/downloads')
SESSION_NAME = os.getenv('SESSION_NAME', '/app/session/uploader')
DB_PATH = os.getenv('DB_PATH', '/app/session/uploader.db')
MAX_SPLIT_SIZE_MB = get_positive_int_env('MAX_SPLIT_SIZE_MB', 2000, minimum=50, maximum=4000)
MAX_CONCURRENT_UPLOADS = get_positive_int_env('MAX_CONCURRENT_UPLOADS', 1, minimum=1, maximum=10)
CHECK_INTERVAL = 15  # File stability check interval in seconds

# ---------------- Customization & Network ----------------
DEVICE_MODEL = os.getenv('DEVICE_MODEL', 'TG-Uploader-Pro')
SYSTEM_VERSION = os.getenv('SYSTEM_VERSION', 'Linux')
APP_VERSION = os.getenv('APP_VERSION', '2.0')

# Proxy Configuration (e.g., PROXY_TYPE=socks5, PROXY_HOST=127.0.0.1, PROXY_PORT=1080)
PROXY_TYPE = os.getenv('PROXY_TYPE', '')
PROXY_HOST = os.getenv('PROXY_HOST', '')
PROXY_PORT = os.getenv('PROXY_PORT', '')

is_running = True

def handle_shutdown_signal(signum, frame):
    global is_running
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    is_running = False

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGTERM, handle_shutdown_signal)
signal.signal(signal.SIGINT, handle_shutdown_signal)

# ---------------- Database ----------------
def init_db() -> sqlite3.Connection:
    """初始化 SQLite 数据库并显式开启 WAL 模式"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS uploads (
            filepath TEXT PRIMARY KEY,
            status TEXT,
            message_id INTEGER,
            uploaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        conn.execute('ALTER TABLE uploads ADD COLUMN message_id INTEGER;')
    except sqlite3.OperationalError:
        pass  # 字段已存在

    # 关键健壮性修复：重启时，将所有因为意外中断而卡在 UPLOADING 状态的任务重置，让其能够被重新扫描上传
    conn.execute('UPDATE uploads SET status = "PENDING" WHERE status = "UPLOADING"')
    conn.commit()
    return conn

def get_upload_status(conn: sqlite3.Connection, filepath: str) -> str:
    """查询文件的上传状态"""
    cursor = conn.execute('SELECT status FROM uploads WHERE filepath = ?', (filepath,))
    row = cursor.fetchone()
    return row[0] if row else None

def update_upload_status(conn: sqlite3.Connection, filepath: str, status: str, message_id: int = None):
    """更新/插入文件的上传状态及可选的 TG 消息 ID (防重复)"""
    conn.execute('''
        INSERT INTO uploads (filepath, status, message_id) VALUES (?, ?, ?)
        ON CONFLICT(filepath) DO UPDATE SET 
            status=excluded.status, 
            message_id=COALESCE(excluded.message_id, uploads.message_id), 
            uploaded_at=CURRENT_TIMESTAMP
    ''', (filepath, status, message_id))
    conn.commit()

def get_upload_message_id(conn: sqlite3.Connection, dir_path: str, prefix_pattern: str) -> int:
    """利用精确路径前缀进行索引查询，避免全表扫描和通配符污染"""
    raw_pattern = os.path.join(dir_path, prefix_pattern)
    safe_pattern = raw_pattern.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + "%"
    cursor = conn.execute('SELECT message_id FROM uploads WHERE filepath LIKE ? ESCAPE "\\" AND status = "COMPLETED" AND message_id IS NOT NULL', (safe_pattern,))
    row = cursor.fetchone()
    return row[0] if row else None

# ---------------- 视频处理模块 ----------------
async def generate_thumbnail(video_path: str) -> str:
    """
    使用 FFmpeg 截取视频封面。
    性能优化：将 -ss 参数放在 -i 前面，实现快速寻道，防止 I/O 阻塞和 CPU 爆满。
    """
    # 如果处于只读挂载目录，将封面生成在临时目录，避免权限报错，且用 UUID 防并发冲突
    if not os.access(os.path.dirname(video_path), os.W_OK):
        thumb_path = os.path.join('/tmp', f"{os.path.basename(video_path)}_{uuid.uuid4().hex[:8]}.thumb.jpg")
    else:
        thumb_path = f"{video_path}.thumb.jpg"
        
    for ss in ['00:00:05', '00:00:00']:
        process = None
        try:
            cmd = [
                'ffmpeg', '-y',
                '-ss', ss,
                '-i', video_path,
                '-vframes', '1',
                '-vf', 'scale=320:-2',
                '-q:v', '5',
                thumb_path
            ]
            
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            # 增加 60 秒超时控制，防止 ffmpeg 因坏文件出现死锁/僵尸进程
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
            
            if process.returncode == 0 and os.path.exists(thumb_path):
                return thumb_path
            else:
                if stderr:
                    logger.warning(f"FFmpeg thumbnail error at {ss}: {stderr.decode(errors='ignore').strip()}")
        except asyncio.TimeoutError:
            logger.error(f"FFmpeg thumbnail generation timed out for {video_path} at {ss}")
            if process:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except Exception: pass
        except asyncio.CancelledError:
            if process:
                try: 
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except Exception: pass
            raise
        except Exception as e:
            logger.error(f"Failed to generate thumbnail for {video_path} at {ss}: {e}")
            
    return None

async def get_video_attributes(video_path: str):
    """使用 ffprobe 提取视频的时长、宽、高元数据，防止 Telegram 渲染为小方块或错误比例"""
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height:format=duration',
            '-of', 'json',
            video_path
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            # 增加 30 秒超时控制，防止 ffprobe 僵死
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=30)
            info = json.loads(stdout)
            
            streams = info.get('streams', [])
            width = int(streams[0].get('width', 0)) if streams else 0
            height = int(streams[0].get('height', 0)) if streams else 0
            duration = int(float(info.get('format', {}).get('duration', 0)))
            
            return width, height, duration
        except asyncio.TimeoutError:
            logger.error(f"ffprobe timed out for {video_path}")
            if process:
                try: 
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except Exception: pass
            return 0, 0, 0
        except asyncio.CancelledError:
            if process:
                try: 
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                except Exception: pass
            raise
    except Exception as e:
        logger.error(f"Failed to extract video attributes for {video_path}: {e}")
        return 0, 0, 0

# ---------------- 核心上传模块 ----------------
async def upload_file(client: TelegramClient, filepath: str, conn: sqlite3.Connection, semaphore: asyncio.Semaphore, fail_counts: dict):
    """处理视频的缩略图生成与 TG 异步流式上传"""
    if not is_running:
        return
        
    logger.info(f"Task queued for: {filepath}")
    
    async with semaphore:
        if not is_running:
            return
            
        logger.info(f"Starting processing and upload for: {filepath}")
        
        # [0字节文件防御机制] 如果由于录制异常产生了 0 字节的文件，直接抛弃并清理
        if os.path.exists(filepath) and os.path.getsize(filepath) == 0:
            logger.warning(f"File {filepath} is 0 bytes. Skipping upload and auto-cleaning.")
            update_upload_status(conn, filepath, 'SKIPPED_EMPTY_FILE')
            try:
                os.remove(filepath)
                dir_path = os.path.dirname(filepath)
                watch_dir_abs_list = [os.path.abspath(d.strip()) for d in WATCH_DIR.split(',') if d.strip()]
                while dir_path and os.path.abspath(dir_path) not in watch_dir_abs_list:
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                        logger.info(f"Empty directory auto-cleaned: {dir_path}")
                        dir_path = os.path.dirname(dir_path)
                    else:
                        break
            except Exception as e:
                logger.warning(f"Cleanup for 0-byte file failed: {e}")
            return
            
        update_upload_status(conn, filepath, 'UPLOADING')
        
        is_temp_mp4 = False
        upload_target_path = filepath
        thumb_path = None
        
        # [竞态防御终极机制]
        filename_no_ext = os.path.splitext(os.path.basename(filepath))[0]
        if not filepath.lower().endswith(('.ts', '.flv', '.mkv')):
            # 处理的是原生 MP4，检查是否有原始 TS 已经被上传过
            for ext in ['.ts', '.flv', '.mkv']:
                raw_file = os.path.join(os.path.dirname(filepath), f"{filename_no_ext}{ext}")
                if get_upload_status(conn, raw_file) in ('COMPLETED', 'UPLOADING'):
                    logger.info(f"Race condition averted: Raw counterpart ({ext}) was already rescued and uploaded. Skipping this MP4.")
                    update_upload_status(conn, filepath, 'SKIPPED_DUPLICATE_MP4')
                    try:
                        if os.path.exists(filepath): os.remove(filepath)
                    except Exception: pass
                    return
        else:
            # [竞态防御机制] 检查对应的 MP4 文件是否已经存在，或已经被成功上传
            possible_mp4 = os.path.join(os.path.dirname(filepath), f"{filename_no_ext}.mp4")
            if os.path.exists(possible_mp4) or get_upload_status(conn, possible_mp4) in ('COMPLETED', 'UPLOADING'):
                logger.info(f"Race condition averted: Native MP4 already exists or uploaded for {filepath}, skipping TS rescue.")
                update_upload_status(conn, filepath, 'SKIPPED_FOR_NATIVE_MP4')
                try:
                    if os.path.exists(filepath): os.remove(filepath)
                except Exception: pass
                return
                
            # 极速转封装：将 ts/flv/mkv 无损转换为 mp4，输出到可写的 session 临时目录，并加入 UUID 防止重名冲突
            unique_suffix = uuid.uuid4().hex[:8]
            session_dir = os.path.dirname(SESSION_NAME)
            os.makedirs(session_dir, exist_ok=True)
            upload_target_path = os.path.join(session_dir, f"{filename_no_ext}_{unique_suffix}_converted.mp4")
            logger.info(f"Converting to MP4: {filepath} -> {upload_target_path}")
            cmd = [
                'ffmpeg', '-y',
                '-i', filepath,
                '-c', 'copy',
                '-movflags', '+faststart',
                upload_target_path
            ]
            
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )
            except Exception as e:
                logger.error(f"Failed to start FFmpeg remux for {filepath}: {e}")
                update_upload_status(conn, filepath, 'FAILED')
                return
                
            stderr = None
            try:
                # 增加 1800 秒超时控制，防止坏视频导致 Remux 卡死
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=1800)
            except asyncio.TimeoutError:
                logger.error(f"FFmpeg remux timed out for {filepath}")
                if process:
                    try: 
                        process.kill()
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                    except Exception: pass
                update_upload_status(conn, filepath, 'FAILED')
                return
            except asyncio.CancelledError:
                if process:
                    try: 
                        process.kill()
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                    except Exception: pass
                raise
            
            if process.returncode == 0 and os.path.exists(upload_target_path) and os.path.getsize(upload_target_path) > 0:
                is_temp_mp4 = True
                logger.info(f"Remux successful: {upload_target_path}")
            else:
                err_msg = stderr.decode(errors='ignore').strip() if stderr else 'Unknown error'
                logger.error(f"FFmpeg remux failed with code {process.returncode}: {err_msg}")
                if os.path.exists(upload_target_path):
                    try: os.remove(upload_target_path)
                    except Exception: pass
                upload_target_path = filepath
                is_temp_mp4 = False

        # [超大文件切割预处理] - 此时使用转换后的 upload_target_path，它应该是拥有正确时间戳的健康的 mp4
        file_size_bytes = os.path.getsize(upload_target_path)
        max_size_bytes = MAX_SPLIT_SIZE_MB * 1024 * 1024
        
        if file_size_bytes > max_size_bytes:
            dir_path = os.path.dirname(filepath)
            free_space = shutil.disk_usage(dir_path).free
            required_space = file_size_bytes * 1.1
            if free_space < required_space:
                logger.error(f"Insufficient disk space to safely split {filepath}. Required: {required_space/1024**3:.2f}GB, Free: {free_space/1024**3:.2f}GB. Refusing to split.")
                update_upload_status(conn, filepath, 'FAILED')
                if is_temp_mp4 and os.path.exists(upload_target_path):
                    try: os.remove(upload_target_path)
                    except Exception: pass
                return
                
            logger.info(f"File {upload_target_path} exceeds limit ({file_size_bytes} > {max_size_bytes} bytes). Initiating lossless segment split...")
            _, _, duration = await get_video_attributes(upload_target_path)
            if duration > 0 and duration <= 60:
                logger.warning(f"File {upload_target_path} exceeds limit but duration is only {duration}s. Refusing to split to prevent infinite loop.")
            elif duration > 60:
                bytes_per_sec = file_size_bytes / duration
                target_seconds = math.floor((max_size_bytes * 0.95) / bytes_per_sec)
                
                # Safeguard: clamp target_seconds
                if target_seconds < 60:
                    logger.warning(f"Calculated target_seconds {target_seconds} is too low. Clamping to 60s.")
                    target_seconds = 60
                if target_seconds > duration * 0.95:
                    target_seconds = math.floor(duration * 0.95)
                    logger.warning(f"Calculated target_seconds was too high. Clamping down to {target_seconds}s.")
                
                if target_seconds > 0:
                    # 分块文件的存放目录，必须放回原始视频所在的监控目录 dir_path，雷达才能扫描到！
                    dir_path = os.path.dirname(filepath)
                    filename = os.path.basename(filepath)
                    original_name_without_ext = os.path.splitext(filename)[0]
                    split_successful = False
                    retry_count = 0
                    
                    # Clean up any previously generated segments before trying again
                    part_pattern = re.compile(rf'^{re.escape(original_name_without_ext)}_\d{{3}}\.mp4$')
                    for candidate in Path(dir_path).iterdir():
                        if candidate.is_file() and part_pattern.match(candidate.name):
                            try: candidate.unlink()
                            except Exception as e: logger.warning(f"Could not clean old part {candidate}: {e}")
                    
                    while not split_successful and retry_count < 10:
                        retry_count += 1
                        output_pattern = os.path.join(dir_path, f"{original_name_without_ext}_%03d.mp4")
                        
                        cmd = [
                            'ffmpeg', '-y',
                            '-i', upload_target_path,
                            '-c', 'copy',
                            '-f', 'segment',
                            '-segment_time', str(target_seconds),
                            '-segment_start_number', '1',
                            '-reset_timestamps', '1',
                            '-movflags', '+faststart',
                            output_pattern
                        ]
                        logger.info(f"Trial split with target_seconds={target_seconds}s: {cmd}")
                        
                        try:
                            process = await asyncio.create_subprocess_exec(
                                *cmd,
                                stdout=asyncio.subprocess.DEVNULL,
                                stderr=asyncio.subprocess.PIPE
                            )
                        except Exception as e:
                            logger.error(f"Failed to start FFmpeg split for {filepath}: {e}")
                            update_upload_status(conn, filepath, 'FAILED')
                            return
                            
                        stderr = None
                        try:
                            _, stderr = await asyncio.wait_for(process.communicate(), timeout=1800)
                        except asyncio.TimeoutError:
                            logger.error(f"FFmpeg segment split timed out for {upload_target_path}")
                            if process:
                                try: 
                                    process.kill()
                                    await asyncio.wait_for(process.wait(), timeout=2.0)
                                except Exception: pass
                            break
                        except asyncio.CancelledError:
                            if process:
                                try: 
                                    process.kill()
                                    await asyncio.wait_for(process.wait(), timeout=2.0)
                                except Exception: pass
                            raise
                            
                        if process.returncode != 0:
                            err_msg = stderr.decode(errors='ignore').strip() if stderr else 'Unknown error'
                            logger.error(f"FFmpeg segment split failed with code {process.returncode}: {err_msg}")
                            break
                            
                        # Validate generated files
                        generated_files = []
                        # 查找所有生成的分片 (001, 002...)
                        for i in range(1, 1001): # max 1000 parts
                            part_file = os.path.join(dir_path, f"{original_name_without_ext}_{i:03d}.mp4")
                            if os.path.exists(part_file):
                                generated_files.append(part_file)
                            else:
                                break
                                
                        if not generated_files:
                            logger.error("FFmpeg produced no output files.")
                            break
                            
                        # Check sizes
                        needs_retry = False
                        max_part_size = 0
                        for part_file in generated_files:
                            size = os.path.getsize(part_file)
                            max_part_size = max(max_part_size, size)
                            if size > max_size_bytes:
                                logger.info(f"Generated part {part_file} is too big ({size} bytes). Adjusting and retrying...")
                                needs_retry = True
                                break
                                
                        if needs_retry:
                            # Delete all generated files
                            for part_file in generated_files:
                                try: os.remove(part_file)
                                except Exception: pass
                            
                            # Adjust target_seconds based on the largest part
                            ratio = (max_size_bytes * 0.95) / max_part_size
                            new_target = math.floor(target_seconds * ratio)
                            if new_target >= target_seconds:
                                new_target = target_seconds - 10 # force reduction
                            target_seconds = max(60, new_target)
                        else:
                            # Clean up small fragments < 1MB that segment muxer sometimes produces at the end
                            valid_files = []
                            for part_file in generated_files:
                                if os.path.getsize(part_file) < 1024 * 1024:
                                    try: os.remove(part_file)
                                    except Exception: pass
                                else:
                                    valid_files.append(part_file)
                                
                            split_successful = True
                            
                    if split_successful:
                        logger.info(f"Manual segment split successful for {filepath}. Removed original and yielding to radar.")
                        update_upload_status(conn, filepath, 'SKIPPED_FOR_SPLIT')
                        if os.path.exists(filepath):
                            try: os.remove(filepath)
                            except Exception: pass
                        if is_temp_mp4 and os.path.exists(upload_target_path):
                            try: os.remove(upload_target_path)
                            except Exception: pass
                        return
                    else:
                        update_upload_status(conn, filepath, 'FAILED')
                        if is_temp_mp4 and os.path.exists(upload_target_path):
                            try: os.remove(upload_target_path)
                            except Exception: pass
                        return
            else:
                logger.warning(f"Could not get duration for {upload_target_path}, cannot split. Proceeding with raw upload.")
    
        thumb_path = await generate_thumbnail(upload_target_path)
        filename = os.path.basename(filepath)
        name_without_ext = os.path.splitext(filename)[0]
        
        # 作用域初始化
        chunk_idx = None
        prefix = None
        source_name = None
        date_str = None
        time_str = None
        dir_path = os.path.dirname(filepath)
        
        # Parse filename to generate formatted caption
        
        # Match pattern: SourceName_YYYY-MM-DDTHH_MM_SS
        iso_date_match = re.match(r'^(.*)([0-9]{4}-[0-9]{2}-[0-9]{2})T(.*)$', name_without_ext)
        
        if iso_date_match:
            source_name = iso_date_match.group(1)
            date_str = iso_date_match.group(2)
            t_parts = iso_date_match.group(3).split('_')
            time_str = ':'.join(t_parts[:3])
            
            if len(t_parts) > 3 and t_parts[-1].isdigit():
                chunk_idx = t_parts[-1]
                prefix = f"{source_name}{date_str}T{'_'.join(t_parts[:-1])}_"
                
                dir_path = os.path.dirname(filepath)
                sibling_files = [f for f in os.listdir(dir_path) if f.startswith(prefix) and f.endswith(('.mp4', '.ts', '.flv', '.mkv'))]
                
                if chunk_idx == "000":
                    has_uploaded_siblings = (get_upload_message_id(conn, dir_path, prefix) is not None)
                    if len(sibling_files) > 1 or has_uploaded_siblings:
                        caption_text = f"{source_name} {date_str} {time_str} {chunk_idx}"
                    else:
                        caption_text = f"{source_name} {date_str} {time_str}"
                else:
                    caption_text = f"{source_name} {date_str} {time_str} {chunk_idx}"
            else:
                caption_text = f"{source_name} {date_str} {time_str}"
        else:
            parts = name_without_ext.split('_')
            
            if len(parts) >= 4 and parts[-1].isdigit():
                # Match pattern: SourceName_YYYY-MM-DD_HH-MM-SS_idx.mp4
                chunk_idx = parts[-1]
                time_str = parts[-2].replace('-', ':')
                date_str = parts[-3]
                source_name = '_'.join(parts[:-3])
                
                dir_path = os.path.dirname(filepath)
                prefix = f"{source_name}_{parts[-3]}_{parts[-2]}_"
                sibling_files = [f for f in os.listdir(dir_path) if f.startswith(prefix) and f.endswith(('.mp4', '.ts', '.flv', '.mkv'))]
                
                if chunk_idx == "000":
                    has_uploaded_siblings = (get_upload_message_id(conn, dir_path, prefix) is not None)
                    if len(sibling_files) > 1 or has_uploaded_siblings:
                        caption_text = f"{source_name} {date_str} {time_str} {chunk_idx}"
                    else:
                        caption_text = f"{source_name} {date_str} {time_str}"
                else:
                    caption_text = f"{source_name} {date_str} {time_str} {chunk_idx}"
                    
            elif len(parts) >= 3:
                # Match legacy pattern: SourceName_YYYY-MM-DD_HH-MM-SS.mp4
                time_str = parts[-1].replace('-', ':')
                date_str = parts[-2]
                source_name = '_'.join(parts[:-2])
                caption_text = f"{source_name} {date_str} {time_str}"
                
            else:
                # 非预期格式视频直接使用原文件名
                caption_text = name_without_ext
        
        try:
            # 获取视频长宽和时长元数据
            v_width, v_height, v_duration = await get_video_attributes(upload_target_path)
            video_attr = DocumentAttributeVideo(
                duration=v_duration,
                w=v_width,
                h=v_height,
                supports_streaming=True
            )
            
            # --- 新的原生分块并发加速上传逻辑 (FastTelethon Multi-Connection) ---
            from fast_telethon import upload_file as fast_upload
            file_size = os.path.getsize(upload_target_path)
            if file_size == 0:
                raise ValueError(f"Target file {upload_target_path} is 0 bytes. Refusing to upload.")
                
            logger.info(f"Starting FastTelethon parallel upload: size={file_size}")
            
            with open(upload_target_path, 'rb') as f:
                # 因为已经在最外层拿到了 semaphore，所以这里不需要再获取了，直接全速并发上传即可
                input_file = await fast_upload(client, f)
                
            if not is_running:
                return
                
            logger.info("All parts uploaded via FastTelethon. Assembling final message...")
            
            # 将原始的文件名覆盖到 input_file 上，确保展示为整洁的名字
            clean_filename = f"{name_without_ext}.mp4"
            input_file.name = clean_filename
            
            # 使用组装好的 input_file 发送最终消息（此时不再走网络上传大流，只是发送元数据和拼装指令）
            uploaded_msg_id = None
            while is_running:
                try:
                    final_msg = await client.send_file(
                        CHANNEL_ID,
                        file=input_file,
                        thumb=thumb_path,
                        caption=caption_text,
                        attributes=[video_attr]
                    )
                    if final_msg:
                        uploaded_msg_id = final_msg.id
                    break  # 上传成功，跳出循环
                    
                except FloodWaitError as e:
                    wait_time = e.seconds + 5
                    logger.warning(f"Telegram API Rate Limit on final send_file. Sleeping for {wait_time}s...")
                    await asyncio.sleep(wait_time)
                except Exception as inner_e:
                    logger.error(f"Error during final file transmission: {inner_e}")
                    raise inner_e
                    
            if is_running:
                update_upload_status(conn, filepath, 'COMPLETED', uploaded_msg_id)
                logger.info(f"Upload COMPLETED: {filepath}")
                
                # ====== Cascade Edit ======
                if chunk_idx is not None and chunk_idx != "000" and chunk_idx.isdigit() and source_name is not None:
                    zero_chunk_msg_id = get_upload_message_id(conn, dir_path, f"{prefix}000")
                    if zero_chunk_msg_id:
                        new_caption = f"{source_name} {date_str} {time_str} 000"
                        try:
                            await client.edit_message(CHANNEL_ID, zero_chunk_msg_id, new_caption)
                            logger.info(f"Cascade edit successful: fixed caption for chunk 000 (msg_id {zero_chunk_msg_id}) to '{new_caption}'")
                        except Exception as edit_e:
                            logger.warning(f"Cascade edit failed for chunk 000 (msg_id {zero_chunk_msg_id}): {edit_e}")
                            
                # ====== 阅后即焚接管 (Auto-Cleanup) ======
                try:
                    if os.path.exists(filepath):
                        os.remove(filepath)
                        logger.info(f"Source file auto-cleaned: {filepath}")
                        
                    # 如果当前处理的是 MP4，为了防止之前的 TS/FLV 残留，顺手补一刀
                    if filepath.lower().endswith('.mp4'):
                        filename_no_ext = os.path.splitext(os.path.basename(filepath))[0]
                        for ext in ['.ts', '.flv', '.mkv']:
                            raw_file = os.path.join(os.path.dirname(filepath), f"{filename_no_ext}{ext}")
                            if os.path.exists(raw_file):
                                os.remove(raw_file)
                                logger.info(f"Residual raw file auto-cleaned: {raw_file}")
                                
                    # ====== 级联空文件夹清理 (Empty Directory Pruning) ======
                    # 当文件被删除后，递归检查父目录。如果父目录空了，就把它也删掉，防止海量空文件夹残留。
                    # 终止条件是到达了设置的 WATCH_DIR 根目录。
                    dir_path_obj = Path(filepath).parent
                    watch_dir_abs_list = [Path(d.strip()).resolve() for d in WATCH_DIR.split(',') if d.strip()]
                    while dir_path_obj and dir_path_obj.resolve() not in watch_dir_abs_list:
                        try:
                            if not any(dir_path_obj.iterdir()):
                                dir_path_obj.rmdir()
                                logger.info(f"Empty directory auto-cleaned: {dir_path_obj}")
                                dir_path_obj = dir_path_obj.parent
                            else:
                                break  # 目录不为空，说明还有别的视频，停止清理
                        except Exception as e:
                            logger.warning(f"Could not remove directory {dir_path_obj}: {e}")
                            break
                            
                except Exception as del_e:
                    logger.error(f"Failed to auto-clean source files for {filepath}: {del_e}")
            
        except Exception as e:
            logger.error(f"Failed to upload {filepath}: {e}", exc_info=True)
            update_upload_status(conn, filepath, 'FAILED')
            fail_counts[filepath] = fail_counts.get(filepath, 0) + 1
        finally:
            # 清理临时生成的封面图
            try:
                if thumb_path and os.path.exists(thumb_path):
                    os.remove(thumb_path)
            except Exception as e:
                logger.warning(f"Failed to clean up thumbnail {thumb_path}: {e}")
            # 清理临时转封装生成的 MP4 文件释放空间
            try:
                if is_temp_mp4 and os.path.exists(upload_target_path):
                    os.remove(upload_target_path)
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {upload_target_path}: {e}")

def _sync_scan_directories(watch_dir_list):
    all_files = []
    # 使用单次遍历获取所有文件，避免多次 rglob 的极高 I/O 浪费
    for w_dir in watch_dir_list:
        for file in w_dir.rglob('*'):
            if file.is_file() and file.suffix.lower() in ('.mp4', '.ts', '.flv', '.mkv'):
                all_files.append(file)
    return all_files

def _sync_global_prune(watch_dir_list):
    for w_dir in watch_dir_list:
        try:
            if not w_dir.exists():
                continue
            watch_dir_abs = str(w_dir.resolve())
            for root_dir, dirs, files in os.walk(watch_dir_abs, topdown=False):
                if root_dir == watch_dir_abs:
                    continue
                if not os.listdir(root_dir):
                    try:
                        os.rmdir(root_dir)
                        logger.info(f"Global pruning: Removed empty directory {root_dir}")
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Global pruning error for {w_dir}: {e}")

async def scan_and_upload(client: TelegramClient, conn: sqlite3.Connection):
    """目录非阻塞雷达轮询与智能并发任务调度主循环"""
    watch_dir_list = [Path(d.strip()) for d in WATCH_DIR.split(',') if d.strip()]
    
    # 状态机：记录所有发现文件的 [大小, 修改时间, 稳定次数]
    file_stats = {}
    # 状态机：记录当前正在后台执行的上传任务
    active_upload_tasks = {}
    fail_counts = {}
    # 全局并发锁：控制同时处于上传状态的文件数，可通过 MAX_CONCURRENT_UPLOADS 配置（高带宽服务器可适当调大）
    global_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPLOADS)
    
    while is_running:
        try:
            # 递归获取所有支持的视频文件，放入线程池避免阻塞 Asyncio
            all_files = await asyncio.to_thread(_sync_scan_directories, watch_dir_list)
            current_files = set()
            
            # --- Phase 1: Radar Scan ---
            for filepath in all_files:
                path_str = str(filepath)
                # Ignore hidden or temporary files
                if filepath.name.startswith('.') or filepath.name.endswith('.tmp'):
                    continue
                    
                status = get_upload_status(conn, path_str)
                # Skip completed, failed, or explicitly skipped files
                if status in ('COMPLETED', 'SKIPPED_FOR_NATIVE_MP4', 'SKIPPED_DUPLICATE_MP4', 'SKIPPED_EMPTY_FILE', 'SKIPPED_FOR_SPLIT'):
                    continue
                if status == 'FAILED':
                    if fail_counts.get(path_str, 0) >= 3:
                        continue  # 超过 3 次实際重试，永久放弃
                    # 重置为 PENDING 以便重新进入上传流程，计数在真正重试失败时才增加
                    update_upload_status(conn, path_str, 'PENDING')
                # Skip if already in active background tasks
                if path_str in active_upload_tasks:
                    continue
                    
                current_files.add(path_str)
                try:
                    stat = os.stat(path_str)
                    c_size, c_mtime = stat.st_size, stat.st_mtime
                    if path_str not in file_stats:
                        file_stats[path_str] = {'size': c_size, 'mtime': c_mtime, 'stable_count': 0}
                    else:
                        if c_size == file_stats[path_str]['size'] and c_mtime == file_stats[path_str]['mtime']:
                            file_stats[path_str]['stable_count'] += 1
                        else:
                            file_stats[path_str] = {'size': c_size, 'mtime': c_mtime, 'stable_count': 0}
                except OSError as stat_e:
                    logger.debug(f"Could not stat {path_str}: {stat_e}")
                    if path_str in file_stats:
                        del file_stats[path_str]
            
            # Cleanup ghost files that disappeared from disk
            for path_str in list(file_stats.keys()):
                if path_str not in current_files:
                    del file_stats[path_str]
            # Cleanup stale fail_counts entries for files no longer on disk
            for path_str in list(fail_counts.keys()):
                if path_str not in current_files and path_str not in active_upload_tasks:
                    del fail_counts[path_str]
                    
            # --- Phase 2: Sequential Scheduler (Group-based) ---
            groups = {}
            for path_str in current_files:
                filename = os.path.basename(path_str)
                name_without_ext = os.path.splitext(filename)[0]
                parts = name_without_ext.split('_')
                
                # Extract prefix (representing a single stream session) and chunk index
                # Ensure the last part is a chunk index (at least 3 digits padded) to avoid collision with timestamp seconds
                if len(parts) >= 4 and parts[-1].isdigit() and len(parts[-1]) >= 3:
                    prefix = '_'.join(parts[:-1])
                    idx = int(parts[-1])
                else:
                    prefix = name_without_ext
                    idx = 0
                    
                group_key = (str(Path(path_str).parent.resolve()), prefix)
                if group_key not in groups:
                    groups[group_key] = []
                groups[group_key].append({'path': path_str, 'idx': idx})
                
            # Sort groups by parent path and prefix to ensure chronological uploads
            for group_key, files in sorted(groups.items(), key=lambda x: x[0][1]):
                prefix = group_key[1]
                if not is_running:
                    break
                    
                # --- Group-Level Stability Logic ---
                has_ts = any(f['path'].lower().endswith(('.ts', '.flv', '.mkv')) for f in files)
                has_mp4 = any(f['path'].lower().endswith('.mp4') for f in files)
                
                if has_ts and has_mp4:
                    # Mixed phase: Recorder might be slowly converting TS to MP4.
                    # Grant an extended 15-minute grace period to prevent stealing the file.
                    group_required_stable = 90
                elif has_ts and not has_mp4:
                    # Raw phase: Currently recording, or conversion hasn't started.
                    group_required_stable = 90
                else:
                    # Finished phase: Only MP4s exist.
                    # Grant a short 30-second grace period for disk sync.
                    group_required_stable = 3
                    
                # Check if ALL files in this group have reached their stability threshold
                is_group_completely_stable = all(file_stats[f['path']]['stable_count'] >= group_required_stable for f in files)
                if not is_group_completely_stable:
                    continue
                    
                # Check if this group already has a file currently uploading
                is_group_busy = False
                for active_path in active_upload_tasks.keys():
                    filename = os.path.basename(active_path)
                    name_without_ext = os.path.splitext(filename)[0]
                    a_parts = name_without_ext.split('_')
                    a_prefix = '_'.join(a_parts[:-1]) if (len(a_parts) >= 4 and a_parts[-1].isdigit() and len(a_parts[-1]) >= 3) else name_without_ext
                    a_group_key = (str(Path(active_path).parent.resolve()), a_prefix)
                    if a_group_key == group_key:
                        is_group_busy = True
                        break
                        
                if is_group_busy:
                    # 前序切片正在上传，后序切片静默等待
                    continue
                    
                # 找出本组内当前序号最小的文件
                min_file = min(files, key=lambda x: x['idx'])
                
                logger.info(f"Dispatching task: {min_file['path']} (Group: {prefix}, Index: {min_file['idx']})")
                # 启动后台独立上传协程
                task = asyncio.create_task(upload_file(client, min_file['path'], conn, global_semaphore, fail_counts))
                active_upload_tasks[min_file['path']] = task
                
                # 注册回调：上传结束（成功或失败）后，从活动字典中移除，释放组通道
                def _make_callback(p):
                    def _callback(t):
                        if p in active_upload_tasks:
                            del active_upload_tasks[p]
                    return _callback
                task.add_done_callback(_make_callback(min_file['path']))
                
                
        except asyncio.CancelledError:
            logger.info("Radar scan cancelled. Terminating active upload tasks...")
            if active_upload_tasks:
                for task in active_upload_tasks.values():
                    task.cancel()
                await asyncio.gather(*active_upload_tasks.values(), return_exceptions=True)
            raise
        except Exception as e:
            logger.error(f"Error during directory scan: {e}", exc_info=True)
            
        if is_running:
            # --- 全局空文件夹修剪 (Global Empty Directory Pruning) ---
            # 无论什么原因产生的空文件夹，每 10 秒都会被自底向上彻底抹除，放入独立线程池防阻塞
            await asyncio.to_thread(_sync_global_prune, watch_dir_list)
                
            await asyncio.sleep(10)  # 雷达每 10 秒扫一次

# ---------------- 主干程序 ----------------
async def main():
    global MAX_SPLIT_SIZE_MB
    logger.info("Initializing SQLite database...")
    conn = init_db()
    
    # 垃圾清理：清理上次异常崩溃可能遗留的临时转换文件和封面图
    logger.info("Cleaning up any orphaned temporary files...")
    session_dir = os.path.dirname(SESSION_NAME)
    for temp_dir in [session_dir, '/tmp']:
        if os.path.exists(temp_dir):
            for file in os.listdir(temp_dir):
                if file.endswith('_converted.mp4') or file.endswith('.thumb.jpg'):
                    try:
                        os.remove(os.path.join(temp_dir, file))
                    except Exception as e:
                        logger.warning(f"Failed to remove orphaned temp file {file}: {e}")
    
    logger.info("Starting Telegram Client...")
    
    # 构造代理配置
    proxy = None
    if PROXY_TYPE and PROXY_HOST and PROXY_PORT:
        proxy = {
            'proxy_type': PROXY_TYPE.lower(), # 'http' or 'socks5'
            'addr': PROXY_HOST,
            'port': int(PROXY_PORT)
        }
        logger.info(f"Using proxy: {PROXY_TYPE}://{PROXY_HOST}:{PROXY_PORT}")

    # 结合 Premium，开启 auto_reconnect 与无限制重试，并自定义设备名称
    client = TelegramClient(
        SESSION_NAME, 
        API_ID, 
        API_HASH, 
        connection_retries=None, 
        auto_reconnect=True,
        device_model=DEVICE_MODEL,
        system_version=SYSTEM_VERSION,
        app_version=APP_VERSION,
        proxy=proxy
    )
    
    await client.start()
    logger.info("Telegram Client started successfully.")
    
    try:
        me = await client.get_me()
        if me:
            if os.getenv("MAX_SPLIT_SIZE_MB") is not None:
                logger.info(f"Using explicitly configured MAX_SPLIT_SIZE_MB: {MAX_SPLIT_SIZE_MB}MB")
            elif getattr(me, 'premium', False):
                MAX_SPLIT_SIZE_MB = 4000
                logger.info(f"Logged in as {me.first_name} (Premium: Yes). Max split size auto-configured to {MAX_SPLIT_SIZE_MB}MB.")
            else:
                MAX_SPLIT_SIZE_MB = 2000
                logger.info(f"Logged in as {me.first_name} (Premium: No). Max split size auto-configured to {MAX_SPLIT_SIZE_MB}MB.")
    except Exception as e:
        logger.warning(f"Could not retrieve user profile to check Premium status: {e}")
    
    # 将扫描任务投入事件循环
    upload_task = asyncio.create_task(scan_and_upload(client, conn))
    
    # 阻塞主线程，直到收到终止信号
    while is_running:
        await asyncio.sleep(1)
        
    logger.info("Termination signal received. Waiting for active upload slice to flush safely...")
    
    # 取消当前任务（如果在睡眠阶段会抛出 CancelledError，如果在上传中会在当前块传完后中断）
    upload_task.cancel()
    try:
        await upload_task
    except asyncio.CancelledError:
        pass
        
    logger.info("Disconnecting Telegram Client gracefully...")
    await client.disconnect()
    
    logger.info("Closing SQLite database connection...")
    conn.close()
    logger.info("Graceful shutdown completed successfully.")

if __name__ == '__main__':
    # 确保环境变量都配齐了
    if not all([API_ID, API_HASH, CHANNEL_ID]):
        logger.error("Missing critical environment variables: API_ID, API_HASH or CHANNEL_ID.")
        exit(1)
        
    asyncio.run(main())
