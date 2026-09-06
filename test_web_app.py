import asyncio
import struct
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


class FakeProgressStdout:
    def __init__(self, lines=()):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""


class FakeStreamProc:
    def __init__(self, stdout_lines=(), returncode=0, hang_until_killed=False):
        self.stdin = FakeStreamStdin()
        self.stdout = FakeProgressStdout(stdout_lines)
        self.stderr = None
        # 真实进程运行中 returncode 为 None，hang 模式模拟这一点，看门狗才会动手。
        self.returncode = None if hang_until_killed else returncode
        self.hang_until_killed = hang_until_killed
        self.killed = False

    async def wait(self):
        if self.hang_until_killed:
            while not self.killed:
                await asyncio.sleep(0.01)
            self.returncode = -9
            return self.returncode
        await asyncio.sleep(0.05)
        return self.returncode

    def kill(self):
        self.killed = True


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

    async def test_worker_skips_on_progress_stall_and_kills_ffmpeg(self):
        procs = []

        def _spawn(*_a, **_k):
            proc = FakeStreamProc(hang_until_killed=True)
            procs.append(proc)
            return proc

        fake = FakeStreamTelegram()
        with patch.object(web_app, "telegram", fake), patch.object(
            web_app, "_catalog", return_value=[]
        ), patch.object(web_app, "_ensure_group_call_live", new=AsyncMock()), patch.object(
            web_app, "_get_rtmp_url", new=AsyncMock(return_value="rtmp://x/live")
        ), patch.object(asyncio, "create_subprocess_exec",
                         new=AsyncMock(side_effect=_spawn)), patch.object(
            web_app, "LIVE_STREAM_PROGRESS_TIMEOUT", 0.05
        ):
            await web_app._live_worker("@c", [7], "once")
        self.assertEqual(fake.streamed, [7])
        self.assertTrue(procs and procs[0].killed)
        self.assertEqual(web_app.live_state["status"], "IDLE")
        self.assertIn("停滞", web_app.live_state["error"])

    async def test_worker_skips_video_on_fetch_timeout(self):
        fake = FakeStreamTelegram()

        async def _timeout(_channel, *, ids):
            raise asyncio.TimeoutError()

        fake.get_messages = _timeout
        with patch.object(web_app, "telegram", fake), patch.object(
            web_app, "_catalog", return_value=[]
        ), patch.object(web_app, "_ensure_group_call_live", new=AsyncMock()), patch.object(
            web_app, "_get_rtmp_url", new=AsyncMock(return_value="rtmp://x/live")
        ), patch.object(asyncio, "create_subprocess_exec",
                         new=AsyncMock(side_effect=lambda *a, **k: FakeStreamProc())):
            await web_app._live_worker("@c", [7], "once")
        self.assertEqual(fake.streamed, [])
        self.assertEqual(web_app.live_state["status"], "IDLE")
        self.assertIn("超时", web_app.live_state["error"])

    async def test_worker_counts_bytes_sent(self):
        fake = FakeStreamTelegram()
        with patch.object(web_app, "telegram", fake), patch.object(
            web_app, "_catalog", return_value=[]
        ), patch.object(web_app, "_ensure_group_call_live", new=AsyncMock()), patch.object(
            web_app, "_get_rtmp_url", new=AsyncMock(return_value="rtmp://x/live")
        ), patch.object(asyncio, "create_subprocess_exec",
                         new=AsyncMock(side_effect=lambda *a, **k: FakeStreamProc(
                             stdout_lines=[b"total_size=5\n"]))):
            await web_app._live_worker("@c", [7], "once")
        self.assertEqual(web_app.live_state["bytes_sent"], 5)

    async def test_worker_transcodes_portrait_with_pillarbox(self):
        spawned = []

        def _capture(*a, **_k):
            spawned.append((a, web_app.live_state.get("picture")))
            return FakeStreamProc()

        portrait = {"codec": "h264", "width": 1088, "height": 1920,
                    "sar": 1.0, "rotation": 0}
        fake = FakeStreamTelegram()
        with patch.object(web_app, "telegram", fake), patch.object(
            web_app, "_catalog", return_value=[]
        ), patch.object(web_app, "_ensure_group_call_live", new=AsyncMock()), patch.object(
            web_app, "_get_rtmp_url", new=AsyncMock(return_value="rtmp://x/live")
        ), patch.object(asyncio, "create_subprocess_exec",
                         new=AsyncMock(side_effect=_capture)), patch.object(
            web_app, "_probe_live_video", new=AsyncMock(return_value=portrait)
        ):
            await web_app._live_worker("@c", [7], "once")
        self.assertEqual(len(spawned), 1)
        args, picture = spawned[0]
        self.assertIn("-filter_complex", args)
        self.assertIn("libx264", args)
        self.assertTrue(any("overlay=" in x and "gblur=" in x for x in args))
        self.assertNotIn("copy", args[args.index("-c:v") + 1:args.index("-c:v") + 2])
        self.assertEqual(picture, "transcode")

    async def test_worker_copies_clean_landscape(self):
        spawned = []

        def _capture(*a, **_k):
            spawned.append((a, web_app.live_state.get("picture")))
            return FakeStreamProc()

        landscape = {"codec": "h264", "width": 1920, "height": 1080,
                     "sar": 1.0, "rotation": 0}
        fake = FakeStreamTelegram()
        with patch.object(web_app, "telegram", fake), patch.object(
            web_app, "_catalog", return_value=[]
        ), patch.object(web_app, "_ensure_group_call_live", new=AsyncMock()), patch.object(
            web_app, "_get_rtmp_url", new=AsyncMock(return_value="rtmp://x/live")
        ), patch.object(asyncio, "create_subprocess_exec",
                         new=AsyncMock(side_effect=_capture)), patch.object(
            web_app, "_probe_live_video", new=AsyncMock(return_value=landscape)
        ):
            await web_app._live_worker("@c", [7], "once")
        self.assertEqual(len(spawned), 1)
        args, picture = spawned[0]
        self.assertNotIn("-vf", args)
        self.assertNotIn("-filter_complex", args)
        self.assertIn("-c:v", args)
        self.assertEqual(args[args.index("-c:v") + 1], "copy")
        self.assertEqual(picture, "copy")

    async def test_spawn_after_stop_killed_instantly(self):
        procs = []

        def _spawn_and_stop(*_a, **_k):
            # 模拟停止恰好落在产卵瞬间：事件已置位，进程刚出生。
            web_app.live_stop_event.set()
            proc = FakeStreamProc()
            procs.append(proc)
            return proc

        fake = FakeStreamTelegram()
        with patch.object(web_app, "telegram", fake), patch.object(
            web_app, "_catalog", return_value=[]
        ), patch.object(web_app, "_ensure_group_call_live", new=AsyncMock()), patch.object(
            web_app, "_get_rtmp_url", new=AsyncMock(return_value="rtmp://x/live")
        ), patch.object(asyncio, "create_subprocess_exec",
                         new=AsyncMock(side_effect=_spawn_and_stop)):
            await web_app._live_worker("@c", [7], "once")
        self.assertEqual(fake.streamed, [7])
        self.assertTrue(procs and procs[0].killed)
        self.assertIsNone(web_app.live_process)
        self.assertEqual(web_app.live_state["status"], "IDLE")

    async def test_stop_kills_sweeps_discards_and_idles(self):
        from unittest.mock import Mock
        proc = SimpleNamespace(pid=4321, kill=Mock())
        web_app.live_process = proc
        task = asyncio.create_task(asyncio.sleep(60))
        web_app.live_task = task
        web_app.live_state.update(status="STREAMING", channel="@c")
        try:
            with patch.object(web_app, "_kill_stray_rtmp_ffmpeg",
                               return_value=1) as sweep, patch.object(
                web_app, "_discard_group_call", new=AsyncMock(return_value=True)
            ) as discard:
                result = await web_app.live_stop()
            self.assertEqual(result, {"ok": True})
            proc.kill.assert_called_once_with()
            self.assertTrue(task.cancelled() or task.done())
            self.assertIsNone(web_app.live_task)
            sweep.assert_called_once_with()
            discard.assert_awaited_once_with("@c")
            self.assertEqual(web_app.live_state["status"], "IDLE")
            self.assertEqual(web_app.live_state["mode"], "once")
        finally:
            web_app.live_process = None

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


