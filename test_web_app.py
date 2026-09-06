import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import web_app


class FakeDownloadIterator:
    def __init__(self, data):
        self.data = data
        self.sent = False
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.sent:
            raise StopAsyncIteration
        self.sent = True
        return self.data

    async def close(self):
        self.closed = True


class FakeTelegram:
    def __init__(self, data):
        self.data = data
        self.downloads = 0

    def iter_download(self, _document, *, offset, file_size, limit=1, **_kwargs):
        self.downloads += 1
        end = min(file_size, offset + web_app.MEDIA_CACHE_BLOCK_SIZE * limit)
        return FakeDownloadIterator(self.data[offset:end])


class MediaCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamer_history_route_returns_spa(self):
        response = await web_app.streamer_page("susu/2026-08-01")
        self.assertEqual(Path(response.path), web_app.FRONTEND_DIR / "index.html")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")

    async def test_top_level_history_routes_return_spa(self):
        for response in (await web_app.index(), await web_app.design_page()):
            self.assertEqual(Path(response.path), web_app.FRONTEND_DIR / "index.html")
            self.assertEqual(response.headers["Cache-Control"], "no-cache")

    async def test_live_studio_assets_are_served(self):
        script = await web_app.live_studio_script()
        style = await web_app.live_studio_style()
        self.assertEqual(Path(script.path), web_app.FRONTEND_DIR / "live.js")
        self.assertEqual(Path(style.path), web_app.FRONTEND_DIR / "live.css")

    async def test_concurrent_read_downloads_block_once_then_hits_cache(self):
        data = bytes((index % 251 for index in range(web_app.MEDIA_CACHE_BLOCK_SIZE + 37)))
        details = {"size": len(data), "mime_type": "video/mp4"}
        message = SimpleNamespace(document=object())
        fake_telegram = FakeTelegram(data)
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(web_app, "MEDIA_CACHE_DIR", Path(temporary)), patch.object(
                web_app, "telegram", fake_telegram
            ):
                results = await asyncio.gather(
                    *(web_app._media_block(message, details, 99, 0) for _ in range(4))
                )
                await asyncio.gather(*list(web_app.media_prefetch_tasks))
                cached, hit = await web_app._media_block(message, details, 99, 0)
                prefetched, prefetched_hit = await web_app._media_block(message, details, 99, 1)

        self.assertEqual(fake_telegram.downloads, 1)
        self.assertTrue(all(result == data[: web_app.MEDIA_CACHE_BLOCK_SIZE] for result, _hit in results))
        self.assertFalse(results[0][1])
        self.assertTrue(all(hit for _result, hit in results[1:]))
        self.assertTrue(hit)
        self.assertEqual(cached, data[: web_app.MEDIA_CACHE_BLOCK_SIZE])
        self.assertTrue(prefetched_hit)
        self.assertEqual(prefetched, data[web_app.MEDIA_CACHE_BLOCK_SIZE :])
        self.assertFalse(web_app.media_cache_inflight)

    async def test_failed_prefetch_releases_all_inflight_entries(self):
        details = {"size": web_app.MEDIA_CACHE_BLOCK_SIZE * 2, "mime_type": "video/mp4"}
        message = SimpleNamespace(document=object())
        fake_telegram = FakeTelegram(b"short")
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(web_app, "MEDIA_CACHE_DIR", Path(temporary)), patch.object(
                web_app, "telegram", fake_telegram
            ):
                with self.assertRaises(RuntimeError):
                    await web_app._media_block(message, details, 100, 0)
                await asyncio.gather(*list(web_app.media_prefetch_tasks), return_exceptions=True)

        self.assertFalse(web_app.media_cache_inflight)

    def test_media_headers_are_cacheable_and_range_aware(self):
        headers = web_app._media_headers(
            42,
            {"size": 1000, "mime_type": "video/mp4"},
            100,
            199,
            True,
        )
        self.assertEqual(headers["Content-Length"], "100")
        self.assertEqual(headers["Content-Range"], "bytes 100-199/1000")
        self.assertEqual(headers["ETag"], '"tg-42-1000"')
        self.assertNotIn("no-store", headers["Cache-Control"])


def _live_part(message_id, streamer="susu", date="2026-08-01", time="20:00:00"):
    return SimpleNamespace(
        message_id=message_id, streamer=streamer, date=date, time=time,
        part_label=f"P{message_id}",
    )


def _live_message(message_id, valid=True):
    if not valid:
        return None
    document = SimpleNamespace(attributes=[], size=8, mime_type="video/mp4")
    return SimpleNamespace(id=message_id, document=document)


class FakeLiveTelegram:
    def __init__(self, valid_ids):
        self.valid_ids = set(valid_ids)
        self.calls = 0

    async def get_messages(self, _channel, *, ids):
        self.calls += 1
        wanted = ids if isinstance(ids, list) else [ids]
        return [_live_message(mid, mid in self.valid_ids) for mid in wanted]


class LiveCandidatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_small_result_set_is_fully_verified(self):
        fake = FakeLiveTelegram(valid_ids={1, 3})
        parts = [_live_part(1), _live_part(2), _live_part(3)]
        with patch.object(web_app, "_catalog", return_value=parts), patch.object(
            web_app, "telegram", fake
        ):
            items = await web_app.live_candidates(streamer=[], exclude=[])
        by_id = {item["message_id"]: item for item in items}
        self.assertTrue(by_id[1]["available"] and by_id[1]["verified"])
        self.assertTrue(by_id[3]["available"] and by_id[3]["verified"])
        self.assertFalse(by_id[2]["available"])
        self.assertTrue(by_id[2]["verified"])
        self.assertEqual(fake.calls, 1)

    async def test_large_result_set_skips_telegram_verification(self):
        fake = FakeLiveTelegram(valid_ids=set())
        parts = [_live_part(mid) for mid in range(1, web_app.LIVE_VERIFY_THRESHOLD + 6)]
        with patch.object(web_app, "_catalog", return_value=parts), patch.object(
            web_app, "telegram", fake
        ):
            items = await web_app.live_candidates(streamer=[], exclude=[])
        self.assertEqual(len(items), web_app.LIVE_VERIFY_THRESHOLD + 5)
        self.assertEqual(fake.calls, 0)
        self.assertTrue(all(item["available"] and item["verified"] is False for item in items))


class FakeStreamStdin:
    def write(self, _data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    def is_closing(self):
        return False


class FakeStreamProc:
    def __init__(self):
        self.stdin = FakeStreamStdin()

    async def wait(self):
        return 0

    def kill(self):
        pass


class FakeStreamTelegram:
    """只记录被推流的 message_id；达到 stop_after 后置位停止事件。"""
    def __init__(self, stop_after=None):
        self.streamed = []
        self.stop_after = stop_after

    async def get_entity(self, _channel):
        return SimpleNamespace()

    async def get_messages(self, _channel, *, ids):
        mid = ids if isinstance(ids, int) else ids[0]
        self.streamed.append(mid)
        if self.stop_after is not None and len(self.streamed) >= self.stop_after:
            web_app.live_stop_event.set()
        return SimpleNamespace(id=mid, document=SimpleNamespace())

    def iter_download(self, _document, **_kwargs):
        async def _gen():
            yield b"chunk"

        return _gen()


class LivePlayModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._saved_state = dict(web_app.live_state)
        self._saved_task = web_app.live_task
        self._saved_telegram = web_app.telegram
        web_app.live_task = None
        web_app.live_state.update(status="IDLE", error="")

    async def asyncTearDown(self):
        task = web_app.live_task
        web_app.live_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        web_app.live_state.clear()
        web_app.live_state.update(self._saved_state)
        web_app.live_task = self._saved_task
        web_app.telegram = self._saved_telegram
        web_app.live_stop_event.clear()

    async def test_start_rejects_bad_mode(self):
        web_app.telegram = object()
        with self.assertRaises(web_app.HTTPException) as ctx:
            await web_app.live_start(web_app.LiveStartRequest(
                channel="@c", message_ids=[1], mode="bogus"))
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_start_normalizes_and_returns_mode(self):
        web_app.telegram = object()
        body = web_app.LiveStartRequest(channel="@c", message_ids=[1, 2], mode="LOOP")
        with patch.object(web_app, "_catalog", return_value=[]), patch.object(
            web_app, "_live_worker", new=AsyncMock()
        ) as worker:
            result = await web_app.live_start(body)
            await asyncio.wait_for(web_app.live_task, timeout=5)
        self.assertEqual(result["mode"], "loop")
        self.assertEqual(web_app.live_state["mode"], "loop")
        worker.assert_awaited_once_with("@c", [1, 2], "loop")

    async def test_worker_once_plays_through_and_stops(self):
        fake = FakeStreamTelegram()
        with patch.object(web_app, "telegram", fake), patch.object(
            web_app, "_catalog", return_value=[]
        ), patch.object(web_app, "_ensure_group_call_live", new=AsyncMock()), patch.object(
            web_app, "_get_rtmp_url", new=AsyncMock(return_value="rtmp://x/live")
        ), patch.object(asyncio, "create_subprocess_exec",
                         new=AsyncMock(side_effect=lambda *a, **k: FakeStreamProc())):
            await web_app._live_worker("@c", [7, 8], "once")
        self.assertEqual(fake.streamed, [7, 8])
        self.assertEqual(web_app.live_state["status"], "IDLE")
        self.assertEqual(web_app.live_state["round"], 0)

    async def test_worker_loop_repeats_until_stopped(self):
        fake = FakeStreamTelegram(stop_after=5)
        with patch.object(web_app, "telegram", fake), patch.object(
            web_app, "_catalog", return_value=[]
        ), patch.object(web_app, "_ensure_group_call_live", new=AsyncMock()), patch.object(
            web_app, "_get_rtmp_url", new=AsyncMock(return_value="rtmp://x/live")
        ), patch.object(asyncio, "create_subprocess_exec",
                         new=AsyncMock(side_effect=lambda *a, **k: FakeStreamProc())):
            await web_app._live_worker("@c", [7, 8], "loop")
        self.assertEqual(fake.streamed, [7, 8, 7, 8, 7])
        self.assertEqual(web_app.live_state["status"], "IDLE")

    async def test_worker_shuffle_reorders_each_round(self):
        fake = FakeStreamTelegram(stop_after=2)
        with patch.object(web_app, "telegram", fake), patch.object(
            web_app, "_catalog", return_value=[]
        ), patch.object(web_app, "_ensure_group_call_live", new=AsyncMock()), patch.object(
            web_app, "_get_rtmp_url", new=AsyncMock(return_value="rtmp://x/live")
        ), patch.object(asyncio, "create_subprocess_exec",
                         new=AsyncMock(side_effect=lambda *a, **k: FakeStreamProc())), patch.object(
            web_app.random, "shuffle", side_effect=lambda seq: seq.reverse()) as mock_shuffle:
            await web_app._live_worker("@c", [7, 8], "shuffle")
        self.assertEqual(fake.streamed, [8, 7])
        self.assertEqual(mock_shuffle.call_count, 1)


if __name__ == "__main__":
    unittest.main()
