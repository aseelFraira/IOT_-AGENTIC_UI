"""Photographs the board's screen for the vision judge.

Settings (.env or environment):
    CAMERA_INDEX   OpenCV camera number. Required for visual checks; without it
                   they are skipped. Run camera_check.py to find the right one.
    CAMERA_CROP    optional x,y,w,h in camera pixels: keep only the screen.
    CAMERA_ROTATE  optional 0, 90, 180 or 270 (clockwise), applied after cropping.
    CAMERA_RESOLUTION  optional, default 1600x1200. A 4:3 mode sees more than a
                   16:9 one (the camera has no zoom control). CAMERA_CROP is in
                   pixels of this resolution, so re-check it after changing this.
    CAMERA_EXPOSURE  optional, default -6 (manual); lower is darker. "auto" for
                   the camera's automatic exposure, which overexposes screens.
    CAMERA_WB      optional white balance in kelvin, default 6500; or "auto".

Each capture takes a short burst and keeps the sharpest frame, which absorbs
autofocus hunting, exposure flicker and the display refresh.

A CameraError means the setup failed (no camera, no frames). It is never sent
to Gemini as a UI bug.
"""

import os
import time
from pathlib import Path

DEFAULT_RESOLUTION = "1600x1200"
DEFAULT_EXPOSURE = "-6"   # DirectShow log2 seconds (-6 = 1/64 s); tuned on the rig 2026-10-11
DEFAULT_WB = "6500"       # kelvin; LCD white is about 6500 K
BURST = 3
WARMUP_S = 2.0          # let auto exposure / focus settle after opening
STALE_FRAMES = 4        # OpenCV buffers frames; drop these before a burst
MAX_SIDE = 1024         # photos sent to Gemini are scaled down to this
JPEG_QUALITY = 88


class CameraError(Exception):
    pass


def settings_from_env():
    """Returns (index, crop, rotate) or None when CAMERA_INDEX is not set."""
    index = os.environ.get("CAMERA_INDEX", "").strip()
    if not index:
        return None
    crop = None
    raw = os.environ.get("CAMERA_CROP", "").strip()
    if raw:
        try:
            crop = tuple(int(v) for v in raw.split(","))
            assert len(crop) == 4 and crop[2] > 0 and crop[3] > 0
        except (ValueError, AssertionError):
            raise CameraError(f"CAMERA_CROP must be x,y,w,h in pixels, got {raw!r}") from None
    rotate = os.environ.get("CAMERA_ROTATE", "0").strip() or "0"
    rotate = int(rotate) if rotate.isdigit() else -1
    if rotate not in (0, 90, 180, 270):
        raise CameraError("CAMERA_ROTATE must be 0, 90, 180 or 270")
    try:
        return int(index), crop, rotate
    except ValueError:
        raise CameraError(f"CAMERA_INDEX must be a number, got {index!r}") from None


def resolution_from_env():
    """CAMERA_RESOLUTION as (width, height). 4:3 modes usually see more top to
    bottom than 16:9 ones, which crop the sensor."""
    raw = os.environ.get("CAMERA_RESOLUTION", "").strip().lower() or DEFAULT_RESOLUTION
    try:
        w, h = (int(v) for v in raw.split("x"))
        assert w > 0 and h > 0
    except (ValueError, AssertionError):
        raise CameraError(f"CAMERA_RESOLUTION must look like 1600x1200, got {raw!r}") from None
    return w, h


def lock_picture(cap):
    """Fixed exposure and white balance. Automatic exposure brightens a screen far
    too much (a near-black background photographs bright blue, blue turns white),
    and automatic white balance drifts between photos. "auto" keeps the camera's
    own setting."""
    import cv2

    exposure = os.environ.get("CAMERA_EXPOSURE", DEFAULT_EXPOSURE).strip().lower()
    if exposure != "auto":
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)  # 0.25 = manual on DirectShow/MSMF
        cap.set(cv2.CAP_PROP_EXPOSURE, float(exposure))
    white_balance = os.environ.get("CAMERA_WB", DEFAULT_WB).strip().lower()
    if white_balance != "auto":
        cap.set(cv2.CAP_PROP_AUTO_WB, 0)
        cap.set(cv2.CAP_PROP_WB_TEMPERATURE, float(white_balance))


def open_capture(index: int):
    """Opens a camera with the first Windows backend that delivers frames."""
    import cv2

    width, height = resolution_from_env()
    backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY] if os.name == "nt" else [cv2.CAP_ANY]
    for backend in backends:
        cap = cv2.VideoCapture(index, backend)
        if not cap.isOpened():
            cap.release()
            continue
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ok, _ = cap.read()
            if ok:
                lock_picture(cap)
                return cap
            time.sleep(0.1)
        cap.release()
    raise CameraError(f"Camera {index} gives no picture. Is it connected, and not in use by "
                      "another app (Zoom, Teams, the Camera app)?")


def sharpness(frame) -> float:
    import cv2

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def process(frame, crop=None, rotate=0):
    """Crop, rotate and scale a frame the way every saved photo is processed."""
    import cv2

    if crop:
        x, y, w, h = crop
        frame = frame[max(y, 0):y + h, max(x, 0):x + w]
        if frame.size == 0:
            raise CameraError("CAMERA_CROP is outside the camera picture")
    if rotate:
        code = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}[rotate]
        frame = cv2.rotate(frame, code)
    h, w = frame.shape[:2]
    scale = MAX_SIDE / max(h, w)
    if scale < 1:
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    return frame


class Camera:
    def __init__(self, index: int, crop=None, rotate: int = 0, log=print):
        self.index, self.crop, self.rotate, self.log = index, crop, rotate, log
        self.cap = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self):
        self.cap = open_capture(self.index)
        end = time.monotonic() + WARMUP_S
        while time.monotonic() < end:
            self.cap.read()

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None

    def capture(self, path: Path, raw: bool = False) -> Path:
        """Saves the sharpest frame of a burst as a JPEG and returns the path.
        raw skips crop/rotate/scaling, so pixel positions match CAMERA_CROP."""
        import cv2

        for _ in range(STALE_FRAMES):
            self.cap.read()
        frames = []
        for _ in range(BURST):
            ok, frame = self.cap.read()
            if ok:
                frames.append(frame)
        if not frames:
            raise CameraError(f"Camera {self.index} stopped delivering pictures")
        best = max(frames, key=sharpness)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), best if raw else process(best, self.crop, self.rotate),
                           [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]):
            raise CameraError(f"Could not save {path}")
        return path
