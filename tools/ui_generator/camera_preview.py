"""Live camera window for lining up the camera with the screen.

    python tools/ui_generator/camera_preview.py            # CAMERA_INDEX from .env
    python tools/ui_generator/camera_preview.py --index 1

Shows the full picture in the same mode and rotation the tests use
(CAMERA_RESOLUTION, CAMERA_ROTATE), with the current CAMERA_CROP drawn as a green
box. Aim for the whole display, including its dark border, inside the picture
with a small margin. Press q or Esc (or close the window) to quit; the camera is
then free for the tests again.
"""

import argparse
import os
import sys

import gemini_client  # noqa: F401  (loads .env)
import camera

WINDOW = "Camera preview - q to close"
MAX_WINDOW = 900  # px, longest side of the preview window


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--index", type=int, help="camera number (default: CAMERA_INDEX)")
    args = ap.parse_args()

    import cv2

    settings = camera.settings_from_env()
    index = args.index if args.index is not None else (settings[0] if settings else None)
    if index is None:
        sys.exit("Set CAMERA_INDEX in .env or pass --index N")
    crop, rotate = (settings[1], settings[2]) if settings else (None, 0)

    try:
        cap = camera.open_capture(index)
    except camera.CameraError as e:
        sys.exit(str(e))
    rotation = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}.get(rotate)

    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                continue
            h, w = frame.shape[:2]
            if crop:
                x, y, cw, ch = crop
                cv2.rectangle(frame, (x, y), (x + cw, y + ch), (0, 255, 0), max(2, w // 400))
            if rotation is not None:
                frame = cv2.rotate(frame, rotation)
            scale = MAX_WINDOW / max(frame.shape[:2])
            if scale < 1:
                frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            label = f"camera {index}  {w}x{h}  rotate {rotate}  sharpness {camera.sharpness(frame):.0f}"
            cv2.putText(frame, label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3)
            cv2.putText(frame, label, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.imshow(WINDOW, frame)
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