class LivePictureTests(unittest.TestCase):
    def test_clean_landscape_copies(self):
        vf, note = web_app._live_picture_plan(
            {"codec": "h264", "width": 1920, "height": 1080, "sar": 1.0, "rotation": 0})
        self.assertIsNone(vf)
        self.assertEqual(note, "原画直推")

    def test_portrait_pillarboxes_without_stretch(self):
        vf, note = web_app._live_picture_plan(
            {"codec": "h264", "width": 1088, "height": 1920, "sar": 1.0, "rotation": 0})
        self.assertIn("overlay=(W-w)/2:(H-h)/2", vf)
        self.assertIn("gblur=", vf)
        self.assertNotIn("color=black", vf)
        self.assertIn("setsar=1", vf)
        self.assertIn("非16:9", note)

    def test_rotated_landscape_counts_as_portrait(self):
        vf, note = web_app._live_picture_plan(
            {"codec": "h264", "width": 1920, "height": 1080, "sar": 1.0, "rotation": 90})
        self.assertIn("overlay=", vf)
        self.assertIn("旋转", note)

    def test_non_h264_and_sar_transcode(self):
        vf, _ = web_app._live_picture_plan(
            {"codec": "hevc", "width": 1920, "height": 1080, "sar": 1.0, "rotation": 0})
        self.assertIsNotNone(vf)
        vf, _ = web_app._live_picture_plan(
            {"codec": "h264", "width": 720, "height": 1280, "sar": 4 / 3, "rotation": 0})
        self.assertIn("scale=iw*1.3333:ih", vf)

    def test_unknown_probe_fails_open_to_copy(self):
        vf, _ = web_app._live_picture_plan(None)
        self.assertIsNone(vf)
        self.assertIsNone(web_app._parse_probe_streams({}))
        self.assertIsNone(web_app._parse_probe_streams({"streams": [{"codec_type": "audio"}]}))

    def test_parse_helpers(self):
        self.assertAlmostEqual(web_app._parse_ratio("16:9"), 16 / 9)
        self.assertEqual(web_app._parse_ratio("0:1"), 1.0)
        self.assertEqual(web_app._parse_ratio("N/A"), 1.0)
        self.assertEqual(web_app._parse_ratio(None), 1.0)
        self.assertEqual(web_app._stream_rotation({"tags": {"rotate": "90"}}), 90)
        self.assertEqual(web_app._stream_rotation(
            {"side_data_list": [{"rotation": -90.0}]}), 270)
        self.assertEqual(web_app._stream_rotation({}), 0)
        info = web_app._parse_probe_streams({"streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1088,
             "height": 1920, "sample_aspect_ratio": "1:1"},
            {"codec_type": "audio", "codec_name": "aac"},
        ]})
        self.assertEqual(info, {"codec": "h264", "width": 1088, "height": 1920,
                                "sar": 1.0, "rotation": 0})


