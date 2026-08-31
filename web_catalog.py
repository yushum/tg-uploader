import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


ISO_NAME_RE = re.compile(
    r"^(?P<name>.*?)(?P<date>\d{4}-\d{2}-\d{2})T"
    r"(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2})"
    r"(?P<suffix>(?:_\d+)*)(?:_retry)?$"
)
STANDARD_NAME_RE = re.compile(
    r"^(?P<name>.+?)_(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<hour>\d{2})-(?P<minute>\d{2})-(?P<second>\d{2})"
    r"(?P<suffix>(?:_\d+)*)(?:_retry)?$"
)
DATE_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class RecordingPart:
    filepath: str
    message_id: int
    streamer: str
    platform: str
    date: str
    time: str
    part_order: tuple[int, ...]

    @property
    def session_key(self) -> tuple[str, str, str, str]:
        return self.streamer, self.platform, self.date, self.time

    @property
    def part_label(self) -> str:
        return ".".join(str(value) for value in self.part_order) if self.part_order else "1"


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _platform_from_path(filepath: str) -> str:
    folded = filepath.casefold()
    if "bili" in folded or "哔哩" in filepath:
        return "B站"
    if "douyin" in folded or "抖音" in filepath:
        return "抖音"
    return "其他"


def _fallback_streamer(path: Path) -> str:
    parent = path.parent
    if DATE_RE.fullmatch(parent.name) and parent.parent.name:
        return parent.parent.name
    return parent.name or path.stem


def parse_recording(filepath: str, message_id: int, uploaded_at: str = "") -> RecordingPart:
    path = Path(filepath)
    stem = path.stem
    match = ISO_NAME_RE.match(stem) or STANDARD_NAME_RE.match(stem)

    if match and _valid_date(match.group("date")):
        streamer = match.group("name").rstrip("_ -") or _fallback_streamer(path)
        date = match.group("date")
        time = f'{match.group("hour")}:{match.group("minute")}:{match.group("second")}'
        suffix = match.group("suffix")
        part_order = tuple(int(value) for value in suffix.split("_") if value)
    else:
        date_match = DATE_RE.search(filepath)
        candidate_date = date_match.group("date") if date_match else ""
        date = candidate_date if _valid_date(candidate_date) else (uploaded_at[:10] or "未知日期")
        streamer = _fallback_streamer(path)
        time = "00:00:00"
        part_order = ()

    return RecordingPart(
        filepath=filepath,
        message_id=int(message_id),
        streamer=streamer,
        platform=_platform_from_path(filepath),
        date=date,
        time=time,
        part_order=part_order,
    )


def load_recordings(db_path: str) -> list[RecordingPart]:
    absolute_path = os.path.abspath(db_path)
    connection = sqlite3.connect(f"file:{absolute_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT filepath, message_id, uploaded_at
            FROM uploads
            WHERE status = 'COMPLETED' AND message_id IS NOT NULL
            ORDER BY message_id
            """
        ).fetchall()
    finally:
        connection.close()

    # A Telegram message identifies one media object; keep the newest local row if duplicates exist.
    by_message_id: dict[int, RecordingPart] = {}
    for filepath, message_id, uploaded_at in rows:
        by_message_id[int(message_id)] = parse_recording(filepath, message_id, uploaded_at or "")
    return list(by_message_id.values())


def group_sessions(parts: list[RecordingPart]):
    sessions: dict[tuple[str, str, str, str], list[RecordingPart]] = defaultdict(list)
    for part in parts:
        sessions[part.session_key].append(part)
    for session_parts in sessions.values():
        session_parts.sort(key=lambda part: (part.part_order, part.message_id))
    return sessions


def parse_http_range(value: str | None, file_size: int) -> tuple[int, int, bool]:
    if file_size <= 0:
        raise ValueError("empty file")
    if not value:
        return 0, file_size - 1, False
    if not value.startswith("bytes=") or "," in value:
        raise ValueError("unsupported range")

    range_value = value[6:].strip()
    if "-" not in range_value:
        raise ValueError("invalid range")
    start_text, end_text = range_value.split("-", 1)

    if not start_text:
        suffix_length = int(end_text)
        if suffix_length <= 0:
            raise ValueError("invalid suffix range")
        start = max(0, file_size - suffix_length)
        end = file_size - 1
    else:
        start = int(start_text)
        end = int(end_text) if end_text else file_size - 1
        if start < 0 or start >= file_size or end < start:
            raise ValueError("range outside file")
        end = min(end, file_size - 1)

    return start, end, True
