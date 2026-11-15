"""Tests: Aparat URL validation, metadata fetching, download safety."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from conftest import SKILL_DIR, fake_metadata

from scripts import extractor


class TestUrlValidation(unittest.TestCase):
    def test_valid_standard_urls(self):
        for url, expected in [
            ("https://www.aparat.com/v/wP8On", "wP8On"),
            ("http://aparat.com/v/ABCdef123", "ABCdef123"),
            ("https://www.aparat.com/v/8dflw/", "8dflw"),
            ("https://m.aparat.com/v/xyz99", "xyz99"),
            ("https://www.aparat.com/video/video/embed/videohash/LXq3x",
             "LXq3x"),
        ]:
            with self.subTest(url=url):
                self.assertEqual(extractor.parse_url(url), expected)
                vid, err = extractor.validate(url)
                self.assertIsNone(err)
                self.assertEqual(vid, expected)

    def test_invalid_urls(self):
        bad = [
            "https://youtube.com/watch?v=abc",
            "https://www.aparat.com/",
            "https://www.aparat.com/v/",
            "not-a-url",
            "https://www.aparat.com/v/!!bad!!",
            "",
            None,
            "https://aparat.com/v/abc/extra/path",
        ]
        for url in bad:
            with self.subTest(url=url):
                self.assertIsNone(extractor.parse_url(url))
                vid, err = extractor.validate(url or "")
                self.assertIsNone(vid)
                self.assertIsNotNone(err)

    def test_normalize_url(self):
        self.assertEqual(extractor.normalize_url("abc"),
                         "https://www.aparat.com/v/abc")


class TestMetadataFetch(unittest.TestCase):
    def test_fetch_metadata_unavailable_raises(self):
        with mock.patch.object(extractor, "fetch_metadata_ytdlp",
                               return_value=None), \
             mock.patch.object(extractor, "fetch_metadata_scrape",
                               return_value=None):
                with self.assertRaises(extractor.VideoUnavailableError):
                    extractor.fetch_metadata("https://www.aparat.com/v/z9z9z")

    def test_fetch_metadata_ytdlp_path(self):
        raw = {"id": "wP8On", "title": "تست", "duration": 231,
               "formats": [{"format_id": "http-360p", "height": 360}],
               "uploader": "u"}
        with mock.patch("subprocess.run") as mrun:
            mrun.return_value = mock.Mock(
                returncode=0, stdout=json.dumps(raw), stderr="")
            got = extractor.fetch_metadata_ytdlp("https://www.aparat.com/v/wP8On")
        self.assertEqual(got["title"], "تست")

    def test_fetch_metadata_ytdlp_404_raises_unavailable(self):
        with mock.patch("subprocess.run") as mrun:
            mrun.return_value = mock.Mock(
                returncode=1, stdout="",
                stderr="ERROR: [Aparat] wP8On: Unable to download webpage: "
                       "HTTP Error 404")
            with self.assertRaises(extractor.VideoUnavailableError):
                extractor.fetch_metadata_ytdlp("https://www.aparat.com/v/wP8On")

    def test_scrape_parses_options_json(self):
        page = """
        <html><head><meta property="og:title" content="عنوان تست"></head>
        <script>options = {"duration": 120, "poster": "https://x/y.jpg"};</script>
        </html>
        """
        with mock.patch.object(extractor, "_http_get", return_value=page):
            got = extractor.fetch_metadata_scrape("TEST1")
        self.assertEqual(got["title"], "عنوان تست")
        self.assertEqual(got["duration"], 120)
        self.assertEqual(got["thumbnail"], "https://x/y.jpg")

    def test_fetch_metadata_prefers_ytdlp_then_scrape(self):
        with mock.patch.object(extractor, "fetch_metadata_ytdlp",
                               return_value={"id": "A", "title": "from-ytdlp"}):
            got = extractor.fetch_metadata("https://www.aparat.com/v/A")
        self.assertEqual(got["title"], "from-ytdlp")
        with mock.patch.object(extractor, "fetch_metadata_ytdlp",
                               return_value=None), \
             mock.patch.object(extractor, "fetch_metadata_scrape",
                               return_value={"id": "A", "title": "scraped"}):
            got = extractor.fetch_metadata("https://www.aparat.com/v/A")
        self.assertEqual(got["title"], "scraped")


class TestDownloadSafety(unittest.TestCase):
    def test_download_uses_argv_no_shell(self):
        from scripts import extractor as ex

        fake = mock.Mock(returncode=0, stderr="")
        with mock.patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
             mock.patch("subprocess.run", return_value=fake) as mrun, \
             mock.patch.object(Path, "iterdir", return_value=[]):
            with self.assertRaises(RuntimeError):
                ex.download_best("https://www.aparat.com/v/ABCdef",
                                 self.mkdtmp(), max_bytes=1000, audio_only=True)
        argv = mrun.call_args[0][0]
        self.assertIsInstance(argv, list)
        self.assertFalse(any("||" in a or ";" in a for a in argv))
        self.assertIn("-f", argv)
        self.assertEqual(argv[0], "yt-dlp")

    def mkdtmp(self):
        import tempfile

        return Path(tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()
