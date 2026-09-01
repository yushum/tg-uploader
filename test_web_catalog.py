import unittest

from web_catalog import parse_http_range, parse_recording


class CatalogParsingTests(unittest.TestCase):
    def test_douyin_name_and_part(self):
        part = parse_recording(
            "/downloads/douyin/抖音直播/黄甜甜/2026-02-26/黄甜甜_2026-02-26_05-53-48_002.mp4",
            42,
        )
        self.assertEqual((part.streamer, part.date, part.time), ("黄甜甜", "2026-02-26", "05:53:48"))
        self.assertEqual(part.part_order, (2,))
        self.assertEqual(part.platform, "抖音")

    def test_biliup_iso_name(self):
        part = parse_recording("/downloads/biliup/永雏塔菲2026-08-20T21_59_24_005.mp4", 43)
        self.assertEqual((part.streamer, part.date, part.time), ("永雏塔菲", "2026-08-20", "21:59:24"))
        self.assertEqual(part.part_order, (5,))
        self.assertEqual(part.platform, "B站")

    def test_date_directory_fallback(self):
        part = parse_recording("/downloads/抖音直播/小萝卜/2025-04-15/小萝卜.mp4", 44)
        self.assertEqual((part.streamer, part.date, part.time), ("小萝卜", "2025-04-15", "00:00:00"))

    def test_nonstandard_name_uses_parent_and_upload_date(self):
        part = parse_recording(
            "/downloads/manual/某主播/随手改过的标题.mp4",
            45,
            "2026-08-31 12:34:56",
        )
        self.assertEqual((part.streamer, part.date, part.time), ("某主播", "2026-08-31", "00:00:00"))
        self.assertEqual(part.part_order, ())


class HttpRangeTests(unittest.TestCase):
    def test_open_ended_range(self):
        self.assertEqual(parse_http_range("bytes=100-", 1000), (100, 999, True))

    def test_suffix_range(self):
        self.assertEqual(parse_http_range("bytes=-100", 1000), (900, 999, True))

    def test_invalid_range(self):
        with self.assertRaises(ValueError):
            parse_http_range("bytes=1000-", 1000)


if __name__ == "__main__":
    unittest.main()
