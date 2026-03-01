#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yaml


DEFAULT_VIDEO = Path(r"C:/Users/18046/Desktop/master/masterthesis/rawdata/rawdata/44.mp4")
DEFAULT_SEGMENTS_JSON = Path("out_rawdata_fullcrack2/44/segments.json")
DEFAULT_CONFIG = Path("config.rawdata.fullcrack.yaml")


def parse_roi_text(value: str) -> list[int]:
    parts = [p.strip() for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError("ROI must be x,y,w,h")
    rect = [int(p) for p in parts]
    if rect[2] <= 0 or rect[3] <= 0:
        raise ValueError("ROI width/height must be > 0")
    return rect


def validate_rect4(value: object) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        rect = [int(v) for v in value]
    except Exception:
        return None
    if rect[2] <= 0 or rect[3] <= 0:
        return None
    return rect


def clip_roi(rect: list[int], width: int, height: int) -> list[int]:
    x, y, w, h = rect
    x1 = max(0, min(x, width - 1))
    y1 = max(0, min(y, height - 1))
    x2 = max(x1 + 1, min(width, x + w))
    y2 = max(y1 + 1, min(height, y + h))
    return [x1, y1, x2 - x1, y2 - y1]


def load_roi_from_segments(path: Path) -> list[int] | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_rect4(((data.get("roi") or {}).get("rect")))


def load_roi_from_config(path: Path, video_id: str, width: int, height: int) -> list[int]:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    roi_cfg = cfg.get("roi") or {}
    overrides = roi_cfg.get("overrides") or {}

    rect = validate_rect4(overrides.get(video_id))
    if rect is None:
        key = "rect_landscape" if width >= height else "rect_portrait"
        rect = validate_rect4(roi_cfg.get(key))
    if rect is None:
        rect = validate_rect4(roi_cfg.get("rect"))
    if rect is None:
        raise ValueError("No valid ROI found in config.")
    return rect


def parse_resize(resize_value: str) -> tuple[int, int]:
    text = resize_value.lower().strip().replace(" ", "")
    if "x" not in text:
        raise ValueError("resize must be like 640x640")
    w_str, h_str = text.split("x", 1)
    w = int(w_str)
    h = int(h_str)
    if w <= 0 or h <= 0:
        raise ValueError("resize width/height must be > 0")
    return w, h


def letterbox_resize(image: np.ndarray, target_wh: tuple[int, int], pad_value: int) -> np.ndarray:
    target_w, target_h = target_wh
    src_h, src_w = image.shape[:2]
    if src_w <= 0 or src_h <= 0:
        raise ValueError("Invalid source image size.")
    if src_w == target_w and src_h == target_h:
        return image

    scale = min(target_w / src_w, target_h / src_h)
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC)

    if image.ndim == 2:
        canvas = np.full((target_h, target_w), int(pad_value), dtype=image.dtype)
    else:
        channels = image.shape[2]
        canvas = np.full((target_h, target_w, channels), int(pad_value), dtype=image.dtype)
    left = (target_w - new_w) // 2
    top = (target_h - new_h) // 2
    canvas[top : top + new_h, left : left + new_w] = resized
    return canvas


def create_output_dir(custom_output: str | None) -> Path:
    if custom_output:
        out_dir = Path(custom_output)
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(f"out_video44_last3s_{stamp}")
    out_dir.mkdir(parents=True, exist_ok=False)
    return out_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract last N seconds frames from video 44 using the project's ROI and save as a_1, a_2..."
    )
    parser.add_argument("--video", type=str, default=str(DEFAULT_VIDEO), help="Target video path.")
    parser.add_argument("--seconds", type=float, default=3.0, help="Extract frames from the last N seconds.")
    parser.add_argument("--output-dir", type=str, default=None, help="Output folder. If omitted, create a new timestamp folder.")
    parser.add_argument("--segments-json", type=str, default=str(DEFAULT_SEGMENTS_JSON), help="ROI source (preferred): segments.json")
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG), help="Fallback ROI config file.")
    parser.add_argument("--roi", type=str, default=None, help="Manual ROI override: x,y,w,h")
    parser.add_argument("--prefix", type=str, default="a", help="Output file prefix.")
    parser.add_argument("--ext", type=str, default="jpg", choices=["jpg", "png"], help="Output image format.")
    parser.add_argument("--jpg-quality", type=int, default=95, help="JPG quality (1-100).")
    parser.add_argument(
        "--keep-size",
        action="store_true",
        help="Keep original ROI size. By default uses project-like resize (640x640 letterbox).",
    )
    parser.add_argument("--resize", type=str, default="640x640", help="Resize target when not using --keep-size.")
    parser.add_argument("--pad-value", type=int, default=114, help="Letterbox pad value.")
    args = parser.parse_args()

    video_path = Path(args.video)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    out_dir = create_output_dir(args.output_dir)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
        cap.release()
        raise RuntimeError(f"Invalid video metadata: fps={fps}, frame_count={frame_count}, width={width}, height={height}")

    video_id = video_path.stem
    if args.roi:
        roi_raw = parse_roi_text(args.roi)
        roi_source = "manual"
    else:
        roi_raw = load_roi_from_segments(Path(args.segments_json))
        if roi_raw is not None:
            roi_source = f"segments:{Path(args.segments_json)}"
        else:
            roi_raw = load_roi_from_config(Path(args.config), video_id=video_id, width=width, height=height)
            roi_source = f"config:{Path(args.config)}"
    roi = clip_roi(roi_raw, width, height)
    x, y, w, h = roi

    target_wh: tuple[int, int] | None = None
    if not args.keep_size:
        target_wh = parse_resize(args.resize)

    seconds = float(args.seconds)
    if seconds <= 0:
        cap.release()
        raise ValueError("--seconds must be > 0")

    frames_to_extract = max(1, int(np.ceil(seconds * fps)))
    start_idx = max(0, frame_count - frames_to_extract)
    cap.set(cv2.CAP_PROP_POS_FRAMES, float(start_idx))

    saved = 0
    for i in range(frames_to_extract):
        src_idx = start_idx + i
        if src_idx >= frame_count:
            break
        ok, frame = cap.read()
        if not ok or frame is None:
            break

        crop = frame[y : y + h, x : x + w]
        if crop.size == 0:
            continue
        if target_wh is not None:
            crop = letterbox_resize(crop, target_wh=target_wh, pad_value=args.pad_value)

        saved += 1
        out_name = f"{args.prefix}_{saved}.{args.ext}"
        out_path = out_dir / out_name
        if args.ext == "jpg":
            cv2.imwrite(str(out_path), crop, [cv2.IMWRITE_JPEG_QUALITY, int(np.clip(args.jpg_quality, 1, 100))])
        else:
            cv2.imwrite(str(out_path), crop)

    cap.release()

    summary = {
        "video_path": str(video_path),
        "fps": fps,
        "frame_count": frame_count,
        "duration_sec": frame_count / fps,
        "seconds_requested": seconds,
        "start_frame_idx": start_idx,
        "roi": roi,
        "roi_source": roi_source,
        "resize": None if target_wh is None else [target_wh[0], target_wh[1]],
        "output_dir": str(out_dir.resolve()),
        "saved_count": saved,
        "naming_example": f"{args.prefix}_1.{args.ext}",
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
