import re
import shutil
import subprocess
import tempfile
from collections import namedtuple
from pathlib import Path
from unittest import mock, skipUnless

from boto3.exceptions import S3UploadFailedError
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from videos.models import Video
from videos.progress import ProgressReporter
from videos.tasks import transcode_video
from videos.transcoding import (
    DISK_RESERVE_BYTES,
    INPUT_GUARDS,
    MAX_DIMENSION,
    MIN_TRANSCODE_TIMEOUT,
    MediaInfo,
    TranscodeError,
    build_ffmpeg_command,
    build_thumbnail_command,
    extract_thumbnail,
    max_video_kbps,
    parse_probe,
    plan_renditions,
    probe,
    require_free_space,
    transcode_timeout,
)

Status = Video.Status
MEDIA = MediaInfo(width=854, height=480, duration=12.5, has_audio=True)
DiskUsage = namedtuple("DiskUsage", "total used free")


def probe_data(width=1920, height=1080, duration="12.5", audio=True, **video):
    streams = [{"codec_type": "video", "width": width, "height": height, **video}]
    if audio:
        streams.append({"codec_type": "audio"})
    return {"streams": streams, "format": {"duration": duration}}


class PlanRenditionsTests(SimpleTestCase):
    def names(self, width, height):
        return [rendition.name for rendition in plan_renditions(width, height)]

    def test_ladder_never_upscales(self):
        cases = {
            (3840, 2160): ["360p", "720p", "1080p"],
            (1920, 1080): ["360p", "720p", "1080p"],
            (1280, 720): ["360p", "720p"],
            (854, 480): ["360p", "480p"],
            (640, 360): ["360p"],
            (426, 240): ["240p"],
        }
        for (width, height), expected in cases.items():
            with self.subTest(f"{width}x{height}"):
                self.assertEqual(self.names(width, height), expected)

    def test_portrait_uses_the_short_side(self):
        self.assertEqual(self.names(1080, 1920), ["360p", "720p", "1080p"])

    def test_odd_source_rounds_down_to_even(self):
        self.assertEqual(self.names(855, 481), ["360p", "480p"])

    def test_bitrate_grows_with_size(self):
        self.assertEqual(max_video_kbps(360), 800)
        self.assertEqual(max_video_kbps(480), 1467)
        self.assertEqual(max_video_kbps(1080), 5000)
        self.assertEqual(max_video_kbps(2160), 5000)


class ParseProbeTests(SimpleTestCase):
    def test_reads_metadata(self):
        self.assertEqual(
            parse_probe(probe_data()),
            MediaInfo(width=1920, height=1080, duration=12.5, has_audio=True),
        )

    def test_detects_missing_audio(self):
        self.assertFalse(parse_probe(probe_data(audio=False)).has_audio)

    def test_rotation_swaps_dimensions(self):
        for rotation in (90, -90, 270):
            with self.subTest(rotation):
                media = parse_probe(probe_data(side_data_list=[{"rotation": rotation}]))

                self.assertEqual((media.width, media.height), (1080, 1920))

    def test_upside_down_keeps_dimensions(self):
        media = parse_probe(probe_data(side_data_list=[{"rotation": 180}]))

        self.assertEqual((media.width, media.height), (1920, 1080))

    def test_reads_legacy_rotate_tag(self):
        media = parse_probe(probe_data(tags={"rotate": "90"}))

        self.assertEqual((media.width, media.height), (1080, 1920))

    def test_ignores_cover_art(self):
        data = probe_data()
        cover = {"codec_type": "video", "width": 300, "height": 300}
        data["streams"].insert(0, cover | {"disposition": {"attached_pic": 1}})

        self.assertEqual(parse_probe(data).width, 1920)

    def test_rejects_audio_with_only_cover_art(self):
        data = {
            "streams": [
                {"codec_type": "audio"},
                {
                    "codec_type": "video",
                    "width": 300,
                    "height": 300,
                    "disposition": {"attached_pic": 1},
                },
            ],
            "format": {"duration": "10"},
        }

        with self.assertRaises(TranscodeError):
            parse_probe(data)

    def test_falls_back_to_stream_duration(self):
        data = probe_data(duration="N/A")
        data["streams"][0]["duration"] = "7.5"

        self.assertEqual(parse_probe(data).duration, 7.5)

    def test_rejects_unusable_durations(self):
        for duration in ("N/A", "0", "-1", "nan", "inf", None):
            with self.subTest(duration), self.assertRaises(TranscodeError):
                parse_probe(probe_data(duration=duration))

    @override_settings(VIDEO_MAX_DURATION_SECONDS=10)
    def test_rejects_video_over_max_duration(self):
        with self.assertRaises(TranscodeError):
            parse_probe(probe_data(duration="10.5"))

    def test_rejects_extreme_resolutions(self):
        for width, height in ((8, 8), (MAX_DIMENSION + 2, 1080)):
            with self.subTest(f"{width}x{height}"), self.assertRaises(TranscodeError):
                parse_probe(probe_data(width=width, height=height))


