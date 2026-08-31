import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def iter_download(self, _document, *, offset, file_size, **_kwargs):
        self.downloads += 1
        end = min(file_size, offset + web_app.MEDIA_CACHE_BLOCK_SIZE)
        return FakeDownloadIterator(self.data[offset:end])


class MediaCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_streamer_history_route_returns_spa(self):
        response = await web_app.streamer_page("susu/2026-08-01")
        self.assertEqual(Path(response.path), web_app.STATIC_DIR / "index.html")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")

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
                cached, hit = await web_app._media_block(message, details, 99, 0)

        self.assertEqual(fake_telegram.downloads, 1)
        self.assertTrue(all(result == data[: web_app.MEDIA_CACHE_BLOCK_SIZE] for result, _hit in results))
        self.assertFalse(results[0][1])
        self.assertTrue(all(hit for _result, hit in results[1:]))
        self.assertTrue(hit)
        self.assertEqual(cached, data[: web_app.MEDIA_CACHE_BLOCK_SIZE])

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


if __name__ == "__main__":
    unittest.main()
