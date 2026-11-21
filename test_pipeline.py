import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import build_short
import broll_fetcher
import idea_generator
import youtube_publisher
import analytics_loop
import compile_weekly


class PipelineTest(unittest.TestCase):
    def test_idea_validation_and_schedule_helpers(self):
        spec = idea_generator.ShortSpec(
            title="Test Three Tools #Shorts",
            description="Test description",
            tags=["test"],
            urls=["https://example.com"],
            broll_queries=["laptop screen", "desk setup", "phone notes"],
            script=" ".join(["word"] * 80),
        )
        idea_generator.validate_spec(spec, "test topic")
        self.assertTrue(spec.title.endswith("#Shorts"))
        self.assertEqual(idea_generator.next_id([{"id": "short_02"}]), 3)

    def test_dynamic_ass_timing_and_filters(self):
        self.assertEqual(build_short.format_ass_time(10_000_000), "0:00:01.00")
        self.assertEqual(build_short.escape_ass_text("A{B}\\C"), "A\\{B\\}\\\\C")
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / "captions.ass"
            mp3 = Path(directory) / "voice.mp3"
            mp3.write_bytes(b"fake")
            ass.write_text(
                "[Script Info]\nScriptType: v4.00+\n",
                encoding="utf-8",
            )
            self.assertEqual(build_short.get_duration, build_short.get_duration)
            escaped = build_short.escape_filter_path(ass)
            self.assertIn("ass=filename=", escaped)

    def test_queue_priority_resolves_local_urls_broll_and_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            local = root / "manual.mp4"
            local.write_bytes(b"video")
            item_temp = root / "temp"
            item_temp.mkdir()
            self.assertEqual(
                build_short.resolve_raw_video(
                    {"local_video": str(local)}, "short_01", item_temp
                ),
                local,
            )
            with patch("build_short.record_web_demo", return_value=root / "raw.webm") as record:
                result = build_short.resolve_raw_video(
                    {"urls": ["https://example.com"]}, "short_02", item_temp
                )
                record.assert_called_once()
                self.assertEqual(result, root / "raw.webm")
            with patch(
                "broll_fetcher.fetch_and_stitch_broll",
                return_value=root / "broll.mp4",
            ) as broll:
                result = build_short.resolve_raw_video(
                    {"broll_queries": ["laptop desk", "phone app", "typing hands"]},
                    "short_03",
                    item_temp,
                )
                broll.assert_called_once()
                self.assertEqual(result, root / "broll.mp4")
            image = root / "screen.png"
            image.write_bytes(b"image")
            with patch(
                "broll_fetcher.images_to_ken_burns_video",
                return_value=root / "images.mp4",
            ) as kenburns:
                result = build_short.resolve_raw_video(
                    {"images": [str(image)]}, "short_04", item_temp
                )
                kenburns.assert_called_once()
                self.assertEqual(result, root / "images.mp4")

    def test_pixabay_and_local_fallback_paths(self):
        hit = {
            "videos": {
                "medium": {"url": "https://example.com/video.mp4"},
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temp_dir = root / "temp"
            out = root / "broll.mp4"
            temp_dir.mkdir()

            def touch_destination(*args):
                Path(args[-1]).touch()

            def create_concat_output(*args, **kwargs):
                if kwargs.get("check") is not True:
                    raise AssertionError("FFmpeg concat must be checked.")
                Path(args[0][-1]).touch()

            with patch("broll_fetcher.require_ffmpeg"), patch.dict(
                "os.environ", {"PIXABAY_API_KEY": "test"}
            ), patch(
                "broll_fetcher.search_pixabay", return_value=[hit]
            ), patch("broll_fetcher.download_clip"), patch(
                "broll_fetcher.normalize_clip", side_effect=touch_destination
            ), patch("broll_fetcher.subprocess.run", side_effect=create_concat_output):
                result = broll_fetcher.fetch_and_stitch_broll(
                    ["desk setup"], out, temp_dir
                )
                self.assertEqual(result, out)
                self.assertTrue(out.is_file())

            fallback_dir = root / "fallback"
            fallback_dir.mkdir()
            (fallback_dir / "fallback.mp4").write_bytes(b"source")
            fallback_output = root / "fallback.mp4"
            with patch("broll_fetcher.require_ffmpeg"), patch.dict(
                "os.environ", {"PIXABAY_API_KEY": ""}
            ), patch.object(
                broll_fetcher, "FALLBACK_DIR", fallback_dir
            ), patch(
                "broll_fetcher.normalize_clip", side_effect=touch_destination
            ), patch("broll_fetcher.subprocess.run", side_effect=create_concat_output):
                result = broll_fetcher.fetch_and_stitch_broll(
                    ["desk setup"], fallback_output, temp_dir
                )
                self.assertEqual(result, fallback_output)
                self.assertTrue(fallback_output.is_file())

    def test_pixabay_error_does_not_echo_api_key(self):
        response = type("Response", (), {"ok": False, "status_code": 400})()
        with patch("broll_fetcher.requests.get", return_value=response):
            with self.assertRaisesRegex(ValueError, "HTTP 400") as error:
                broll_fetcher.search_pixabay("desk setup", "test-secret")
        self.assertNotIn("test-secret", str(error.exception))

    def test_youtube_publish_validation(self):
        self.assertEqual(
            set(youtube_publisher.SCOPES),
            {
                "https://www.googleapis.com/auth/youtube.upload",
                "https://www.googleapis.com/auth/youtube.readonly",
                "https://www.googleapis.com/auth/yt-analytics.readonly",
            },
        )
        future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self.assertTrue(youtube_publisher.parse_publish_time(future).endswith("Z"))
        with self.assertRaises(ValueError):
            youtube_publisher.parse_publish_time("2020-01-01T00:00:00Z")

    def test_compilation_filter_graph_and_chapters(self):
        graph, video_label, duration, chapter_starts = compile_weekly.make_filter_graph(
            [20.0, 30.0, 40.0], 0.6
        )
        self.assertIn("xfade=transition=fade", graph)
        self.assertIn("acrossfade=d=0.600", graph)
        self.assertEqual(video_label, "vx2")
        self.assertAlmostEqual(duration, 88.8)
        self.assertEqual(chapter_starts, [19.4, 48.8])
        self.assertEqual(compile_weekly.format_chapter_time(3661), "01:01:01")
        with self.assertRaises(ValueError):
            compile_weekly.make_filter_graph([0.5, 10.0], 0.6)

    def test_next_saturday_utc_uses_upcoming_future_slot(self):
        saturday_before_slot = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        saturday_after_slot = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)
        with patch("compile_weekly.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = saturday_before_slot
            self.assertEqual(
                compile_weekly.get_next_saturday_utc(),
                "2026-11-21T15:00:00Z",
            )
            mocked_datetime.now.return_value = saturday_after_slot
            self.assertEqual(
                compile_weekly.get_next_saturday_utc(),
                "2026-11-28T15:00:00Z",
            )
        with self.assertRaises(ValueError):
            compile_weekly.get_next_saturday_utc(hour_utc=24)

    def test_compilation_candidate_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            for name in ("short_01", "short_02", "short_03"):
                (output_dir / f"{name}.mp4").write_bytes(b"video")
            queue = [
                {"id": "short_01", "status": "done"},
                {"id": "short_02", "status": "rendered"},
                {"id": "short_03", "status": "done", "compiled": True},
            ]
            self.assertEqual(
                [item["id"] for item in compile_weekly.select_candidates(queue, output_dir)],
                ["short_01"],
            )

    def test_analytics_ranking_and_gemini_topics(self):
        class FakeRequest:
            def __init__(self, value):
                self.value = value

            def execute(self):
                return self.value

        class FakeReports:
            def query(self, **kwargs):
                if kwargs.get("ids") != "channel==MINE":
                    raise AssertionError("Analytics must query the authenticated channel.")
                return FakeRequest(
                    {"rows": [["video_a", 100, 50.0], ["video_b", 200, 10.0]]}
                )

        class FakeAnalytics:
            def __init__(self):
                self.reports_instance = FakeReports()

            def reports(self):
                return self.reports_instance

        class FakeVideos:
            def list(self, **kwargs):
                if kwargs.get("part") != "snippet" or not kwargs.get("id"):
                    raise AssertionError("The analytics video IDs must be resolved.")
                return FakeRequest(
                    {
                        "items": [
                            {"id": "video_a", "snippet": {"title": "A"}},
                            {"id": "video_b", "snippet": {"title": "B"}},
                        ]
                    }
                )

        class FakeYoutube:
            def videos(self):
                return FakeVideos()

        def fake_build(service, version, credentials):
            if version != ("v2" if service == "youtubeAnalytics" else "v3"):
                raise AssertionError("Unexpected YouTube API version.")
            if credentials is None:
                raise AssertionError("Google credentials are required.")
            return FakeAnalytics() if service == "youtubeAnalytics" else FakeYoutube()

        with patch("analytics_loop.load_credentials", return_value=object()), patch(
            "googleapiclient.discovery.build", side_effect=fake_build
        ):
            ranked = analytics_loop.fetch_top_performing_titles()
        self.assertEqual([item["title"] for item in ranked], ["A", "B"])
        self.assertEqual(ranked[0]["score"], 5000.0)
        self.assertEqual(ranked[1]["score"], 2000.0)

        class FakeModels:
            def generate_content(self, **kwargs):
                if kwargs.get("model") != analytics_loop.MODEL:
                    raise AssertionError("Unexpected Gemini model.")
                return type(
                    "Response",
                    (),
                    {
                        "parsed": {
                            "winning_angles": [
                                "Topic one",
                                "Topic two",
                                "Topic three",
                                "Topic four",
                                "Topic five",
                            ]
                        },
                        "text": None,
                    },
                )()

        with patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"}), patch(
            "analytics_loop.create_gemini_client",
            return_value=type("Client", (), {"models": FakeModels()})(),
        ):
            topics = analytics_loop.generate_follow_up_topics(ranked)
        self.assertEqual(len(topics), 5)

    def test_standalone_surfaces_and_workflow_exist(self):
        for name in (
            "master_pipeline.py",
            "record_demo.py",
            "upload_short.py",
            "compile_weekly.py",
            "analytics_loop.py",
            "gemini_client.py",
            "idea_generator.py",
            "build_short.py",
            "broll_fetcher.py",
            "youtube_publisher.py",
            "videos.json",
            ".github/workflows/weekly_shorts.yml",
        ):
            self.assertTrue((Path(__file__).resolve().parent / name).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