class StraySweepTests(unittest.TestCase):
    def _fake_proc(self, root, pid, cmdline):
        d = Path(root) / pid
        d.mkdir(parents=True, exist_ok=True)
        if cmdline is not None:
            (d / "cmdline").write_bytes(cmdline)

    def test_sweep_kills_only_rtmp_ffmpeg(self):
        import tempfile
        killed = []
        with tempfile.TemporaryDirectory() as tmp:
            self._fake_proc(tmp, "11", b"/usr/bin/ffmpeg\0-re\0rtmps://x/y\0")
            self._fake_proc(tmp, "22", b"ffmpeg\0-ss\05\0-i\0f.mp4\0")
            self._fake_proc(tmp, "33", b"python\0uploader.py\0")
            self._fake_proc(tmp, "44", None)
            with patch("os.kill", side_effect=lambda pid, sig: killed.append(pid)):
                n = web_app._kill_stray_rtmp_ffmpeg(proc_dir=tmp)
        import signal as _signal
        self.assertEqual(n, 1)
        self.assertEqual(killed, [11])

    def test_sweep_missing_proc_dir_returns_zero(self):
        self.assertEqual(web_app._kill_stray_rtmp_ffmpeg(proc_dir="/nonexistent-xyz"), 0)


class DiscardCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_discard_active_call(self):
        requested = []

        class FakeTG:
            async def get_entity(self, _channel):
                return SimpleNamespace()

            async def __call__(self, req):
                requested.append(req)
                if len(requested) == 1:
                    return SimpleNamespace(full_chat=SimpleNamespace(
                        call=SimpleNamespace(id=7, access_hash=8)))
                return SimpleNamespace()

        with patch.object(web_app, "telegram", FakeTG()):
            self.assertTrue(await web_app._discard_group_call("@c"))
        self.assertEqual(len(requested), 2)
        self.assertEqual(requested[1].call.id, 7)
        self.assertEqual(requested[1].call.access_hash, 8)

    async def test_discard_without_active_call(self):
        class FakeTG:
            async def get_entity(self, _channel):
                return SimpleNamespace()

            async def __call__(self, _req):
                return SimpleNamespace(full_chat=SimpleNamespace(call=None))

        with patch.object(web_app, "telegram", FakeTG()):
            self.assertFalse(await web_app._discard_group_call("@c"))

    async def test_discard_never_raises(self):
        class FakeTG:
            async def get_entity(self, _channel):
                raise RuntimeError("nope")

        with patch.object(web_app, "telegram", FakeTG()):
            self.assertFalse(await web_app._discard_group_call("@c"))
        with patch.object(web_app, "telegram", None):
            self.assertFalse(await web_app._discard_group_call("@c"))
            self.assertFalse(await web_app._discard_group_call(""))


