import shutil
import subprocess

from celery import shared_task


@shared_task(name="core.ping")
def ping():
    """Infra smoke test: confirms the worker is consuming and FFmpeg is available."""
    ffmpeg = shutil.which("ffmpeg")
    version = None
    if ffmpeg:
        out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=10)
        version = out.stdout.splitlines()[0] if out.stdout else None
    return {"pong": True, "ffmpeg": version}