class FfmpegCommandTests(SimpleTestCase):
    def command(self, media=MEDIA):
        renditions = plan_renditions(media.width, media.height)
        return build_ffmpeg_command(Path("/tmp/job/source"), media, renditions)

    def option(self, command, flag):
        return command[command.index(flag) + 1]

    def test_input_guards_come_before_the_input(self):
        command = self.command()
        input_index = command.index("-i")

        self.assertEqual(command[input_index - len(INPUT_GUARDS) : input_index], [*INPUT_GUARDS])
        self.assertNotIn("hls", self.option(command, "-format_whitelist"))
        self.assertEqual(self.option(command, "-protocol_whitelist"), "file")

    def test_scales_landscape_by_height(self):
        self.assertEqual(
            self.option(self.command(), "-filter_complex"),
            "[0:v:0]split=2[s0][s1];[s0]scale=-2:360[v0];[s1]scale=-2:480[v1]",
        )

    def test_scales_portrait_by_width(self):
        portrait = MediaInfo(width=480, height=854, duration=5, has_audio=True)

        graph = self.option(self.command(portrait), "-filter_complex")

        self.assertIn("scale=360:-2", graph)
        self.assertIn("scale=480:-2", graph)

    def test_maps_audio_into_each_rendition(self):
        command = self.command()

        self.assertEqual(command.count("0:a:0"), 2)
        self.assertEqual(
            self.option(command, "-var_stream_map"), "v:0,a:0,name:360p v:1,a:1,name:480p"
        )

    def test_handles_video_without_audio(self):
        command = self.command(MediaInfo(width=854, height=480, duration=5, has_audio=False))

        self.assertNotIn("0:a:0", command)
        self.assertNotIn("-c:a", command)
        self.assertEqual(self.option(command, "-var_stream_map"), "v:0,name:360p v:1,name:480p")

    def test_caps_bitrate_per_rendition(self):
        command = self.command()

        self.assertEqual(self.option(command, "-maxrate:v:0"), "800k")
        self.assertEqual(self.option(command, "-bufsize:v:0"), "1600k")
        self.assertEqual(self.option(command, "-maxrate:v:1"), "1467k")

    def test_aligns_keyframes_with_segments(self):
        command = self.command()

        self.assertEqual(self.option(command, "-hls_time"), "6")
        self.assertEqual(self.option(command, "-force_key_frames"), "expr:gte(t,n_forced*6)")
        self.assertEqual(self.option(command, "-sc_threshold"), "0")

    def test_strips_source_metadata(self):
        self.assertEqual(self.option(self.command(), "-map_metadata"), "-1")

    def test_reports_progress_on_stdout(self):
        command = self.command()

        self.assertEqual(self.option(command, "-progress"), "pipe:1")
        self.assertIn("-nostats", command)

    def test_writes_a_vod_master_playlist(self):
        command = self.command()

        self.assertEqual(self.option(command, "-hls_playlist_type"), "vod")
        self.assertEqual(self.option(command, "-master_pl_name"), "master.m3u8")
        self.assertEqual(command[-1], "%v/index.m3u8")


