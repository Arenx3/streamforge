"""FFmpeg building blocks for adaptive HLS: rendition planning and subprocess wrappers."""

import json
import logging
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from itertools import pairwise

from django.conf import settings

logger = logging.getLogger(__name__)

LADDER = (360, 720, 1080)
# (short side in pixels, max video kbps); other sizes are interpolated.
BITRATE_POINTS = ((0, 0), (360, 800), (720, 2800), (1080, 5000))
AUDIO_KBPS = 128
SEGMENT_SECONDS = 6
MASTER_PLAYLIST = "master.m3u8"

THUMBNAIL_NAME = "thumbnail.webp"
THUMBNAIL_POSITION = 0.1
THUMBNAIL_TIMEOUT = 120
# Scales first so the frames `thumbnail` buffers to compare stay small on 4K sources.
THUMBNAIL_FILTER = (
    "scale='min(1280,iw)':'min(1280,ih)':force_original_aspect_ratio=decrease,thumbnail=n=30"
)

MIN_DIMENSION = 16
MAX_DIMENSION = 8192
PROBE_TIMEOUT = 60
MIN_TRANSCODE_TIMEOUT = 600
TRANSCODE_TIMEOUT_FACTOR = 10
DISK_RESERVE_BYTES = 1024**3

# Uploads are untrusted. Playlist-style demuxers (hls, concat) can read local files or
# reach internal hosts, so only real containers and the file protocol are allowed.
INPUT_GUARDS = (
    "-protocol_whitelist",
    "file",
    "-format_whitelist",
    "mov,matroska,avi,mpegts,asf,flv",
)


class TranscodeError(Exception):
    """A processing failure whose message is safe to show to the video's owner."""


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    duration: float
    has_audio: bool


@dataclass(frozen=True)
class Rendition:
    name: str
    short_side: int
    max_video_kbps: int


