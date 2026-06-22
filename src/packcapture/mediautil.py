"""Small video I/O helpers shared by the render commands."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile


def to_h264(path: str) -> None:
    """Re-encode a finished render to H.264 in place.

    OpenCV's VideoWriter can only produce mp4v (MPEG-4 Part 2) reliably on
    Windows, which the stock players won't open. ffmpeg is a project
    dependency, so hand the file to it; if it's missing, leave the mp4v file
    and say so.
    """
    if shutil.which("ffmpeg") is None:
        print(f"note: ffmpeg not found — {path} is mp4v and may not play in stock players")
        return
    fd, tmp = tempfile.mkstemp(suffix=".mp4", dir=os.path.dirname(os.path.abspath(path)))
    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-c:v", "libx264", "-crf", "20",
             "-preset", "fast", "-pix_fmt", "yuv420p", tmp],
            check=True, capture_output=True, text=True,
        )
        os.replace(tmp, path)
    except subprocess.CalledProcessError as e:
        os.unlink(tmp)
        print(f"note: H.264 re-encode failed ({e.stderr[-200:]}); {path} left as mp4v")