class ThumbnailCommandTests(SimpleTestCase):
    def command(self, at_seconds=1.5):
        return build_thumbnail_command(
            Path("/tmp/job/source"), Path("/tmp/job/thumbnail.webp"), at_seconds
        )

    def option(self, command, flag):
        return command[command.index(flag) + 1]

    def test_guards_and_seek_come_before_the_input(self):
        command = self.command()
        input_index = command.index("-i")

        self.assertLess(command.index("-protocol_whitelist"), input_index)
        self.assertLess(command.index("-format_whitelist"), input_index)
        self.assertLess(command.index("-ss"), input_index)
        self.assertEqual(self.option(command, "-ss"), "1.500")

    def test_fits_inside_1280_without_upscaling(self):
        video_filter = self.option(self.command(), "-vf")

        self.assertIn("min(1280,iw)", video_filter)
        self.assertIn("min(1280,ih)", video_filter)
        self.assertIn("force_original_aspect_ratio=decrease", video_filter)

    def test_writes_a_single_webp_frame(self):
        command = self.command()

        self.assertEqual(self.option(command, "-frames:v"), "1")
        self.assertEqual(self.option(command, "-c:v"), "libwebp")
        self.assertEqual(self.option(command, "-f"), "webp")
        self.assertEqual(Path(command[-1]).name, "thumbnail.webp")

    def test_skips_cover_art_and_strips_metadata(self):
        command = self.command()

        self.assertEqual(self.option(command, "-map"), "0:V:0")
        self.assertEqual(self.option(command, "-map_metadata"), "-1")


class ExtractThumbnailTests(SimpleTestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = self.tmp / "thumbnail" / "thumbnail.webp"
        patcher = mock.patch("videos.transcoding.run_ffmpeg")
        self.run_ffmpeg = patcher.start()
        self.addCleanup(patcher.stop)

    def write_frame(self, *_args, **_kwargs):
        self.target.write_bytes(b"webp")

    def seeks(self):
        return [
            call.args[0][call.args[0].index("-ss") + 1] for call in self.run_ffmpeg.call_args_list
        ]

    def test_seeks_to_ten_percent(self):
        self.run_ffmpeg.side_effect = self.write_frame

        self.assertTrue(extract_thumbnail(Path("source"), self.target, 40.0))
        self.assertEqual(self.seeks(), ["4.000"])

    def test_falls_back_to_the_start_when_seek_finds_no_frame(self):
        calls = iter([lambda: None, self.write_frame])
        self.run_ffmpeg.side_effect = lambda *_args, **_kwargs: next(calls)()

        self.assertTrue(extract_thumbnail(Path("source"), self.target, 0.5))
        self.assertEqual(self.seeks(), ["0.050", "0.000"])

    def test_falls_back_when_ffmpeg_fails(self):
        def fail_then_write(*_args, **_kwargs):
            if self.run_ffmpeg.call_count == 1:
                raise TranscodeError("no frame")
            self.write_frame()

        self.run_ffmpeg.side_effect = fail_then_write

        self.assertTrue(extract_thumbnail(Path("source"), self.target, 10.0))

    def test_empty_output_counts_as_no_frame(self):
        self.run_ffmpeg.side_effect = lambda *_args, **_kwargs: self.target.write_bytes(b"")

        self.assertFalse(extract_thumbnail(Path("source"), self.target, 10.0))
        self.assertEqual(self.run_ffmpeg.call_count, 2)

    def test_returns_false_when_every_attempt_fails(self):
        self.run_ffmpeg.side_effect = TranscodeError("broken")

        self.assertFalse(extract_thumbnail(Path("source"), self.target, 10.0))


class LimitsTests(SimpleTestCase):
    @override_settings(VIDEO_TRANSCODE_MAX_SECONDS=7200)
    def test_timeout_is_proportional_within_bounds(self):
        self.assertEqual(transcode_timeout(1), MIN_TRANSCODE_TIMEOUT)
        self.assertEqual(transcode_timeout(300), 3000)
        self.assertEqual(transcode_timeout(3600), 7200)

    def test_requires_free_disk_space(self):
        free = DISK_RESERVE_BYTES + 100
        with mock.patch("videos.transcoding.shutil.disk_usage") as usage:
            usage.return_value = DiskUsage(0, 0, free)

            require_free_space("/tmp", 100)
            with self.assertRaises(TranscodeError):
                require_free_space("/tmp", 101)


def fake_hls(_command, *, cwd, timeout, on_progress=None):
    for name in (
        "master.m3u8",
        "360p/index.m3u8",
        "360p/seg_00000.ts",
        "480p/index.m3u8",
        "480p/seg_00000.ts",
    ):
        path = cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")


def fake_thumbnail(_source, target, _duration):
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"webp")
    return True