def probe(source):
    command = [
        "ffprobe",
        "-v",
        "error",
        *INPUT_GUARDS,
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(source),
    ]
    try:
        result = subprocess.run(
            command,
            env=_sandbox_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise TranscodeError("The file could not be analyzed.") from exc
    if result.returncode != 0:
        raise TranscodeError("The file is not a supported video.")
    return parse_probe(json.loads(result.stdout))


def parse_probe(data):
    streams = data.get("streams", [])
    video = next(
        (
            stream
            for stream in streams
            if stream.get("codec_type") == "video"
            and not stream.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise TranscodeError("The file has no video stream.")

    width, height = video.get("width", 0), video.get("height", 0)
    # Phones store portrait video as rotated landscape; FFmpeg applies the rotation.
    if _rotation(video) % 180 == 90:
        width, height = height, width
    if min(width, height) < MIN_DIMENSION or max(width, height) > MAX_DIMENSION:
        raise TranscodeError("The video resolution is not supported.")

    duration = _to_float(data.get("format", {}).get("duration")) or _to_float(video.get("duration"))
    if duration <= 0:
        raise TranscodeError("The video duration could not be determined.")
    if duration > settings.VIDEO_MAX_DURATION_SECONDS:
        raise TranscodeError("The video is longer than the allowed duration.")

    has_audio = any(stream.get("codec_type") == "audio" for stream in streams)
    return MediaInfo(width=width, height=height, duration=duration, has_audio=has_audio)


def max_video_kbps(short_side):
    for (low, low_kbps), (high, high_kbps) in pairwise(BITRATE_POINTS):
        if short_side <= high:
            return round(low_kbps + (short_side - low) * (high_kbps - low_kbps) / (high - low))
    return BITRATE_POINTS[-1][1]


def plan_renditions(width, height):
    """Ladder rungs up to the source, plus the source itself when it sits between rungs."""
    short_side = min(width, height)
    short_side -= short_side % 2  # H.264 with 4:2:0 chroma needs even dimensions.
    sizes = [rung for rung in LADDER if rung <= short_side]
    if short_side < LADDER[-1] and short_side not in sizes:
        sizes.append(short_side)
    return [Rendition(f"{size}p", size, max_video_kbps(size)) for size in sizes]


def build_ffmpeg_command(source, media, renditions):
    scale = "scale=-2:{}" if media.width >= media.height else "scale={}:-2"
    outputs = "".join(f"[s{index}]" for index in range(len(renditions)))
    chains = ";".join(
        f"[s{index}]{scale.format(rendition.short_side)}[v{index}]"
        for index, rendition in enumerate(renditions)
    )

    command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *INPUT_GUARDS,
        "-i",
        str(source),
        "-filter_complex",
        f"[0:v:0]split={len(renditions)}{outputs};{chains}",
    ]
    for index in range(len(renditions)):
        command += ["-map", f"[v{index}]"]
    if media.has_audio:
        command += ["-map", "0:a:0"] * len(renditions)

    # Metadata is dropped because phone videos embed the GPS location.
    command += [
        "-map_metadata",
        "-1",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-profile:v",
        "main",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        # Same keyframe times in every rendition, so players can switch between them.
        "-sc_threshold",
        "0",
        "-force_key_frames",
        f"expr:gte(t,n_forced*{SEGMENT_SECONDS})",
    ]
    for index, rendition in enumerate(renditions):
        command += [
            f"-maxrate:v:{index}",
            f"{rendition.max_video_kbps}k",
            f"-bufsize:v:{index}",
            f"{rendition.max_video_kbps * 2}k",
        ]
    if media.has_audio:
        command += ["-c:a", "aac", "-b:a", f"{AUDIO_KBPS}k", "-ac", "2", "-ar", "48000"]

    audio = ",a:{}" if media.has_audio else ""
    stream_map = " ".join(
        f"v:{index}{audio.format(index)},name:{rendition.name}"
        for index, rendition in enumerate(renditions)
    )
    command += [
        "-f",
        "hls",
        "-hls_time",
        str(SEGMENT_SECONDS),
        "-hls_playlist_type",
        "vod",
        "-hls_flags",
        "independent_segments",
        "-hls_segment_type",
        "mpegts",
        "-hls_segment_filename",
        "%v/seg_%05d.ts",
        "-master_pl_name",
        MASTER_PLAYLIST,
        "-var_stream_map",
        stream_map,
        "%v/index.m3u8",
    ]
    return command


def run_ffmpeg(command, *, cwd, timeout):
    log_path = cwd.parent / "ffmpeg.log"
    try:
        with log_path.open("wb") as log:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=_sandbox_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=log,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired as exc:
        raise TranscodeError("Processing took too long and was stopped.") from exc
    if result.returncode != 0:
        logger.error("FFmpeg exited with %s: %s", result.returncode, _tail(log_path))
        raise TranscodeError("FFmpeg could not process this file.")


def build_thumbnail_command(source, target, at_seconds):
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *INPUT_GUARDS,
        "-ss",
        f"{at_seconds:.3f}",
        "-i",
        str(source),
        "-map",
        "0:V:0",  # Capital V skips embedded cover art.
        "-map_metadata",
        "-1",
        "-vf",
        THUMBNAIL_FILTER,
        "-frames:v",
        "1",
        "-c:v",
        "libwebp",
        "-quality",
        "80",
        "-f",
        "webp",
        str(target),
    ]


def extract_thumbnail(source, target, duration):
    """Write a representative frame. Returns False if the video yields none."""
    target.parent.mkdir(parents=True, exist_ok=True)
    # Seeking can land past the only frame of a very short video, so retry from the start.
    for at_seconds in (duration * THUMBNAIL_POSITION, 0):
        target.unlink(missing_ok=True)
        try:
            run_ffmpeg(
                build_thumbnail_command(source, target, at_seconds),
                cwd=target.parent,
                timeout=THUMBNAIL_TIMEOUT,
            )
        except TranscodeError:
            continue
        if target.exists() and target.stat().st_size > 0:
            return True
    return False


def transcode_timeout(duration):
    return min(
        settings.VIDEO_TRANSCODE_MAX_SECONDS,
        max(MIN_TRANSCODE_TIMEOUT, duration * TRANSCODE_TIMEOUT_FACTOR),
    )


def estimated_output_bytes(renditions, duration):
    total_kbps = sum(rendition.max_video_kbps + AUDIO_KBPS for rendition in renditions)
    return int(total_kbps * 1000 / 8 * duration * 1.1)


def require_free_space(path, needed_bytes):
    if shutil.disk_usage(path).free < needed_bytes + DISK_RESERVE_BYTES:
        raise TranscodeError("Not enough temporary disk space to process this video.")


def _rotation(stream):
    for side_data in stream.get("side_data_list", []):
        if "rotation" in side_data:
            return round(_to_float(side_data["rotation"]))
    return round(_to_float(stream.get("tags", {}).get("rotate")))


def _to_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _sandbox_env():
    """FFmpeg parses untrusted files, so it never sees the app's secrets."""
    return {"PATH": os.environ.get("PATH", "")}


def _tail(path, size=2000):
    with path.open("rb") as log:
        log.seek(max(0, path.stat().st_size - size))
        return log.read().decode(errors="replace")
