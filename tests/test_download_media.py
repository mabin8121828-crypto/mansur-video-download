"""Regression checks, including real conversion and full decoding."""

from argparse import Namespace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import download_media as media


def arguments(**overrides):
    values = dict(command="download", url="https://www.youtube.com/watch?v=test123",
        output_dir="", timeout=60, cookies=None, cookies_from_browser=None, cookie_source="none",
        playlist=False, format="", max_height=1080, extras=False, short_video_backend=None)
    return Namespace(**(values | overrides))


class CoreTests(unittest.TestCase):
    def test_canonical_urls_remove_tracking_but_keep_video(self):
        expected = "https://www.youtube.com/watch?v=abc123"
        for url in ("https://youtu.be/abc123?si=tracking", "https://www.youtube.com/shorts/abc123",
                    "https://www.youtube.com/watch?v=abc123&list=RDabc123&start_radio=1"):
            self.assertEqual(media.canonical_url(url), expected)
        self.assertEqual(media.canonical_url("https://x.com/elonmusk/status/123?s=20"),
                         "https://x.com/i/status/123")

    def test_invalid_url_credentials_and_local_path_rejected(self):
        for value in ("file:///tmp/video", "not a url", "https://user:password@example.com/video"):
            with self.assertRaises(media.WorkflowError):
                media.canonical_url(value)

    def test_platform_routing_uses_hostname(self):
        self.assertEqual(media.platform_prefix("https://v.douyin.com/abc/"), "DY")
        self.assertEqual(media.platform_prefix("https://www.facebook.com/reel/123"), "FB")
        self.assertEqual(media.platform_prefix("https://www.tiktok.com/@a/video/1"), "TT")
        self.assertEqual(media.platform_prefix("https://douyin.com.example.com/a"), "VIDEO")

    def test_format_caps_and_unlimited(self):
        self.assertTrue(media.quality_sort(1080).startswith("res:1080,"))
        self.assertTrue(media.quality_sort(0).startswith("res,"))

    def test_portrait_full_hd_and_codec_preference(self):
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True,
                "format": "bestvideo+bestaudio/best", "format_sort": media.quality_sort(1080).split(",")}) as downloader:
            formats = [dict(format_id=identifier, width=width, height=height, vcodec=codec,
                            acodec="aac", ext="mp4", url=f"https://example.com/{identifier}.mp4")
                       for identifier, width, height, codec in (
                           ("portrait_hd", 720, 1280, "avc1"),
                           ("portrait_full_hd_av1", 1080, 1920, "av01"),
                           ("portrait_full_hd", 1080, 1920, "avc1"),
                           ("portrait_4k", 2160, 3840, "avc1"))]
            result = downloader.process_video_result({"id": "test", "title": "Test", "formats": formats}, download=False)
            self.assertEqual(result["format_id"], "portrait_full_hd")

    def test_compatibility_rejects_risky_codecs_and_pixel_format(self):
        base = dict(video_codec="h264", audio_codec="aac", pixel_format="yuv420p")
        self.assertTrue(media.compatible(base))
        self.assertTrue(media.compatible(base | {"audio_codec": None}))
        for changes in ({"video_codec": "hevc"}, {"audio_codec": "opus"}, {"pixel_format": "yuv420p10le"}):
            self.assertFalse(media.compatible(base | changes))

    def test_cookie_copy_does_not_modify_original(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            work = root / "work"
            work.mkdir()
            original = root / "cookies.txt"
            original.write_text("private test cookie", encoding="utf-8")
            args = arguments(cookies=str(original))
            strategies = media.cookie_strategies(args, "YT", work)
            self.assertEqual(len(strategies), 1)
            strategies[0][1].write_text("download tool changed its copy", encoding="utf-8")
            self.assertEqual(original.read_text(), "private test cookie")

    def test_auto_public_first_without_browser_access(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cookie = root / "provided.txt"
            cookie.write_text("test")
            work = root / "work"
            work.mkdir()
            with patch.dict(os.environ, {"MANSUR_VIDEO_COOKIE_FILE": str(cookie)}):
                strategies = media.cookie_strategies(arguments(cookie_source="auto"), "YT", work)
            self.assertEqual([s[0] for s in strategies], ["public", "cookie-file"])
            self.assertTrue(all(s[2] is None for s in strategies))

    def test_error_report_redacts_credentials(self):
        text = media.safe_error("failed https://example.com/video?token=secret\nCookie: secret_cookie")
        self.assertNotIn("secret", text)
        self.assertIn("[redacted]", text)
        self.assertNotIn("synthetic-private-list", media.public_url("https://example.com/video?list=synthetic-private-list"))

    def test_safe_name_and_reserved_windows_names(self):
        self.assertEqual(media.safe_stem("a/b:c"), "a_b_c")
        for name in ("CON", "nul.mp4", "LPT1", "..."):
            with self.assertRaises(media.WorkflowError):
                media.safe_stem(name)

    def test_cleanup_only_deletes_owned_job(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            work = root / "._download_work" / uuid.uuid4().hex
            sibling = root / "._download_work" / uuid.uuid4().hex
            work.mkdir(parents=True)
            sibling.mkdir()
            (sibling / "old.part").write_text("keep")
            media.cleanup_job(work, root)
            self.assertFalse(work.exists())
            self.assertTrue((sibling / "old.part").exists())
            with self.assertRaises(media.WorkflowError):
                media.cleanup_job(root, root)

    def test_cross_process_download_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            code = ("from pathlib import Path; import download_media as m; import sys; "
                    "lock=m.download_lock(Path(sys.argv[1]),'same-video'); lock.__enter__()")
            with media.download_lock(root, "same-video"):
                result = subprocess.run([sys.executable, "-c", code, str(root)],
                                        cwd=media.ROOT, capture_output=True, text=True, timeout=15)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("already in use", result.stderr)
            with media.download_lock(root, "same-video"):
                pass

    def test_timeout_stops_process(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(media.WorkflowError, "Timed out"):
                media.run([sys.executable, "-c", "import time; time.sleep(30)"], "test", Path(folder), 1)


class MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix="yt_workflow_test_")
        cls.root = Path(cls.folder.name)
        cls.tools = media.Tools.discover()
        cls.compatible_source = cls.root / "compatible.mp4"
        cls.incompatible_source = cls.root / "incompatible.webm"
        cls.silent_source = cls.root / "silent.mp4"
        base = [cls.tools.ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                "testsrc2=size=160x120:rate=12", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
                "-t", "1.2"]
        subprocess.run(base + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", str(cls.compatible_source)], check=True, timeout=30)
        subprocess.run(base + ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
                        "-c:a", "libopus", str(cls.incompatible_source)], check=True, timeout=30)
        subprocess.run([cls.tools.ffmpeg, "-nostdin", "-v", "error", "-i", str(cls.compatible_source),
                        "-an", "-c:v", "copy", str(cls.silent_source)], check=True, timeout=30)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def setUp(self):
        self.case = Path(tempfile.mkdtemp(dir=self.root))
        self.output = self.case / "output"
        self.output.mkdir()
        self.work = self.output / "._download_work" / uuid.uuid4().hex
        self.work.mkdir(parents=True)

    def prepare(self, source=None, name="test"):
        return media.prepare_video(source or self.compatible_source, self.output, name,
                                   self.tools, self.work, 60)

    def test_compatible_source_remux_without_reencoding(self):
        self.assertFalse(media.faststart(self.compatible_source))
        result = self.prepare()
        self.assertFalse(result["converted"])
        self.assertTrue(result["faststart"])
        self.assertTrue(result["full_decode_verified"])
        self.assertTrue(self.compatible_source.is_file())

    def test_vp9_opus_converted_to_h264_aac(self):
        result = self.prepare(self.incompatible_source)
        self.assertEqual(result["video_codec"], "h264")
        self.assertEqual(result["audio_codec"], "aac")
        self.assertEqual(result["pixel_format"], "yuv420p")
        self.assertTrue(result["converted"])
        self.assertTrue(result["full_decode_verified"])

    def test_silent_video_keeps_no_audio(self):
        result = self.prepare(self.silent_source)
        self.assertIsNone(result["audio_codec"])
        self.assertTrue(result["iphone_compatible"])

    def test_existing_output_not_replaced(self):
        first = self.prepare()
        path = Path(first["path"])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = self.prepare()
        self.assertTrue(result["reused"])
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_same_name_same_duration_different_source_is_conflict(self):
        first = self.prepare()
        original_hash = media.file_sha256(Path(first["path"]))
        alternative = self.case / "different.mp4"
        subprocess.run([self.tools.ffmpeg, "-nostdin", "-v", "error", "-i", str(self.compatible_source),
            "-vf", "negate", "-c:v", "libx264", "-c:a", "copy", str(alternative)], check=True, timeout=30)
        with self.assertRaisesRegex(media.WorkflowError, "different source"):
            self.prepare(alternative)
        self.assertEqual(media.file_sha256(Path(first["path"])), original_hash)

    def test_bad_existing_output_is_left_untouched(self):
        path = self.output / "test.mp4"
        path.write_bytes(b"not a video")
        with self.assertRaises(media.WorkflowError):
            self.prepare()
        self.assertEqual(path.read_bytes(), b"not a video")

    def test_decode_failure_never_publishes(self):
        original_run = media.run

        def failing_decode(command, stage, work, timeout):
            if stage == "decode":
                raise media.WorkflowError("decode", "Broken frame")
            return original_run(command, stage, work, timeout)

        with patch.object(media, "run", side_effect=failing_decode):
            with self.assertRaisesRegex(media.WorkflowError, "Broken frame"):
                self.prepare()
        self.assertFalse((self.output / "test.mp4").exists())

    def test_verified_staged_download_and_cleanup(self):
        original_run = media.run

        def fake_download(command, stage, work, timeout):
            if stage == "metadata":
                return json.dumps({"id": "test123", "title": "Test video"})
            if stage == "download":
                destination = work / "YT_test123.mp4"
                shutil.copyfile(self.compatible_source, destination)
                (work / "final_paths.jsonl").write_text(json.dumps(str(destination)) + "\n", encoding="utf-8")
                return ""
            return original_run(command, stage, work, timeout)

        with patch.object(media, "ROOT", self.case), patch.object(media, "run", side_effect=fake_download), \
             patch.object(self.tools, "describe", return_value={"test": True}):
            result = media.execute_job(arguments(output_dir=str(self.output)), self.tools)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["temporary_files_cleaned"])
        self.assertTrue((self.output / "YT_test123.mp4").is_file())
        self.assertTrue(Path(result["report_path"]).is_file())
        self.assertEqual(len(list((self.output / "._download_work").iterdir())), 1)

    def test_exit_zero_without_output_is_not_success(self):
        def no_output(command, stage, work, timeout):
            return '{"id":"test123"}' if stage == "metadata" else ""

        with patch.object(media, "ROOT", self.case), patch.object(media, "run", side_effect=no_output), \
             patch.object(self.tools, "describe", return_value={}):
            result = media.execute_job(arguments(output_dir=str(self.output)), self.tools)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "download")
        self.assertTrue(Path(result["retained_work_dir"]).is_dir())
        self.assertFalse((self.output / "YT_test123.mp4").exists())

    def test_failed_job_deletes_only_cookie_copy(self):
        cookie = self.case / "provided.txt"
        cookie.write_text("private-cookie")
        with patch.object(media, "ROOT", self.case), \
             patch.object(media, "run", side_effect=media.WorkflowError("metadata", "failed")), \
             patch.object(self.tools, "describe", return_value={}):
            result = media.execute_job(arguments(output_dir=str(self.output), cookies=str(cookie)), self.tools)
        self.assertFalse(result["ok"])
        self.assertEqual(cookie.read_text(), "private-cookie")
        self.assertFalse((Path(result["retained_work_dir"]) / "private_cookies.txt").exists())

    def test_cleanup_failure_is_reported_as_failure(self):
        args = Namespace(command="prepare", path=str(self.compatible_source), name="test",
                         output_dir=str(self.output), timeout=60)
        with patch.object(media, "ROOT", self.case), patch.object(self.tools, "describe", return_value={}), \
             patch.object(media, "cleanup_job", side_effect=media.WorkflowError("cleanup", "failed")):
            result = media.execute_job(args, self.tools)
        self.assertFalse(result["ok"])
        self.assertEqual(result["stage"], "cleanup")
        self.assertTrue(Path(result["files"][0]["path"]).is_file())

    def test_metadata_public_success_download_failure_retries_cookie(self):
        cookie = self.case / "provided.txt"
        cookie.write_text("test-cookie")
        seen = []

        def fake_download(command, stage, work, timeout):
            seen.append((stage, "--cookies" in command))
            if stage == "metadata":
                return '{"id":"test123"}'
            if stage == "download" and "--cookies" not in command:
                raise media.WorkflowError("download", "Login required")
            destination = work / "YT_test123.mp4"
            shutil.copyfile(self.compatible_source, destination)
            (work / "final_paths.jsonl").write_text(json.dumps(str(destination)) + "\n", encoding="utf-8")
            return ""

        with patch.dict(os.environ, {"MANSUR_VIDEO_COOKIE_FILE": str(cookie)}), \
             patch.object(media, "run", side_effect=fake_download):
            files, _ = media.download_ytdlp("https://youtube.com/watch?v=test123", "YT",
                arguments(output_dir=str(self.output), cookie_source="auto"), self.tools, self.work, [])
        self.assertEqual(len(files), 1)
        self.assertEqual(seen, [("metadata", False), ("download", False), ("metadata", True), ("download", True)])

    def test_repeat_skips_network_video_download(self):
        self.prepare(name="YT_test123")
        with patch.object(media, "run", return_value='{"id":"test123"}') as runner:
            files, _ = media.download_ytdlp("https://youtube.com/watch?v=test123", "YT",
                arguments(output_dir=str(self.output)), self.tools, self.work, [])
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].samefile(self.output / "YT_test123.mp4"))
        self.assertEqual(runner.call_count, 1)

    def test_cached_duration_and_resolution_match_platform_request(self):
        self.prepare(name="YT_test123")
        original_run = media.run
        for changes in ({"duration": 120}, {"width": 640, "height": 480}):
            with self.subTest(changes=changes):
                def fake_metadata(command, stage, work, timeout):
                    if stage == "metadata":
                        return json.dumps({"id": "test123"} | changes)
                    return original_run(command, stage, work, timeout)

                with patch.object(media, "run", side_effect=fake_metadata), \
                     patch.object(self.tools, "describe", return_value={}):
                    result = media.execute_job(arguments(output_dir=str(self.output)), self.tools)
                self.assertFalse(result["ok"])
                self.assertEqual(result["stage"], "verify")

    def test_saved_source_urls_hide_signed_query_values(self):
        original_run = media.run

        def fake_download(command, stage, work, timeout):
            if stage == "metadata":
                return '{"id":"test123"}'
            if stage == "download":
                destination = work / "VIDEO_test123.mp4"
                shutil.copyfile(self.compatible_source, destination)
                (work / "final_paths.jsonl").write_text(json.dumps(str(destination)) + "\n", encoding="utf-8")
                return ""
            return original_run(command, stage, work, timeout)

        with patch.object(media, "run", side_effect=fake_download), patch.object(self.tools, "describe", return_value={}):
            result = media.execute_job(arguments(output_dir=str(self.output),
                url="https://example.com/video.mp4?token=synthetic-test-secret"), self.tools)
        self.assertTrue(result["ok"], result)
        for path in self.output.rglob("*.json"):
            self.assertNotIn("synthetic-test-secret", path.read_text(encoding="utf-8"))
        self.assertIn("%5Bredacted%5D", result["source_url"])

    def test_existing_output_still_fetches_requested_extras(self):
        first = self.prepare(name="YT_test123")
        video_hash = media.file_sha256(Path(first["path"]))
        original_run = media.run
        stages = []

        def fake_attachments(command, stage, work, timeout):
            stages.append(stage)
            if stage == "metadata":
                return '{"id":"test123", "subtitles":{"en":[]}}'
            if stage == "attachments":
                self.assertIn("--skip-download", command)
                (work / "YT_test123.en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest\n", encoding="utf-8")
                return ""
            return original_run(command, stage, work, timeout)

        with patch.object(media, "run", side_effect=fake_attachments), patch.object(self.tools, "describe", return_value={}):
            result = media.execute_job(arguments(output_dir=str(self.output), extras=True), self.tools)
        self.assertTrue(result["ok"], result)
        self.assertIn("attachments", stages)
        self.assertNotIn("download", stages)
        self.assertTrue((self.output / "YT_test123.en.srt").is_file())
        self.assertEqual(media.file_sha256(Path(first["path"])), video_hash)

    def test_failed_command_logs_redact_query_and_authorization(self):
        command = [sys.executable, "-c", "import sys; print('https://example.com/v?token=synthetic-secret', file=sys.stderr); "
                   "print('Authorization: Bearer synthetic-secret', file=sys.stderr); sys.exit(2)"]
        original_run = media.run

        def failing_metadata(args, stage, work, timeout):
            if stage == "metadata":
                return original_run(command, stage, work, timeout)
            return original_run(args, stage, work, timeout)

        with patch.object(media, "run", side_effect=failing_metadata), patch.object(self.tools, "describe", return_value={}):
            result = media.execute_job(arguments(output_dir=str(self.output)), self.tools)
        self.assertFalse(result["ok"])
        for path in Path(result["retained_work_dir"]).glob("*.err"):
            self.assertNotIn("synthetic-secret", path.read_text(encoding="utf-8"))

    def test_real_ytdlp_local_http_download_delivery(self):
        from functools import partial
        from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(self.root)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(media, "ROOT", self.case), patch.object(self.tools, "describe", return_value={}):
                result = media.execute_job(arguments(output_dir=str(self.output),
                    url=f"http://127.0.0.1:{server.server_port}/compatible.mp4"), self.tools)
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["files"][0]["full_decode_verified"])
            self.assertTrue(result["temporary_files_cleaned"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_queued_short_video_is_not_a_download(self):
        backend = self.case / "queued_backend.py"
        backend.write_text("def download_url(url, options):\n    return {'ok': True, 'status': 'queued', 'downloaded': False}\n", encoding="utf-8")
        with patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaises(media.WorkflowError):
                media.short_video_worker("https://v.douyin.com/test", self.work, str(backend))

    def test_short_video_backend_receives_explicit_cookie_options(self):
        backend = self.case / "options_backend.py"
        backend.write_text("import json\nfrom pathlib import Path\ndef download_url(url, options):\n"
            "    Path(options['outputDir'], 'options.json').write_text(json.dumps(options))\n"
            "    return {'ok': True}\n", encoding="utf-8")
        with patch("sys.stdout", new_callable=io.StringIO):
            media.short_video_worker("https://v.douyin.com/test", self.work, str(backend), "private-copy.txt", "edge")
        options = json.loads((self.work / "options.json").read_text())
        self.assertEqual(options["cookiesFile"], "private-copy.txt")
        self.assertEqual(options["cookiesFromBrowser"], "edge")


if __name__ == "__main__":
    unittest.main()