@override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})
class TaskTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user(email="owner@example.com")

    def setUp(self):
        cache.clear()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        tmp_setting = override_settings(STREAMFORGE_TMP_DIR=str(self.tmp))
        tmp_setting.enable()
        self.addCleanup(tmp_setting.disable)
        self.s3 = self.patch("videos.tasks.s3_client").return_value
        self.patch("core.storage.time")  # Retries must not really wait.
        # A real close would break the transaction that wraps each test.
        self.patch("videos.tasks.close_old_connections")

    def patch(self, target, **kwargs):
        patcher = mock.patch(target, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def make_video(self, video_status=Status.UPLOADED, **extra):
        return Video.objects.create(
            owner=self.owner,
            title="Demo",
            status=video_status,
            size_bytes=1024,
            source_key="videos/demo/source",
            **extra,
        )

    def run_task(self, video, task_id="task-1"):
        result = transcode_video.apply(args=[str(video.id)], task_id=task_id).get()
        video.refresh_from_db()
        return result

    def workdir(self, video):
        return self.tmp / str(video.id)


class TranscodeTaskTests(TaskTestCase):
    def setUp(self):
        super().setUp()
        self.probe = self.patch("videos.tasks.probe", return_value=MEDIA)
        self.run_ffmpeg = self.patch("videos.tasks.run_ffmpeg", side_effect=fake_hls)
        self.free_space = self.patch("videos.tasks.require_free_space")
        self.thumbnail = self.patch("videos.tasks.extract_thumbnail", side_effect=fake_thumbnail)

    def uploaded_keys(self):
        keys = [call.args[2] for call in self.s3.upload_file.call_args_list]
        return [key for key in keys if "/hls/" in key]

    def test_marks_ready_with_metadata(self):
        video = self.make_video()

        self.run_task(video)

        self.assertEqual(video.status, Status.READY)
        self.assertEqual(video.progress, 100)
        self.assertEqual(video.hls_master_key, f"videos/{video.id}/hls/master.m3u8")
        self.assertEqual((video.width, video.height), (854, 480))
        self.assertEqual(video.duration_seconds, 12.5)
        self.assertIsNotNone(video.processed_at)

    def test_downloads_the_source(self):
        video = self.make_video()

        self.run_task(video)

        bucket, key, target = self.s3.download_file.call_args.args
        self.assertEqual(key, "videos/demo/source")
        self.assertEqual(Path(target), self.workdir(video) / "1" / "source")
        self.assertTrue(bucket)

    def test_uploads_every_file_with_the_master_last(self):
        video = self.make_video()

        self.run_task(video)

        prefix = f"videos/{video.id}/hls"
        keys = self.uploaded_keys()
        self.assertCountEqual(
            keys,
            [
                f"{prefix}/master.m3u8",
                f"{prefix}/360p/index.m3u8",
                f"{prefix}/360p/seg_00000.ts",
                f"{prefix}/480p/index.m3u8",
                f"{prefix}/480p/seg_00000.ts",
            ],
        )
        self.assertEqual(keys[-1], f"{prefix}/master.m3u8")
        first_playlist = next(i for i, key in enumerate(keys) if key.endswith(".m3u8"))
        self.assertTrue(all(key.endswith(".m3u8") for key in keys[first_playlist:]))

    def test_sets_hls_content_types(self):
        video = self.make_video()

        self.run_task(video)

        types = {
            Path(call.args[2]).suffix: call.kwargs["ExtraArgs"]["ContentType"]
            for call in self.s3.upload_file.call_args_list
        }
        self.assertEqual(
            types,
            {".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t", ".webp": "image/webp"},
        )

    def test_publishes_the_thumbnail(self):
        video = self.make_video()

        self.run_task(video)

        self.assertEqual(video.thumbnail_key, f"videos/{video.id}/thumbnail.webp")
        content_types = {
            call.args[2]: call.kwargs["ExtraArgs"]["ContentType"]
            for call in self.s3.upload_file.call_args_list
        }
        self.assertEqual(content_types[video.thumbnail_key], "image/webp")
        self.assertEqual(self.thumbnail.call_args.args[2], MEDIA.duration)

    def test_missing_thumbnail_does_not_fail_the_video(self):
        video = self.make_video()
        self.thumbnail.side_effect = None
        self.thumbnail.return_value = False

        with self.assertLogs("videos.tasks", level="WARNING"):
            self.run_task(video)

        self.assertEqual(video.status, Status.READY)
        self.assertEqual(video.thumbnail_key, "")

    def test_thumbnail_upload_failure_does_not_fail_the_video(self):
        video = self.make_video()

        def fail_on_thumbnail(_path, _bucket, key, **_kwargs):
            if key.endswith(".webp"):
                raise S3UploadFailedError("storage hiccup")

        self.s3.upload_file.side_effect = fail_on_thumbnail

        with self.assertLogs("videos.tasks", level="WARNING"):
            self.run_task(video)

        self.assertEqual(video.status, Status.READY)
        self.assertEqual(video.thumbnail_key, "")
        self.assertTrue(video.hls_master_key)

    def test_removes_temp_files_on_success(self):
        video = self.make_video()

        self.run_task(video)

        self.assertFalse(self.workdir(video).exists())

    def test_ffmpeg_failure_marks_failed_and_cleans_up(self):
        video = self.make_video()
        self.run_ffmpeg.side_effect = TranscodeError("FFmpeg could not process this file.")

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.assertEqual(video.error_message, "FFmpeg could not process this file.")
        self.assertFalse(self.workdir(video).exists())
        self.s3.upload_file.assert_not_called()

    def test_unexpected_error_does_not_leak_details(self):
        video = self.make_video()
        self.s3.download_file.side_effect = RuntimeError("secret internal path /etc/x")

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.assertNotIn("secret", video.error_message)
        self.assertFalse(self.workdir(video).exists())

    def test_low_disk_fails_before_downloading(self):
        video = self.make_video()
        self.free_space.side_effect = TranscodeError("Not enough temporary disk space.")

        with self.assertLogs("videos.tasks", level="ERROR"):
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.s3.download_file.assert_not_called()

    def test_ignores_videos_not_ready_for_processing(self):
        for video_status in (Status.PENDING_UPLOAD, Status.READY, Status.FAILED):
            with self.subTest(video_status):
                video = self.make_video(video_status=video_status)

                result = self.run_task(video)

                self.assertTrue(result["skipped"])
                self.assertEqual(video.status, video_status)
        self.s3.download_file.assert_not_called()

    def test_missing_video_is_skipped(self):
        result = transcode_video.apply(
            args=["00000000-0000-0000-0000-000000000000"], task_id="task-1"
        ).get()

        self.assertTrue(result["skipped"])

    def test_duplicate_task_leaves_the_running_one_alone(self):
        video = self.make_video(
            video_status=Status.PROCESSING, processing_task_id="other", processing_attempts=1
        )
        running_file = self.workdir(video) / "source"
        running_file.parent.mkdir(parents=True)
        running_file.write_text("in progress")

        result = self.run_task(video, task_id="task-1")

        self.assertTrue(result["skipped"])
        self.assertEqual(video.status, Status.PROCESSING)
        self.assertTrue(running_file.exists())
        self.s3.download_file.assert_not_called()

    def test_redelivered_task_resumes_after_worker_crash(self):
        video = self.make_video(
            video_status=Status.PROCESSING, processing_task_id="task-1", processing_attempts=1
        )
        leftover = self.workdir(video) / "hls" / "stale.ts"
        leftover.parent.mkdir(parents=True)
        leftover.write_text("partial")

        self.run_task(video, task_id="task-1")

        self.assertEqual(video.status, Status.READY)
        self.assertEqual(video.processing_attempts, 2)
        self.assertNotIn("stale.ts", " ".join(self.uploaded_keys()))

    def test_gives_up_after_repeated_crashes(self):
        video = self.make_video(
            video_status=Status.PROCESSING,
            processing_task_id="task-1",
            processing_attempts=Video.MAX_PROCESSING_ATTEMPTS,
        )
        self.workdir(video).mkdir(parents=True)

        result = self.run_task(video, task_id="task-1")

        self.assertTrue(result["skipped"])
        self.assertEqual(video.status, Status.FAILED)
        self.assertFalse(self.workdir(video).exists())
        self.s3.download_file.assert_not_called()

    def test_state_change_during_processing_is_respected(self):
        video = self.make_video()

        def another_actor_fails_it(_command, *, cwd, timeout, on_progress=None):
            fake_hls(_command, cwd=cwd, timeout=timeout)
            Video.objects.filter(pk=video.pk).update(status=Status.FAILED)

        self.run_ffmpeg.side_effect = another_actor_fails_it

        with self.assertLogs("videos.tasks", level="WARNING"):
            self.run_task(video)

        self.assertEqual(video.status, Status.FAILED)
        self.assertFalse(self.workdir(video).exists())


def image_info(path):
    output = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0"]
        + ["-show_entries", "stream=codec_name,width,height", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    codec, width, height = output.split(",")
    return codec, int(width), int(height)


def make_sample(path, *extra, size="854x480", audio=True, duration=7):
    command = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y"]
    command += ["-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24:duration={duration}"]
    if audio:
        command += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-c:a", "aac"]
    command += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(command, check=True)
    if extra:
        rotated = path.with_name(f"rotated-{path.name}")
        subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *extra]
            + ["-i", str(path), "-c", "copy", str(rotated)],
            check=True,
        )
        return rotated
    return path


@skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
class RealFfmpegTests(TaskTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.assets = Path(tempfile.mkdtemp())
        cls.addClassCleanup(shutil.rmtree, cls.assets, ignore_errors=True)
        cls.sample = make_sample(cls.assets / "sample.mp4")

    def setUp(self):
        super().setUp()
        self.published = self.tmp / "published"
        self.s3.upload_file.side_effect = self.publish

    def publish(self, path, _bucket, key, ExtraArgs):
        relative = key.split("/hls/")[1] if "/hls/" in key else Path(key).name
        target = self.published / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)

    def transcode(self, source):
        self.s3.download_file.side_effect = lambda _bucket, _key, target: shutil.copy(
            source, target
        )
        video = self.make_video()
        video.size_bytes = source.stat().st_size
        video.save()
        self.run_task(video)
        return video

    def master(self):
        return (self.published / "master.m3u8").read_text()

    def segment_durations(self, rendition):
        playlist = (self.published / rendition / "index.m3u8").read_text()
        return re.findall(r"#EXTINF:([\d.]+)", playlist)

    def test_480p_source_generates_360p_and_480p(self):
        video = self.transcode(self.sample)

        self.assertEqual(video.status, Status.READY, video.error_message)
        self.assertEqual((video.width, video.height), (854, 480))
        self.assertAlmostEqual(video.duration_seconds, 7, delta=0.5)
        master = self.master()
        self.assertIn("360p/index.m3u8", master)
        self.assertIn("480p/index.m3u8", master)
        self.assertNotIn("720p", master)
        self.assertIn("RESOLUTION=640x360", master)
        self.assertIn("RESOLUTION=854x480", master)
        self.assertIn("mp4a", master)

    def test_renditions_share_segment_boundaries(self):
        self.transcode(self.sample)

        durations = self.segment_durations("360p")
        self.assertGreater(len(durations), 1)
        self.assertEqual(durations, self.segment_durations("480p"))

    def test_generated_hls_decodes_without_errors(self):
        self.transcode(self.sample)

        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-xerror"]
            + ["-i", str(self.published / "master.m3u8"), "-map", "0", "-f", "null", "-"],
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_video_without_audio(self):
        source = make_sample(self.assets / "silent.mp4", size="640x360", audio=False)

        video = self.transcode(source)

        self.assertEqual(video.status, Status.READY, video.error_message)
        self.assertNotIn("mp4a", self.master())

    def test_rotated_phone_video_stays_portrait(self):
        source = make_sample(self.assets / "phone.mp4", "-display_rotation", "90")

        video = self.transcode(source)

        self.assertEqual(video.status, Status.READY, video.error_message)
        self.assertEqual((video.width, video.height), (480, 854))
        self.assertIn("RESOLUTION=360x640", self.master())

    def test_corrupt_file_fails_and_cleans_up(self):
        corrupt = self.assets / "corrupt.mp4"
        corrupt.write_bytes(b"\x00not a video\xff" * 512)

        with self.assertLogs("videos.tasks", level="ERROR"):
            video = self.transcode(corrupt)

        self.assertEqual(video.status, Status.FAILED)
        self.assertEqual(video.error_message, "The file is not a supported video.")
        self.assertFalse(self.workdir(video).exists())

    def test_full_run_publishes_a_webp_thumbnail(self):
        video = self.transcode(self.sample)

        self.assertEqual(video.thumbnail_key, f"videos/{video.id}/thumbnail.webp")
        self.assertEqual(image_info(self.published / "thumbnail.webp"), ("webp", 854, 480))

    def test_thumbnail_of_large_video_is_1280_wide(self):
        source = make_sample(self.assets / "hd.mp4", size="1920x1080", audio=False, duration=2)
        target = self.tmp / "out" / "thumbnail.webp"

        self.assertTrue(extract_thumbnail(source, target, 2))
        self.assertEqual(image_info(target), ("webp", 1280, 720))

    def test_thumbnail_of_small_video_is_not_upscaled(self):
        source = make_sample(self.assets / "small.mp4", size="640x360", audio=False, duration=2)
        target = self.tmp / "out" / "thumbnail.webp"

        self.assertTrue(extract_thumbnail(source, target, 2))
        self.assertEqual(image_info(target), ("webp", 640, 360))

    def test_thumbnail_of_rotated_video_stays_portrait(self):
        source = make_sample(
            self.assets / "tall.mp4",
            "-display_rotation",
            "90",
            size="1920x1080",
            audio=False,
            duration=2,
        )
        target = self.tmp / "out" / "thumbnail.webp"

        self.assertTrue(extract_thumbnail(source, target, 2))
        self.assertEqual(image_info(target), ("webp", 720, 1280))

    def test_videos_under_one_second_complete_with_thumbnail(self):
        for name, duration in (("short", 0.4), ("single-frame", 0.04)):
            with self.subTest(name):
                source = make_sample(self.assets / f"{name}.mp4", audio=False, duration=duration)

                video = self.transcode(source)

                self.assertEqual(video.status, Status.READY, video.error_message)
                self.assertEqual(video.thumbnail_key, f"videos/{video.id}/thumbnail.webp")
                self.assertEqual(image_info(self.published / "thumbnail.webp")[0], "webp")

    def test_progress_increases_during_a_real_run(self):
        seen = []
        original = ProgressReporter.transcoded

        def record(reporter, seconds, duration):
            seen.append(seconds)
            original(reporter, seconds, duration)

        with mock.patch.object(ProgressReporter, "transcoded", record):
            video = self.transcode(self.sample)

        self.assertEqual(video.status, Status.READY, video.error_message)
        self.assertEqual(video.progress, 100)
        self.assertTrue(seen)
        self.assertEqual(seen, sorted(seen))
        self.assertGreater(seen[-1], video.duration_seconds * 0.8)

    def test_rejects_playlists_that_could_read_local_files(self):
        attacks = {
            "hls": "#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:10.0,\nfile:///etc/passwd\n"
            "#EXT-X-ENDLIST\n",
            "concat": "ffconcat version 1.0\nfile /etc/passwd\n",
        }
        for name, content in attacks.items():
            with self.subTest(name):
                source = self.assets / f"attack-{name}"
                source.write_text(content)

                with self.assertRaises(TranscodeError):
                    probe(source)
