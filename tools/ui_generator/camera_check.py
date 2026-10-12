"""Find the camera that sees the screen, and check the framing.

    python tools/ui_generator/camera_check.py            # snapshot from every camera
    python tools/ui_generator/camera_check.py --index 1  # snapshot with CAMERA_CROP / CAMERA_ROTATE applied

Snapshots go to tools/ui_generator/runs/camera_check/. Open them, then put the
right CAMERA_INDEX (and optionally CAMERA_CROP=x,y,w,h, CAMERA_ROTATE) in .env.
"""

import argparse
import sys
from pathlib import Path

import gemini_client  # noqa: F401  (loads .env)
import camera

OUT = Path(__file__).resolve().parent / "runs" / "camera_check"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--index", type=int, help="only this camera, with crop/rotation applied")
    ap.add_argument("--max", type=int, default=4, help="how many camera numbers to try (default 4)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    if args.index is not None:
        settings = camera.settings_from_env()
        crop, rotate = (settings[1], settings[2]) if settings else (None, 0)
        try:
            with camera.Camera(args.index, crop, rotate) as cam:
                path = cam.capture(OUT / f"camera_{args.index}_framed.jpg")
        except camera.CameraError as e:
            print(f"Camera {args.index}: {e}")
            sys.exit(1)
        print(f"Saved {path}  (crop={crop}, rotate={rotate})")
        return

    found = False
    for index in range(args.max):
        try:
            with camera.Camera(index) as cam:
                path = cam.capture(OUT / f"camera_{index}.jpg", raw=True)
                w = int(cam.cap.get(3))
                h = int(cam.cap.get(4))
        except camera.CameraError:
            print(f"Camera {index}: no picture")
            continue
        found = True
        print(f"Camera {index}: {w}x{h}, saved {path}")
    if not found:
        print("No camera gave a picture. Close Zoom/Teams/Camera app and try again.")
        sys.exit(1)


if __name__ == "__main__":
    main()