class FakeGroupCallTelegram:
    def __init__(self, effect=None):
        self.effect = effect

    async def __call__(self, _request):
        if isinstance(self.effect, Exception):
            raise self.effect
        return SimpleNamespace()


class EnsureGroupCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_random_id_fits_int32(self):
        captured = {}
        orig = web_app.functions.phone.CreateGroupCallRequest

        def spy(*args, **kwargs):
            captured.update(kwargs)
            return orig(*args, **kwargs)

        with patch.object(web_app.functions.phone, "CreateGroupCallRequest",
                           side_effect=spy), patch.object(
            web_app, "telegram", FakeGroupCallTelegram()
        ):
            await web_app._ensure_group_call_live(SimpleNamespace())
        self.assertTrue(1 <= captured["random_id"] <= 2**31 - 1)

    async def test_already_active_is_ignored(self):
        with patch.object(web_app, "telegram",
                           FakeGroupCallTelegram(RuntimeError("GROUPCALL_ALREADY_ACTIVE"))):
            await web_app._ensure_group_call_live(SimpleNamespace())

    async def test_unexpected_error_is_reraised_not_faked(self):
        with patch.object(web_app, "telegram",
                           FakeGroupCallTelegram(struct.error("'i' format requires ..."))):
            with self.assertRaises(struct.error):
                await web_app._ensure_group_call_live(SimpleNamespace())


if __name__ == "__main__":
    unittest.main()
