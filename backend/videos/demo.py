"""The sample video of the local demo: generated here, then processed by the real pipeline."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings

from core.storage import s3_client, with_retries
from videos.models import Video

SAMPLE_TITLE = "Muestra de StreamForge"
SAMPLE_DESCRIPTION = "Video de muestra generado en tu máquina con FFmpeg."
SAMPLE_SECONDS = 15
SAMPLE_TIMEOUT_SECONDS = 180


def build_sample_command(target):
    picture = (
        f"gradients=s=1920x1080:c0=0x170c10:c1=0x6e1a2b:c2=0xe8b45a:c3=0x2a0d15"
        f":n=4:speed=0.015:r=30:d={SAMPLE_SECONDS},noise=c0s=3:c0f=u,format=yuv420p"
    )
    tone = (
        "aevalsrc=0.07*sin(2*PI*220*t)+0.05*sin(2*PI*277.18*t)+0.05*sin(2*PI*329.63*t)"
        f":d={SAMPLE_SECONDS}:s=48000"
    )
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        picture,
        "-f",
        "lavfi",
        "-i",
        tone,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "24",
        "-c:a",
        "aac",
        "-shortest",
        "-movflags",
        "+faststart",
        str(target),
    ]


def publish_sample(owner):
    """Create the source file in storage and the row that sends it to processing."""
    video = Video(
        owner=owner,
        title=SAMPLE_TITLE,
        description=SAMPLE_DESCRIPTION,
        status=Video.Status.UPLOADED,
    )
    video.source_key = f"videos/{video.id}/source"
    Path(settings.STREAMFORGE_TMP_DIR).mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=settings.STREAMFORGE_TMP_DIR, prefix="demo-") as workdir:
        target = Path(workdir) / "sample.mp4"
        subprocess.run(
            build_sample_command(target),
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=SAMPLE_TIMEOUT_SECONDS,
        )
        s3 = s3_client()
        with_retries(
            lambda: s3.upload_file(
                str(target),
                settings.AWS_STORAGE_BUCKET_NAME,
                video.source_key,
                ExtraArgs={"ContentType": "video/mp4"},
            )
        )
        video.size_bytes = target.stat().st_size
    video.save()
    return video
