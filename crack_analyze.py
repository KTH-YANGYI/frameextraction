#!/usr/bin/env python3
"""Crack video MVP pipeline (Windows-friendly, OpenCV backend).

MVP scope:
- method: ssim_ref (default) + diff_prev baseline
- ROI: fixed (with full-frame fallback)
- outputs: meta.json, score_curve.csv, segments.json, frames/crops/manifest, preview, index.html
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import logging
import math
import os
import platform
import re
import shutil
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "input_dir": "./videos",
    "output_dir": "./out",
    "roi": {
        "mode": "fixed",
        "rect": None,
        "rect_landscape": None,
        "rect_portrait": None,
        "rect_norm": None,
        "rect_norm_landscape": None,
        "rect_norm_portrait": None,
        "overrides": {},
        "auto": {
            "sample_sec": 2.0,
            "sample_frames": 15,
            "orb_nfeatures": 3000,
            "ratio_test": 0.75,
            "min_matches": 20,
            "min_inliers": 8,
            "ransac_thresh": 5.0,
            "refine": True,
            "search_ratio": 0.18,
            "search_steps": 7,
            "scales": [0.9, 1.0, 1.1],
            "post_shift_mode": "fixed",
            "post_shift_ratio": [0.0, 0.0],
            "post_shift_px": [0, 0],
            "post_shift_target_x": 0.48,
            "post_shift_min_ratio": 0.01,
            "post_shift_min_pixels": 120,
            "post_shift_max_abs_ratio": 0.35,
            "post_shift_direction": "left_only",
            "post_shift_fallback": "none",
        },
    },
    "segment_detection": {
        "method": "ssim_ref",
        "coarse_fps": 2.0,
        "resize_w": 320,
        "smooth_win": 5,
        "ssim_ref_n_ref": 5,
        "segment_strategy": "peak",
        "run_pre_pad_sec": 1.0,
        "run_post_pad_sec": 1.5,
        "run_max_len_sec": 20.0,
        "threshold": {
            "mode": "quantile",
            "q": 0.995,
            "mad_k": 6.0,
        },
        "topk": 3,
        "fill_to_topk": True,
        "segment_len_sec": 8.0,
        "merge_gap_sec": 0.5,
    },
    "frame_extraction": {
        "fine_fps": 10.0,
        "image_ext": "jpg",
        "jpg_quality_q": 2,
        "keep_full_frames": False,
        "crop_output": True,
        "naming_mode": "per_segment",
        "global_start_index": 0,
        "crop_resize": None,
        "crop_resize_mode": "letterbox",
        "crop_resize_pad_value": 114,
    },
    "report": {
        "enable_html": True,
        "contact_sheet_fps": 1.0,
        "tile": [4, 3],
    },
    "runtime": {
        "workers": 1,
        "fail_fast": False,
        "log_level": "INFO",
    },
}


class PipelineError(RuntimeError):
    """A controlled pipeline exception."""


def natural_key(text: str) -> list[Any]:
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", text)]


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_yaml(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def parse_roi_text(roi_text: str) -> list[int]:
    parts = [p.strip() for p in roi_text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("ROI must be x,y,w,h")
    try:
        vals = [int(float(p)) for p in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ROI must be integers") from exc
    if vals[2] <= 0 or vals[3] <= 0:
        raise argparse.ArgumentTypeError("ROI width/height must be > 0")
    return vals


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crack MVP pipeline")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--input_dir", type=Path, default=None)
    parser.add_argument("--output_dir", type=Path, default=None)
    parser.add_argument("--method", choices=["ssim_ref", "diff_prev", "flow"], default=None)
    parser.add_argument("--roi", type=parse_roi_text, default=None, help="Override fixed ROI x,y,w,h")
    parser.add_argument("--dry_run", action="store_true", help="Only produce score_curve + segments")
    parser.add_argument("--report_html", dest="report_html", action="store_true")
    parser.add_argument("--no-report_html", dest="report_html", action="store_false")
    parser.set_defaults(report_html=None)
    parser.add_argument("--fail_fast", action="store_true")
    parser.add_argument("--max_videos", type=int, default=None, help="Only process first N videos")
    return parser.parse_args()


def load_config(config_path: Path | None) -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if config_path is not None:
        if not config_path.exists():
            raise PipelineError(f"Config not found: {config_path}")
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise PipelineError("Config root must be a mapping")
        cfg = deep_merge(cfg, loaded)
    return cfg


def apply_cli_overrides(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    merged = deep_merge({}, cfg)
    if args.input_dir is not None:
        merged["input_dir"] = str(args.input_dir)
    if args.output_dir is not None:
        merged["output_dir"] = str(args.output_dir)
    if args.method is not None:
        merged["segment_detection"]["method"] = args.method
    if args.roi is not None:
        merged["roi"]["mode"] = "fixed"
        merged["roi"]["rect"] = args.roi
    if args.report_html is not None:
        merged["report"]["enable_html"] = bool(args.report_html)
    if args.fail_fast:
        merged["runtime"]["fail_fast"] = True
    merged["runtime"]["dry_run"] = bool(args.dry_run)
    if args.max_videos is not None:
        merged["runtime"]["max_videos"] = args.max_videos
    return merged


def setup_logging(run_dir: Path, level: str) -> logging.Logger:
    ensure_dir(run_dir)
    logger = logging.getLogger("crack_pipeline")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def check_tool(name: str) -> str | None:
    return shutil.which(name)


def write_versions(run_dir: Path) -> None:
    data = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version,
        "platform": platform.platform(),
        "opencv": cv2.__version__,
        "numpy": np.__version__,
        "yaml": yaml.__version__,
        "ffmpeg_path": check_tool("ffmpeg"),
        "ffprobe_path": check_tool("ffprobe"),
    }
    write_json(run_dir / "versions.json", data)


def list_videos(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        raise PipelineError(f"Input directory not found: {input_dir}")
    videos = [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"]
    videos.sort(key=lambda p: natural_key(p.name))
    return videos


def fourcc_to_str(fourcc_int: int) -> str:
    chars = [chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)]
    return "".join(chars).strip("\x00")


def probe_video(video_path: Path) -> dict[str, Any]:
    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise PipelineError("VideoCapture open failed")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fourcc_raw = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
    finally:
        cap.release()

    if width <= 0 or height <= 0:
        raise PipelineError("Invalid video dimensions")
    if fps <= 0:
        raise PipelineError("Invalid fps (<=0)")
    if frame_count <= 0:
        raise PipelineError("Invalid frame_count (<=0)")

    duration_sec = frame_count / fps
    if duration_sec <= 0:
        raise PipelineError("Invalid duration (<=0)")

    return {
        "video_path": str(video_path),
        "fps": fps,
        "width": width,
        "height": height,
        "frame_count": frame_count,
        "duration_sec": duration_sec,
        "codec_fourcc": fourcc_to_str(fourcc_raw),
    }


def orientation_label(width: int, height: int) -> str:
    return "portrait" if int(height) > int(width) else "landscape"


def read_representative_frame(video_path: Path, sample_sec: float, sample_frames: int) -> np.ndarray:
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise PipelineError(f"VideoCapture open failed: {video_path}")

    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if fps <= 0 or frame_count <= 0:
            raise PipelineError(f"Invalid video metadata for representative frame: {video_path}")

        max_frame_idx = min(frame_count - 1, int(max(0.2, sample_sec) * fps))
        n = max(1, min(int(sample_frames), max_frame_idx + 1))
        sample_idx = np.linspace(0, max_frame_idx, num=n, endpoint=True)
        sample_idx = np.unique(np.rint(sample_idx).astype(np.int64))

        frames: list[np.ndarray] = []
        for idx in sample_idx.tolist():
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            ok, frame = cap.read()
            if ok and frame is not None:
                frames.append(frame)

        if not frames:
            raise PipelineError(f"Cannot read representative frame: {video_path}")
        if len(frames) == 1:
            return frames[0]

        median = np.median(np.stack(frames, axis=0), axis=0)
        return np.clip(median, 0, 255).astype(np.uint8)
    finally:
        cap.release()


def to_rect4(values: Any) -> list[float] | None:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return None
    try:
        rect = [float(v) for v in values]
    except (TypeError, ValueError):
        return None
    if rect[2] <= 0 or rect[3] <= 0:
        return None
    return rect


def to_vec2(values: Any) -> tuple[float, float] | None:
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        return None
    try:
        x = float(values[0])
        y = float(values[1])
    except (TypeError, ValueError):
        return None
    return x, y


def copper_mask_stats(crop: np.ndarray) -> dict[str, Any]:
    if crop.size == 0:
        return {"ratio": 0.0, "pixels": 0, "cx": None, "cy": None}
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array([5, 50, 40], dtype=np.uint8),
        np.array([35, 255, 255], dtype=np.uint8),
    )
    pixels = int(np.count_nonzero(mask))
    ratio = float(np.mean(mask > 0))
    if pixels < 30:
        return {"ratio": ratio, "pixels": pixels, "cx": None, "cy": None}
    ys, xs = np.where(mask > 0)
    cx = float(np.mean(xs) / max(1.0, crop.shape[1] - 1.0))
    cy = float(np.mean(ys) / max(1.0, crop.shape[0] - 1.0))
    return {"ratio": ratio, "pixels": pixels, "cx": cx, "cy": cy}


def roi_from_norm(rect_norm: list[float], width: int, height: int) -> list[int]:
    x = int(round(rect_norm[0] * width))
    y = int(round(rect_norm[1] * height))
    w = int(round(rect_norm[2] * width))
    h = int(round(rect_norm[3] * height))
    return [x, y, max(1, w), max(1, h)]


def resolve_roi_rect(
    roi_cfg: dict[str, Any],
    width: int,
    height: int,
) -> tuple[list[int] | None, str]:
    orientation = "portrait" if height > width else "landscape"

    key_order = [
        f"rect_{orientation}",
        "rect",
        f"rect_norm_{orientation}",
        "rect_norm",
    ]
    for key in key_order:
        parsed = to_rect4(roi_cfg.get(key))
        if parsed is None:
            continue
        if key.startswith("rect_norm"):
            return roi_from_norm(parsed, width, height), key
        return [int(round(v)) for v in parsed], key
    return None, "fallback_full_frame"


def clip_roi(
    roi_mode: str,
    roi_rect: list[int] | None,
    width: int,
    height: int,
    logger: logging.Logger,
    video_id: str,
) -> list[int]:
    if roi_mode != "fixed":
        raise PipelineError(f"MVP only supports roi.mode=fixed, got: {roi_mode}")

    if roi_rect is None:
        logger.warning("[%s] roi rect missing, fallback to full frame ROI", video_id)
        return [0, 0, width, height]

    x, y, w, h = [int(v) for v in roi_rect]
    x0 = max(0, min(x, width - 1))
    y0 = max(0, min(y, height - 1))
    x1 = max(x0 + 1, min(x + w, width))
    y1 = max(y0 + 1, min(y + h, height))
    clipped = [x0, y0, x1 - x0, y1 - y0]
    if clipped != [x, y, w, h]:
        logger.warning(
            "[%s] roi out of bounds, clipped from %s to %s",
            video_id,
            [x, y, w, h],
            clipped,
        )
    return clipped


def clip_rect_silent(rect: list[int], width: int, height: int) -> list[int]:
    x, y, w, h = [int(v) for v in rect]
    x0 = max(0, min(x, width - 1))
    y0 = max(0, min(y, height - 1))
    x1 = max(x0 + 1, min(x + w, width))
    y1 = max(y0 + 1, min(y + h, height))
    return [x0, y0, x1 - x0, y1 - y0]


def shift_rect_keep_size(rect: list[int], dx: int, dy: int, width: int, height: int) -> list[int]:
    x, y, w, h = [int(v) for v in rect]
    w = max(1, min(int(w), int(width)))
    h = max(1, min(int(h), int(height)))
    max_x = max(0, int(width) - w)
    max_y = max(0, int(height) - h)
    nx = min(max(0, int(x) + int(dx)), max_x)
    ny = min(max(0, int(y) + int(dy)), max_y)
    return [nx, ny, w, h]


def resolve_post_shift(
    frame: np.ndarray,
    rect: list[int],
    auto_cfg: dict[str, Any],
) -> tuple[int, int, dict[str, Any]]:
    shift_mode = str(auto_cfg.get("post_shift_mode", "fixed")).strip().lower()
    shift_ratio = to_vec2(auto_cfg.get("post_shift_ratio")) or (0.0, 0.0)
    shift_px = to_vec2(auto_cfg.get("post_shift_px")) or (0.0, 0.0)
    w = max(1, int(rect[2]))
    h = max(1, int(rect[3]))

    dy = int(round(float(h) * float(shift_ratio[1]) + float(shift_px[1])))
    if shift_mode in ("none", "off", "disabled", ""):
        return 0, dy, {"mode": "none"}

    if shift_mode == "fixed":
        dx_fixed = int(round(float(w) * float(shift_ratio[0]) + float(shift_px[0])))
        return (
            dx_fixed,
            dy,
            {
                "mode": "fixed",
                "ratio": [float(shift_ratio[0]), float(shift_ratio[1])],
                "px": [int(round(shift_px[0])), int(round(shift_px[1]))],
            },
        )

    if shift_mode not in ("adaptive", "adaptive_copper"):
        raise PipelineError("roi.auto.post_shift_mode must be one of: fixed|adaptive_copper|none")

    x, y = int(rect[0]), int(rect[1])
    crop = frame[y : y + h, x : x + w]
    stats = copper_mask_stats(crop)
    target_x = float(auto_cfg.get("post_shift_target_x", 0.48))
    min_ratio = float(auto_cfg.get("post_shift_min_ratio", 0.01))
    min_pixels = max(1, int(auto_cfg.get("post_shift_min_pixels", 120)))
    max_abs_ratio = max(0.0, float(auto_cfg.get("post_shift_max_abs_ratio", 0.35)))
    max_abs_px = int(round(max_abs_ratio * w))
    direction = str(auto_cfg.get("post_shift_direction", "left_only")).strip().lower()
    fallback = str(auto_cfg.get("post_shift_fallback", "none")).strip().lower()

    adaptive_ok = (
        stats.get("cx") is not None
        and float(stats.get("ratio", 0.0)) >= min_ratio
        and int(stats.get("pixels", 0)) >= min_pixels
    )
    if adaptive_ok:
        # If copper appears too far left in crop (cx < target), move ROI left (negative dx).
        raw_dx = int(round((float(stats["cx"]) - target_x) * w))
        if direction == "left_only":
            raw_dx = min(0, raw_dx)
        elif direction == "right_only":
            raw_dx = max(0, raw_dx)
        elif direction not in ("both", "any"):
            raise PipelineError("roi.auto.post_shift_direction must be one of: left_only|right_only|both")
        dx = int(np.clip(raw_dx, -max_abs_px, max_abs_px))
    elif fallback == "fixed":
        dx = int(round(float(w) * float(shift_ratio[0]) + float(shift_px[0])))
    else:
        dx = int(round(float(shift_px[0])))

    return (
        dx,
        dy,
        {
            "mode": "adaptive_copper",
            "adaptive_ok": bool(adaptive_ok),
            "target_x": float(target_x),
            "min_ratio": float(min_ratio),
            "min_pixels": int(min_pixels),
            "max_abs_ratio": float(max_abs_ratio),
            "direction": direction,
            "fallback": fallback,
            "copper_ratio": float(stats.get("ratio", 0.0)),
            "copper_pixels": int(stats.get("pixels", 0)),
            "copper_cx": None if stats.get("cx") is None else float(stats["cx"]),
            "copper_cy": None if stats.get("cy") is None else float(stats["cy"]),
            "ratio": [float(shift_ratio[0]), float(shift_ratio[1])],
            "px": [int(round(shift_px[0])), int(round(shift_px[1]))],
        },
    )


def transform_rect_with_affine(rect: list[int], matrix: np.ndarray) -> list[int]:
    x, y, w, h = [float(v) for v in rect]
    corners = np.array(
        [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]]],
        dtype=np.float32,
    )
    transformed = cv2.transform(corners, matrix)[0]
    x0 = int(round(float(np.min(transformed[:, 0]))))
    y0 = int(round(float(np.min(transformed[:, 1]))))
    x1 = int(round(float(np.max(transformed[:, 0]))))
    y1 = int(round(float(np.max(transformed[:, 1]))))
    return [x0, y0, max(1, x1 - x0), max(1, y1 - y0)]


def roi_candidate_score(frame: np.ndarray, rect: list[int]) -> float:
    h, w = frame.shape[:2]
    x, y, rw, rh = clip_rect_silent(rect, w, h)
    crop = frame[y : y + rh, x : x + rw]
    if crop.size == 0:
        return -1e9

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    edge_mean = float(np.mean(np.abs(lap)) / 255.0)

    green_mask = cv2.inRange(
        hsv,
        np.array([35, 35, 35], dtype=np.uint8),
        np.array([95, 255, 255], dtype=np.uint8),
    )
    copper_mask = cv2.inRange(
        hsv,
        np.array([5, 50, 40], dtype=np.uint8),
        np.array([35, 255, 255], dtype=np.uint8),
    )
    metal_mask = cv2.inRange(
        hsv,
        np.array([0, 0, 35], dtype=np.uint8),
        np.array([180, 70, 230], dtype=np.uint8),
    )
    green_ratio = float(np.mean(green_mask > 0))
    copper_ratio = float(np.mean(copper_mask > 0))
    metal_ratio = float(np.mean(metal_mask > 0))
    copper_excess = max(0.0, copper_ratio - 0.20)

    score = (
        1.25 * edge_mean
        + 0.95 * metal_ratio
        + 0.40 * (1.0 - green_ratio)
        + 0.25 * copper_ratio
        - 1.40 * copper_excess
    )
    copper_pixels = np.argwhere(copper_mask > 0)
    if len(copper_pixels) > 30:
        cy, cx = np.mean(copper_pixels, axis=0)
        cx = float(cx) / max(1.0, crop.shape[1] - 1.0)
        cy = float(cy) / max(1.0, crop.shape[0] - 1.0)
        dist = math.sqrt((cx - 0.5) ** 2 + (cy - 0.5) ** 2)
        score += 0.22 * (1.0 - min(1.0, dist * 2.4))
    return score


def refine_roi_rect(frame: np.ndarray, init_rect: list[int], auto_cfg: dict[str, Any]) -> list[int]:
    h, w = frame.shape[:2]
    base = clip_rect_silent(init_rect, w, h)
    base_x, base_y, base_w, base_h = base
    cx0 = base_x + base_w / 2.0
    cy0 = base_y + base_h / 2.0

    search_ratio = float(auto_cfg.get("search_ratio", 0.18))
    steps = max(3, int(auto_cfg.get("search_steps", 7)))
    scales_raw = auto_cfg.get("scales", [0.9, 1.0, 1.1])
    scales: list[float] = []
    if isinstance(scales_raw, (list, tuple)):
        for s in scales_raw:
            try:
                scales.append(float(s))
            except (TypeError, ValueError):
                continue
    if not scales:
        scales = [1.0]

    dx_range = np.linspace(-search_ratio * base_w, search_ratio * base_w, num=steps)
    dy_range = np.linspace(-search_ratio * base_h, search_ratio * base_h, num=steps)

    best_rect = base
    best_score = roi_candidate_score(frame, base)
    for scale in scales:
        test_w = max(48, int(round(base_w * scale)))
        test_h = max(48, int(round(base_h * scale)))
        for dx in dx_range.tolist():
            for dy in dy_range.tolist():
                cx = cx0 + float(dx)
                cy = cy0 + float(dy)
                rect = [
                    int(round(cx - test_w / 2.0)),
                    int(round(cy - test_h / 2.0)),
                    test_w,
                    test_h,
                ]
                clipped = clip_rect_silent(rect, w, h)
                score = roi_candidate_score(frame, clipped)
                if score > best_score:
                    best_score = score
                    best_rect = clipped

    return best_rect


def choose_reference_videos(videos: list[Path], logger: logging.Logger) -> dict[str, Path]:
    refs: dict[str, Path] = {}
    for path in videos:
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            cap.release()
            continue
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        cap.release()
        if width <= 0 or height <= 0:
            continue
        ori = orientation_label(width, height)
        if ori not in refs:
            refs[ori] = path
        if "landscape" in refs and "portrait" in refs:
            break

    if "landscape" not in refs and videos:
        refs["landscape"] = videos[0]
    if "portrait" not in refs:
        logger.warning("no portrait reference video found; portrait clips will fallback to fixed ROI")
    return refs


def build_auto_roi_context(videos: list[Path], cfg: dict[str, Any], logger: logging.Logger) -> dict[str, Any]:
    roi_cfg = cfg.get("roi", {}) or {}
    mode = str(roi_cfg.get("mode", "fixed")).lower()
    context: dict[str, Any] = {"mode": mode, "refs": {"landscape": [], "portrait": []}, "auto_cfg": roi_cfg.get("auto", {}) or {}}
    if mode != "auto_per_video":
        return context

    auto_cfg = context["auto_cfg"]
    sample_sec = float(auto_cfg.get("sample_sec", 2.0))
    sample_frames = int(auto_cfg.get("sample_frames", 15))
    orb_nfeatures = max(500, int(auto_cfg.get("orb_nfeatures", 3000)))
    max_refs = max(2, int(auto_cfg.get("max_refs", 10)))

    ref_videos = choose_reference_videos(videos, logger)
    orb = cv2.ORB_create(nfeatures=orb_nfeatures)
    for ori, video_path in ref_videos.items():
        try:
            meta = probe_video(video_path)
            ref_frame = read_representative_frame(video_path, sample_sec, sample_frames)
            gray = cv2.cvtColor(ref_frame, cv2.COLOR_BGR2GRAY)
            kp, des = orb.detectAndCompute(gray, None)
            raw_rect, source = resolve_roi_rect(roi_cfg, int(meta["width"]), int(meta["height"]))
            base_rect = clip_roi(
                roi_mode="fixed",
                roi_rect=raw_rect,
                width=int(meta["width"]),
                height=int(meta["height"]),
                logger=logger,
                video_id=f"ref_{ori}",
            )
            context["refs"][ori].append(
                {
                "video_path": str(video_path),
                "kp": kp,
                "des": des,
                "roi_rect": base_rect,
                "source": source,
                "shape": [int(meta["height"]), int(meta["width"])],
                }
            )
            if len(context["refs"][ori]) > max_refs:
                context["refs"][ori] = context["refs"][ori][-max_refs:]
            logger.info(
                "auto ROI reference[%s]: video=%s base_rect=%s keypoints=%d",
                ori,
                video_path.name,
                base_rect,
                0 if kp is None else len(kp),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto ROI reference init failed for %s (%s): %s", ori, video_path.name, exc)
    return context


def update_auto_roi_context(
    roi_ctx: dict[str, Any],
    orientation: str,
    video_path: Path,
    roi_rect: list[int],
    kp: Any,
    des: Any,
    roi_score: float,
    logger: logging.Logger,
) -> None:
    if roi_ctx.get("mode") != "auto_per_video":
        return
    auto_cfg = roi_ctx.get("auto_cfg", {}) or {}
    min_ref_score = float(auto_cfg.get("min_ref_score", 0.45))
    max_refs = max(2, int(auto_cfg.get("max_refs", 10)))
    if des is None or kp is None:
        return
    if len(kp) < 80:
        return
    if roi_score < min_ref_score:
        return

    refs = (roi_ctx.get("refs", {}) or {}).setdefault(orientation, [])
    refs.append(
        {
            "video_path": str(video_path),
            "kp": kp,
            "des": des,
            "roi_rect": [int(v) for v in roi_rect],
            "source": "online_update",
            "shape": None,
        }
    )
    if len(refs) > max_refs:
        del refs[0 : len(refs) - max_refs]
    logger.debug(
        "auto ROI reference update[%s]: pool=%d video=%s score=%.3f",
        orientation,
        len(refs),
        video_path.name,
        roi_score,
    )


def resolve_video_roi(
    video_path: Path,
    meta: dict[str, Any],
    cfg: dict[str, Any],
    roi_ctx: dict[str, Any],
    logger: logging.Logger,
    video_id: str,
) -> tuple[list[int], str, dict[str, Any], dict[str, Any] | None]:
    roi_cfg = cfg.get("roi", {}) or {}
    roi_mode = str(roi_cfg.get("mode", "fixed")).lower()
    width = int(meta["width"])
    height = int(meta["height"])
    ori = orientation_label(width, height)

    overrides = roi_cfg.get("overrides", {}) or {}
    override_val = None
    if isinstance(overrides, dict):
        override_val = overrides.get(str(video_id))
    override_rect = None
    if override_val is not None:
        parsed = to_rect4(override_val)
        if parsed is not None:
            override_rect = [int(round(v)) for v in parsed]
    if override_rect is not None:
        rect = clip_roi(
            roi_mode="fixed",
            roi_rect=override_rect,
            width=width,
            height=height,
            logger=logger,
            video_id=video_id,
        )
        details = {"orientation": ori, "method": "override"}
        return rect, "manual_override", details, None

    # Fixed ROI path (existing behavior).
    if roi_mode != "auto_per_video":
        raw_rect, roi_source = resolve_roi_rect(roi_cfg=roi_cfg, width=width, height=height)
        rect = clip_roi(
            roi_mode="fixed",
            roi_rect=raw_rect,
            width=width,
            height=height,
            logger=logger,
            video_id=video_id,
        )
        details = {"orientation": ori, "method": "fixed"}
        return rect, roi_source, details, None

    # Auto per-video path.
    auto_cfg = roi_ctx.get("auto_cfg", {}) or {}
    sample_sec = float(auto_cfg.get("sample_sec", 2.0))
    sample_frames = int(auto_cfg.get("sample_frames", 15))
    ratio_test = float(auto_cfg.get("ratio_test", 0.75))
    min_matches = int(auto_cfg.get("min_matches", 20))
    min_inliers = int(auto_cfg.get("min_inliers", 12))
    ransac_thresh = float(auto_cfg.get("ransac_thresh", 5.0))
    orb_nfeatures = max(500, int(auto_cfg.get("orb_nfeatures", 3000)))
    do_refine = bool(auto_cfg.get("refine", True))

    frame = read_representative_frame(video_path, sample_sec=sample_sec, sample_frames=sample_frames)
    raw_rect, raw_source = resolve_roi_rect(roi_cfg=roi_cfg, width=width, height=height)
    fallback_rect = clip_roi(
        roi_mode="fixed",
        roi_rect=raw_rect,
        width=width,
        height=height,
        logger=logger,
        video_id=video_id,
    )

    source = f"auto_per_video:fallback:{raw_source}"
    rect = fallback_rect
    details: dict[str, Any] = {
        "orientation": ori,
        "fallback_source": raw_source,
        "match_count": 0,
        "inlier_count": 0,
        "reference_video": None,
        "reference_pool_size": 0,
    }
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(nfeatures=orb_nfeatures)
    kp, des = orb.detectAndCompute(gray, None)
    refs = list(((roi_ctx.get("refs", {}) or {}).get(ori) or []))
    details["reference_pool_size"] = len(refs)

    best: dict[str, Any] | None = None
    if des is not None and kp is not None and len(kp) > 0 and refs:
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        for ref in refs:
            ref_kp = ref.get("kp")
            ref_des = ref.get("des")
            ref_rect = ref.get("roi_rect")
            if ref_kp is None or ref_des is None or ref_rect is None:
                continue
            knn = bf.knnMatch(ref_des, des, k=2)
            good = []
            for pair in knn:
                if len(pair) < 2:
                    continue
                m, n = pair
                if m.distance < ratio_test * n.distance:
                    good.append(m)
            if len(good) < min_matches:
                continue

            src_pts = np.float32([ref_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst_pts = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            matrix, inliers = cv2.estimateAffinePartial2D(
                src_pts,
                dst_pts,
                method=cv2.RANSAC,
                ransacReprojThreshold=ransac_thresh,
            )
            if matrix is None:
                continue
            inlier_count = int(np.sum(inliers)) if inliers is not None else len(good)
            candidate = {
                "match_count": len(good),
                "inlier_count": inlier_count,
                "matrix": matrix,
                "reference_video": ref.get("video_path"),
                "reference_rect": [int(v) for v in ref_rect],
            }
            if best is None:
                best = candidate
            else:
                lhs = (candidate["inlier_count"], candidate["match_count"])
                rhs = (best["inlier_count"], best["match_count"])
                if lhs > rhs:
                    best = candidate
    elif not refs:
        logger.warning("[%s] auto ROI reference unavailable for %s, fallback used", video_id, ori)
    else:
        logger.warning("[%s] auto ROI descriptor extraction failed, fallback used", video_id)

    if best is not None:
        details["match_count"] = int(best["match_count"])
        details["inlier_count"] = int(best["inlier_count"])
        details["reference_video"] = best["reference_video"]
        if int(best["inlier_count"]) >= min_inliers:
            mapped = transform_rect_with_affine(best["reference_rect"], best["matrix"])
            rect = clip_rect_silent(mapped, width, height)
            ref_id = Path(str(best["reference_video"])).stem if best["reference_video"] else "ref"
            source = f"auto_per_video:orb:{ori}:{ref_id}"
        else:
            logger.warning(
                "[%s] auto ROI low inliers (%d), fallback used",
                video_id,
                int(best["inlier_count"]),
            )
    else:
        logger.warning("[%s] auto ROI no valid match, fallback used", video_id)

    if do_refine:
        refined = refine_roi_rect(frame, rect, auto_cfg)
        rect = clip_rect_silent(refined, width, height)
        source = f"{source}+refine"

    # Optional post-shift to compensate systematic composition bias.
    req_dx, req_dy, shift_details = resolve_post_shift(frame, rect, auto_cfg)
    if req_dx != 0 or req_dy != 0:
        prev_rect = [int(v) for v in rect]
        rect = shift_rect_keep_size(rect, req_dx, req_dy, width, height)
        applied_dx = int(rect[0] - prev_rect[0])
        applied_dy = int(rect[1] - prev_rect[1])
        source = f"{source}+shift"
        shift_details["dx_req"] = int(req_dx)
        shift_details["dy_req"] = int(req_dy)
        shift_details["dx_applied"] = int(applied_dx)
        shift_details["dy_applied"] = int(applied_dy)
        shift_details["clamped"] = bool(applied_dx != req_dx or applied_dy != req_dy)
    details["post_shift"] = shift_details

    roi_score = roi_candidate_score(frame, rect)
    details["roi_score"] = float(roi_score)
    details["feature_count"] = 0 if kp is None else int(len(kp))

    ref_payload = {
        "orientation": ori,
        "kp": kp,
        "des": des,
        "roi_score": float(roi_score),
    }
    return rect, source, details, ref_payload


def read_gray_roi(
    cap: cv2.VideoCapture,
    frame_idx: int,
    roi_rect: list[int],
    resize_w: int,
) -> np.ndarray | None:
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    x, y, w, h = roi_rect
    crop = frame[y : y + h, x : x + w]
    if crop.size == 0:
        return None
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    if resize_w > 0 and gray.shape[1] > resize_w:
        out_h = max(1, int(round(gray.shape[0] * resize_w / gray.shape[1])))
        gray = cv2.resize(gray, (resize_w, out_h), interpolation=cv2.INTER_AREA)
    return gray.astype(np.float32)


def ssim_global(img1: np.ndarray, img2: np.ndarray) -> float:
    if img1.shape != img2.shape:
        raise ValueError("SSIM input shape mismatch")
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    mu1 = float(np.mean(img1))
    mu2 = float(np.mean(img2))
    var1 = float(np.var(img1))
    var2 = float(np.var(img2))
    cov12 = float(np.mean((img1 - mu1) * (img2 - mu2)))
    numerator = (2 * mu1 * mu2 + c1) * (2 * cov12 + c2)
    denominator = (mu1 * mu1 + mu2 * mu2 + c1) * (var1 + var2 + c2)
    if denominator <= 0:
        return 1.0 if np.allclose(img1, img2) else 0.0
    value = numerator / denominator
    return float(max(-1.0, min(1.0, value)))


def sample_points(duration_sec: float, fps: float, coarse_fps: float, frame_count: int) -> tuple[np.ndarray, np.ndarray]:
    if coarse_fps <= 0:
        raise PipelineError("coarse_fps must be > 0")
    max_index = max(frame_count - 1, 0)
    n = max(2, int(math.floor(duration_sec * coarse_fps)) + 1)
    t = np.linspace(0.0, duration_sec, num=n, endpoint=True)
    idx = np.clip(np.rint(t * fps).astype(np.int64), 0, max_index)
    uniq_idx, uniq_pos = np.unique(idx, return_index=True)
    uniq_t = t[uniq_pos]
    return uniq_idx, uniq_t


def compute_score_curve(
    video_path: Path,
    meta: dict[str, Any],
    roi_rect: list[int],
    method: str,
    coarse_fps: float,
    resize_w: int,
    ssim_ref_n_ref: int,
) -> tuple[np.ndarray, np.ndarray]:
    if method == "flow":
        raise PipelineError("method=flow is planned for v2; MVP supports ssim_ref/diff_prev")
    if method not in ("ssim_ref", "diff_prev"):
        raise PipelineError(f"Unsupported method: {method}")

    idx_arr, t_arr = sample_points(meta["duration_sec"], meta["fps"], coarse_fps, meta["frame_count"])
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise PipelineError("VideoCapture open failed in coarse scan")

    scores: list[float] = []
    ref_frame: np.ndarray | None = None
    prev_frame: np.ndarray | None = None
    try:
        if method == "ssim_ref":
            n_ref = max(1, int(ssim_ref_n_ref))
            ref_samples: list[np.ndarray] = []
            for idx in idx_arr[:n_ref]:
                gray = read_gray_roi(cap, int(idx), roi_rect, resize_w)
                if gray is not None:
                    ref_samples.append(gray)
            if not ref_samples:
                raise PipelineError("No valid reference frame for ssim_ref")
            if len(ref_samples) == 1:
                ref_frame = ref_samples[0]
            else:
                ref_frame = np.median(np.stack(ref_samples, axis=0), axis=0).astype(np.float32)

        for idx in idx_arr:
            gray = read_gray_roi(cap, int(idx), roi_rect, resize_w)
            if gray is None:
                scores.append(float("nan"))
                continue
            if method == "ssim_ref":
                ssim_val = ssim_global(ref_frame, gray)
                score = 1.0 - ssim_val
            else:
                if prev_frame is None:
                    score = 0.0
                else:
                    score = float(np.mean(np.abs(gray - prev_frame)) / 255.0)
            prev_frame = gray
            scores.append(float(score))
    finally:
        cap.release()

    score_arr = np.array(scores, dtype=np.float64)
    valid = np.isfinite(score_arr)
    if not np.any(valid):
        raise PipelineError("No valid score in score_curve")
    if not np.all(valid):
        finite_mean = float(np.nanmean(score_arr))
        score_arr[~valid] = finite_mean
    return t_arr.astype(np.float64), score_arr


def smooth_series(values: np.ndarray, win: int) -> np.ndarray:
    if win <= 1 or len(values) <= 1:
        return values.copy()
    win = min(int(win), len(values))
    kernel = np.ones(win, dtype=np.float64) / float(win)
    return np.convolve(values, kernel, mode="same")


def find_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start = None
    for i, is_on in enumerate(mask.tolist()):
        if is_on and start is None:
            start = i
        elif not is_on and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def find_local_peaks(values: np.ndarray) -> list[int]:
    if len(values) == 0:
        return []
    if len(values) == 1:
        return [0]
    peaks: list[int] = []
    for i in range(len(values)):
        left = values[i - 1] if i > 0 else -np.inf
        right = values[i + 1] if i < len(values) - 1 else -np.inf
        if values[i] >= left and values[i] >= right:
            peaks.append(i)
    return peaks


def centered_segment(center_t: float, seg_len: float, duration: float) -> tuple[float, float]:
    if duration <= 0:
        return 0.0, 0.0
    seg_len = max(0.1, float(seg_len))
    start = max(0.0, center_t - seg_len / 2.0)
    end = min(duration, start + seg_len)
    start = max(0.0, end - seg_len)
    if end <= start:
        return 0.0, duration
    return float(start), float(end)


def detect_segments(
    t_arr: np.ndarray,
    score_arr: np.ndarray,
    duration_sec: float,
    cfg: dict[str, Any],
    logger: logging.Logger,
    video_id: str,
) -> dict[str, Any]:
    smooth_win = int(cfg.get("smooth_win", 5))
    segment_len_sec = float(cfg.get("segment_len_sec", 8.0))
    merge_gap_sec = float(cfg.get("merge_gap_sec", 0.5))
    segment_strategy = str(cfg.get("segment_strategy", "peak")).lower()
    if segment_strategy not in ("peak", "run"):
        raise PipelineError(f"Unsupported segment_strategy: {segment_strategy}")
    run_pre_pad_sec = max(0.0, float(cfg.get("run_pre_pad_sec", 1.0)))
    run_post_pad_sec = max(0.0, float(cfg.get("run_post_pad_sec", 1.5)))
    run_max_len_sec = float(cfg.get("run_max_len_sec", 20.0))
    if run_max_len_sec < 0:
        run_max_len_sec = 0.0
    topk = int(cfg.get("topk", 3))
    topk = max(1, topk)
    fill_to_topk = bool(cfg.get("fill_to_topk", True))

    smoothed = smooth_series(score_arr, smooth_win)
    threshold_cfg = cfg.get("threshold", {}) or {}
    threshold_mode = str(threshold_cfg.get("mode", "quantile")).lower()

    if threshold_mode == "quantile":
        q = float(threshold_cfg.get("q", 0.995))
        q = min(0.999999, max(0.0, q))
        threshold_value = float(np.quantile(smoothed, q))
        threshold_meta = {"mode": "quantile", "q": q, "value": threshold_value}
    elif threshold_mode == "mad":
        k = float(threshold_cfg.get("mad_k", 6.0))
        median = float(np.median(smoothed))
        mad = float(np.median(np.abs(smoothed - median)))
        threshold_value = float(median + k * mad)
        threshold_meta = {
            "mode": "mad",
            "median": median,
            "mad": mad,
            "mad_k": k,
            "value": threshold_value,
        }
    else:
        raise PipelineError(f"Unsupported threshold mode: {threshold_mode}")
    threshold_meta["segment_strategy"] = segment_strategy
    if segment_strategy == "run":
        threshold_meta["run_pre_pad_sec"] = run_pre_pad_sec
        threshold_meta["run_post_pad_sec"] = run_post_pad_sec
        threshold_meta["run_max_len_sec"] = run_max_len_sec

    runs = find_runs(smoothed >= threshold_value)
    raw_segments: list[dict[str, Any]] = []

    for run_start, run_end in runs:
        local = smoothed[run_start : run_end + 1]
        peak_idx = int(run_start + np.argmax(local))
        peak_t = float(t_arr[peak_idx])
        peak_score = float(score_arr[peak_idx])
        peak_smooth = float(smoothed[peak_idx])
        if segment_strategy == "run":
            run_start_t = float(t_arr[run_start])
            run_end_t = float(t_arr[run_end])
            if run_end_t < run_start_t:
                run_start_t, run_end_t = run_end_t, run_start_t
            start = max(0.0, run_start_t - run_pre_pad_sec)
            end = min(duration_sec, run_end_t + run_post_pad_sec)
            if end - start < segment_len_sec:
                c_start, c_end = centered_segment(peak_t, segment_len_sec, duration_sec)
                start = max(0.0, min(start, c_start))
                end = min(duration_sec, max(end, c_end))
            if run_max_len_sec > 0.0 and (end - start) > run_max_len_sec:
                start, end = centered_segment(peak_t, run_max_len_sec, duration_sec)
        else:
            start, end = centered_segment(peak_t, segment_len_sec, duration_sec)
        raw_segments.append(
            {
                "start_sec": start,
                "end_sec": end,
                "peak_t_sec": peak_t,
                "peak_score": peak_score,
                "peak_smooth_score": peak_smooth,
                "fallback": False,
            }
        )

    if not raw_segments:
        peak_idx = int(np.argmax(smoothed))
        peak_t = float(t_arr[peak_idx])
        start, end = centered_segment(peak_t, segment_len_sec, duration_sec)
        raw_segments.append(
            {
                "start_sec": start,
                "end_sec": end,
                "peak_t_sec": peak_t,
                "peak_score": float(score_arr[peak_idx]),
                "peak_smooth_score": float(smoothed[peak_idx]),
                "fallback": True,
            }
        )
        logger.warning("[%s] no run above threshold, using fallback around global peak", video_id)

    if fill_to_topk and len(raw_segments) < topk:
        peak_indices = find_local_peaks(smoothed)
        peak_indices = sorted(peak_indices, key=lambda i: float(smoothed[i]), reverse=True)
        min_peak_gap = max(merge_gap_sec, segment_len_sec + merge_gap_sec)
        existing_peak_ts = [float(seg["peak_t_sec"]) for seg in raw_segments]

        for peak_idx in peak_indices:
            peak_t = float(t_arr[peak_idx])
            if any(abs(peak_t - t0) < min_peak_gap for t0 in existing_peak_ts):
                continue
            start, end = centered_segment(peak_t, segment_len_sec, duration_sec)
            raw_segments.append(
                {
                    "start_sec": start,
                    "end_sec": end,
                    "peak_t_sec": peak_t,
                    "peak_score": float(score_arr[peak_idx]),
                    "peak_smooth_score": float(smoothed[peak_idx]),
                    "fallback": True,
                }
            )
            existing_peak_ts.append(peak_t)
            if len(raw_segments) >= topk:
                break

        if len(raw_segments) > 0 and len(raw_segments) < topk:
            logger.warning(
                "[%s] only found %d candidate segment(s) after supplementation (topk=%d)",
                video_id,
                len(raw_segments),
                topk,
            )

    raw_segments.sort(key=lambda s: s["start_sec"])
    merged: list[dict[str, Any]] = []
    for seg in raw_segments:
        if not merged:
            seg_copy = dict(seg)
            seg_copy["merged_count"] = 1
            merged.append(seg_copy)
            continue
        prev = merged[-1]
        if seg["start_sec"] <= prev["end_sec"] + merge_gap_sec:
            prev["start_sec"] = min(prev["start_sec"], seg["start_sec"])
            prev["end_sec"] = max(prev["end_sec"], seg["end_sec"])
            prev["fallback"] = bool(prev["fallback"] and seg["fallback"])
            prev["merged_count"] = int(prev["merged_count"]) + 1
            if seg["peak_smooth_score"] > prev["peak_smooth_score"]:
                prev["peak_smooth_score"] = seg["peak_smooth_score"]
                prev["peak_score"] = seg["peak_score"]
                prev["peak_t_sec"] = seg["peak_t_sec"]
        else:
            seg_copy = dict(seg)
            seg_copy["merged_count"] = 1
            merged.append(seg_copy)

    if segment_strategy == "peak":
        for seg in merged:
            seg["start_sec"], seg["end_sec"] = centered_segment(
                float(seg["peak_t_sec"]),
                segment_len_sec,
                duration_sec,
            )
    elif run_max_len_sec > 0.0:
        for seg in merged:
            if float(seg["end_sec"]) - float(seg["start_sec"]) > run_max_len_sec:
                seg["start_sec"], seg["end_sec"] = centered_segment(
                    float(seg["peak_t_sec"]),
                    run_max_len_sec,
                    duration_sec,
                )

    ranked = sorted(merged, key=lambda s: s["peak_smooth_score"], reverse=True)[:topk]
    ranked.sort(key=lambda s: s["start_sec"])

    segments: list[dict[str, Any]] = []
    for i, seg in enumerate(ranked, start=1):
        seg["start_sec"] = float(max(0.0, min(duration_sec, seg["start_sec"])))
        seg["end_sec"] = float(max(0.0, min(duration_sec, seg["end_sec"])))
        if seg["end_sec"] <= seg["start_sec"]:
            seg["start_sec"], seg["end_sec"] = centered_segment(seg["peak_t_sec"], segment_len_sec, duration_sec)
        segments.append(
            {
                "segment_id": f"seg_{i:03d}",
                "start_sec": float(seg["start_sec"]),
                "end_sec": float(seg["end_sec"]),
                "peak_t_sec": float(seg["peak_t_sec"]),
                "peak_score": float(seg["peak_score"]),
                "peak_smooth_score": float(seg["peak_smooth_score"]),
                "fallback": bool(seg["fallback"]),
                "merged_count": int(seg["merged_count"]),
            }
        )

    return {
        "threshold": threshold_meta,
        "segments": segments,
        "smoothed_scores": smoothed,
    }


def qscale_to_jpeg_quality(q: int) -> int:
    q = max(2, min(31, int(q)))
    quality = int(round(100 - (q - 2) * (80.0 / 29.0)))
    return max(20, min(100, quality))


def image_write(path: Path, image: np.ndarray, ext: str, jpg_q: int) -> None:
    ensure_dir(path.parent)
    ext = ext.lower()
    if ext == "png":
        ok = cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    else:
        quality = qscale_to_jpeg_quality(jpg_q)
        ok = cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise PipelineError(f"Failed to write image: {path}")


def parse_crop_resize(value: Any) -> tuple[int, int] | None:
    if value is None:
        return None
    if isinstance(value, bool):
        if value:
            raise PipelineError("frame_extraction.crop_resize=true is invalid; use int or [w,h]")
        return None
    if isinstance(value, (int, float)):
        size = int(round(float(value)))
        if size <= 0:
            raise PipelineError("frame_extraction.crop_resize must be > 0")
        return size, size
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("", "none", "null", "off"):
            return None
        if text.isdigit():
            size = int(text)
            if size <= 0:
                raise PipelineError("frame_extraction.crop_resize must be > 0")
            return size, size
        match = re.match(r"^(\d+)\s*[x,]\s*(\d+)$", text)
        if match:
            w = int(match.group(1))
            h = int(match.group(2))
            if w <= 0 or h <= 0:
                raise PipelineError("frame_extraction.crop_resize dimensions must be > 0")
            return w, h
        raise PipelineError("frame_extraction.crop_resize string must be '640' or '640x640'")
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            w = int(round(float(value[0])))
            h = int(round(float(value[1])))
        except (TypeError, ValueError) as exc:
            raise PipelineError("frame_extraction.crop_resize must contain numbers") from exc
        if w <= 0 or h <= 0:
            raise PipelineError("frame_extraction.crop_resize dimensions must be > 0")
        return w, h
    raise PipelineError("frame_extraction.crop_resize must be null, int, 'WxH', or [w,h]")


def resize_crop_image(
    crop: np.ndarray,
    target_size: tuple[int, int] | None,
    mode: str,
    pad_value: int,
) -> np.ndarray:
    if target_size is None:
        return crop
    target_w, target_h = target_size
    src_h, src_w = crop.shape[:2]
    if src_w == target_w and src_h == target_h:
        return crop

    interp = cv2.INTER_AREA if target_w < src_w or target_h < src_h else cv2.INTER_LINEAR
    mode = str(mode).strip().lower()
    if mode == "stretch":
        return cv2.resize(crop, (target_w, target_h), interpolation=interp)
    if mode != "letterbox":
        raise PipelineError("frame_extraction.crop_resize_mode must be 'letterbox' or 'stretch'")

    scale = min(target_w / max(1.0, float(src_w)), target_h / max(1.0, float(src_h)))
    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    resized = cv2.resize(crop, (new_w, new_h), interpolation=interp)

    fill = int(np.clip(int(pad_value), 0, 255))
    if crop.ndim == 2:
        canvas = np.full((target_h, target_w), fill, dtype=crop.dtype)
    else:
        canvas = np.full((target_h, target_w, crop.shape[2]), fill, dtype=crop.dtype)
    x0 = (target_w - new_w) // 2
    y0 = (target_h - new_h) // 2
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    return canvas


def resolve_frame_name_id(
    local_seq_idx: int,
    naming_state: dict[str, Any],
) -> tuple[str, int]:
    mode = str(naming_state.get("mode", "per_segment")).strip().lower()
    if mode == "global":
        seq_idx = int(naming_state.get("next_index", 0))
        naming_state["next_index"] = seq_idx + 1
    else:
        seq_idx = int(local_seq_idx)
    if seq_idx < 0:
        raise PipelineError("frame_extraction.global_start_index must be >= 0")
    return f"{seq_idx:06d}", seq_idx


def extraction_points(
    start_sec: float,
    end_sec: float,
    fine_fps: float,
    src_fps: float,
    frame_count: int,
) -> tuple[list[int], list[float]]:
    if fine_fps <= 0:
        raise PipelineError("fine_fps must be > 0")
    if end_sec <= start_sec:
        end_sec = start_sec + 1.0 / max(1.0, fine_fps)
    times = np.arange(start_sec, end_sec + 1e-9, 1.0 / fine_fps, dtype=np.float64)
    if len(times) == 0:
        times = np.array([start_sec], dtype=np.float64)
    max_idx = max(0, frame_count - 1)
    idx = np.clip(np.rint(times * src_fps).astype(np.int64), 0, max_idx)

    uniq_idx: list[int] = []
    uniq_t: list[float] = []
    prev = None
    for f_idx, t_sec in zip(idx.tolist(), times.tolist()):
        if prev is not None and f_idx == prev:
            continue
        uniq_idx.append(int(f_idx))
        uniq_t.append(float(t_sec))
        prev = int(f_idx)
    if not uniq_idx:
        uniq_idx = [int(np.clip(round(start_sec * src_fps), 0, max_idx))]
        uniq_t = [float(start_sec)]
    return uniq_idx, uniq_t


def write_manifest(manifest_path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dir(manifest_path.parent)
    fieldnames = [
        "frame_id",
        "t_sec",
        "source_frame_idx",
        "frame_path",
        "crop_path",
        "roi_rect",
    ]
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def build_contact_sheet(
    frame_paths: list[Path],
    tile_cols: int,
    tile_rows: int,
) -> np.ndarray | None:
    if not frame_paths:
        return None
    imgs: list[np.ndarray] = []
    for p in frame_paths:
        img = cv2.imread(str(p))
        if img is not None:
            imgs.append(img)
    if not imgs:
        return None

    h0, w0 = imgs[0].shape[:2]
    tile_count = tile_cols * tile_rows
    canvas: list[np.ndarray] = []
    for img in imgs[:tile_count]:
        if img.shape[:2] != (h0, w0):
            img = cv2.resize(img, (w0, h0), interpolation=cv2.INTER_AREA)
        canvas.append(img)
    while len(canvas) < tile_count:
        canvas.append(np.zeros((h0, w0, 3), dtype=np.uint8))

    rows: list[np.ndarray] = []
    for r in range(tile_rows):
        start = r * tile_cols
        rows.append(np.hstack(canvas[start : start + tile_cols]))
    return np.vstack(rows)


def choose_preview_frames(
    extracted_abs_paths: list[Path],
    segment_len_sec: float,
    contact_sheet_fps: float,
    slots: int,
) -> list[Path]:
    if not extracted_abs_paths:
        return []
    target_count = int(math.ceil(max(0.1, segment_len_sec) * max(0.1, contact_sheet_fps)))
    target_count = max(1, min(slots, target_count, len(extracted_abs_paths)))
    if target_count <= 1:
        return [extracted_abs_paths[0]]
    idx = np.linspace(0, len(extracted_abs_paths) - 1, num=target_count, endpoint=True)
    idx = np.unique(np.rint(idx).astype(np.int64))
    return [extracted_abs_paths[int(i)] for i in idx.tolist()]


def extract_segment(
    video_path: Path,
    meta: dict[str, Any],
    roi_rect: list[int],
    seg: dict[str, Any],
    frame_cfg: dict[str, Any],
    report_cfg: dict[str, Any],
    video_out_dir: Path,
    naming_state: dict[str, Any],
) -> tuple[Path, Path | None, list[dict[str, Any]], Path | None]:
    segment_id = seg["segment_id"]
    seg_dir = video_out_dir / segment_id
    frames_dir = seg_dir / "frames"
    crops_dir = seg_dir / "crops"
    ensure_dir(frames_dir)
    if frame_cfg.get("crop_output", True):
        ensure_dir(crops_dir)

    ext = str(frame_cfg.get("image_ext", "jpg")).lower()
    if ext not in ("jpg", "jpeg", "png"):
        ext = "jpg"
    if ext == "jpeg":
        ext = "jpg"
    jpg_q = int(frame_cfg.get("jpg_quality_q", 2))
    crop_resize = parse_crop_resize(frame_cfg.get("crop_resize"))
    crop_resize_mode = str(frame_cfg.get("crop_resize_mode", "letterbox"))
    crop_resize_pad_value = int(frame_cfg.get("crop_resize_pad_value", 114))

    fine_fps = float(frame_cfg.get("fine_fps", 10.0))
    idx_points, t_points = extraction_points(
        start_sec=float(seg["start_sec"]),
        end_sec=float(seg["end_sec"]),
        fine_fps=fine_fps,
        src_fps=float(meta["fps"]),
        frame_count=int(meta["frame_count"]),
    )

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise PipelineError("VideoCapture open failed in extraction")

    rows: list[dict[str, Any]] = []
    extracted_abs: list[Path] = []
    extracted_crop_abs: list[Path] = []
    try:
        for i, (src_idx, req_t) in enumerate(zip(idx_points, t_points), start=1):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(src_idx))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            frame_id_str, _seq_idx = resolve_frame_name_id(i, naming_state)
            frame_name = f"{frame_id_str}.{ext}"
            frame_abs = frames_dir / frame_name
            image_write(frame_abs, frame, ext, jpg_q)
            extracted_abs.append(frame_abs)

            crop_rel = ""
            if frame_cfg.get("crop_output", True):
                x, y, w, h = roi_rect
                crop = frame[y : y + h, x : x + w]
                crop = resize_crop_image(
                    crop=crop,
                    target_size=crop_resize,
                    mode=crop_resize_mode,
                    pad_value=crop_resize_pad_value,
                )
                crop_abs = crops_dir / frame_name
                image_write(crop_abs, crop, ext, jpg_q)
                extracted_crop_abs.append(crop_abs)
                crop_rel = Path("crops") / frame_name
                crop_rel = crop_rel.as_posix()

            rows.append(
                {
                    "frame_id": frame_id_str,
                    "t_sec": f"{(src_idx / float(meta['fps'])):.6f}",
                    "source_frame_idx": int(src_idx),
                    "frame_path": (Path("frames") / frame_name).as_posix(),
                    "crop_path": crop_rel,
                    "roi_rect": ",".join(str(v) for v in roi_rect),
                }
            )
    finally:
        cap.release()

    if not rows:
        raise PipelineError(f"{segment_id}: extracted 0 frame")

    manifest_path = seg_dir / "manifest.csv"
    write_manifest(manifest_path, rows)

    tile = report_cfg.get("tile", [4, 3])
    if not isinstance(tile, (list, tuple)) or len(tile) != 2:
        tile = [4, 3]
    tile_cols = max(1, int(tile[0]))
    tile_rows = max(1, int(tile[1]))
    slots = tile_cols * tile_rows
    contact_sheet_fps = float(report_cfg.get("contact_sheet_fps", 1.0))
    preview_source = extracted_crop_abs if extracted_crop_abs else extracted_abs
    selected = choose_preview_frames(
        preview_source,
        float(seg["end_sec"] - seg["start_sec"]),
        contact_sheet_fps,
        slots,
    )
    preview_path = None
    preview = build_contact_sheet(selected, tile_cols, tile_rows)
    if preview is not None:
        preview_path = seg_dir / "preview.jpg"
        image_write(preview_path, preview, "jpg", 2)

    return seg_dir, manifest_path, rows, preview_path


def score_csv_write(
    out_path: Path,
    t_arr: np.ndarray,
    score_arr: np.ndarray,
    smooth_arr: np.ndarray,
) -> None:
    ensure_dir(out_path.parent)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["t_sec", "score", "score_smooth"])
        for t, s, ss in zip(t_arr.tolist(), score_arr.tolist(), smooth_arr.tolist()):
            writer.writerow([f"{t:.6f}", f"{s:.8f}", f"{ss:.8f}"])


def make_index_html(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "<!doctype html>",
        "<html lang='en'>",
        "<head>",
        "<meta charset='utf-8'/>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'/>",
        "<title>Crack Pipeline Report</title>",
        "<style>",
        "body{font-family:Segoe UI,Arial,sans-serif;margin:24px;background:#f5f7fa;color:#1f2937;}",
        "h1{margin:0 0 12px 0;font-size:24px;}",
        "table{border-collapse:collapse;width:100%;background:#fff;}",
        "th,td{border:1px solid #d0d7de;padding:8px;vertical-align:top;}",
        "th{background:#eff3f8;text-align:left;}",
        "img{max-width:520px;height:auto;display:block;}",
        ".small{color:#6b7280;font-size:12px;}",
        "</style>",
        "</head>",
        "<body>",
        "<h1>Crack Candidate Segments</h1>",
        f"<p class='small'>Generated at {html.escape(datetime.now().isoformat(timespec='seconds'))}</p>",
        "<table>",
        "<thead><tr><th>video_id</th><th>segment_id</th><th>start/end (s)</th><th>method</th><th>preview</th></tr></thead>",
        "<tbody>",
    ]

    for row in rows:
        preview_html = "-"
        preview_rel = row.get("preview_rel")
        if preview_rel:
            preview_html = (
                f"<a href='{html.escape(preview_rel)}' target='_blank'>"
                f"<img src='{html.escape(preview_rel)}' loading='lazy'/></a>"
            )
        lines.append(
            "<tr>"
            f"<td>{html.escape(str(row['video_id']))}</td>"
            f"<td>{html.escape(str(row['segment_id']))}</td>"
            f"<td>{float(row['start_sec']):.3f} - {float(row['end_sec']):.3f}</td>"
            f"<td>{html.escape(str(row['method']))}</td>"
            f"<td>{preview_html}</td>"
            "</tr>"
        )

    lines.extend(["</tbody>", "</table>", "</body>", "</html>"])
    (run_dir / "index.html").write_text("\n".join(lines), encoding="utf-8")


def process_video(
    video_path: Path,
    cfg: dict[str, Any],
    out_root: Path,
    roi_ctx: dict[str, Any],
    logger: logging.Logger,
    dry_run: bool,
    report_rows: list[dict[str, Any]],
    roi_rows: list[dict[str, Any]],
    naming_state: dict[str, Any],
) -> None:
    video_id = video_path.stem
    video_out_dir = out_root / video_id
    ensure_dir(video_out_dir)
    ensure_dir(video_out_dir / "metrics")

    meta = probe_video(video_path)
    meta["video_id"] = video_id
    write_json(video_out_dir / "meta.json", meta)

    roi_rect, roi_source, roi_details, roi_ref_payload = resolve_video_roi(
        video_path=video_path,
        meta=meta,
        cfg=cfg,
        roi_ctx=roi_ctx,
        logger=logger,
        video_id=video_id,
    )

    seg_cfg = cfg.get("segment_detection", {})
    method = str(seg_cfg.get("method", "ssim_ref")).lower()
    t_arr, score_arr = compute_score_curve(
        video_path=video_path,
        meta=meta,
        roi_rect=roi_rect,
        method=method,
        coarse_fps=float(seg_cfg.get("coarse_fps", 2.0)),
        resize_w=int(seg_cfg.get("resize_w", 320)),
        ssim_ref_n_ref=int(seg_cfg.get("ssim_ref_n_ref", 5)),
    )

    detected = detect_segments(
        t_arr=t_arr,
        score_arr=score_arr,
        duration_sec=float(meta["duration_sec"]),
        cfg=seg_cfg,
        logger=logger,
        video_id=video_id,
    )

    score_csv_write(video_out_dir / "metrics" / "score_curve.csv", t_arr, score_arr, detected["smoothed_scores"])

    segments_json = {
        "video_id": video_id,
        "video_path": str(video_path),
        "method": method,
        "duration_sec": float(meta["duration_sec"]),
        "roi": {
            "mode": str(cfg.get("roi", {}).get("mode", "fixed")),
            "rect": roi_rect,
            "source": roi_source,
            "details": roi_details,
        },
        "threshold": detected["threshold"],
        "segments": detected["segments"],
    }
    write_json(video_out_dir / "segments.json", segments_json)

    roi_rows.append(
        {
            "video_id": video_id,
            "video_path": str(video_path),
            "width": int(meta["width"]),
            "height": int(meta["height"]),
            "roi_rect": roi_rect,
            "roi_source": roi_source,
            "roi_details": roi_details,
        }
    )

    if roi_ref_payload is not None:
        update_auto_roi_context(
            roi_ctx=roi_ctx,
            orientation=str(roi_ref_payload.get("orientation", orientation_label(int(meta["width"]), int(meta["height"])))),
            video_path=video_path,
            roi_rect=roi_rect,
            kp=roi_ref_payload.get("kp"),
            des=roi_ref_payload.get("des"),
            roi_score=float(roi_ref_payload.get("roi_score", 0.0)),
            logger=logger,
        )

    if dry_run:
        for seg in detected["segments"]:
            report_rows.append(
                {
                    "video_id": video_id,
                    "segment_id": seg["segment_id"],
                    "start_sec": seg["start_sec"],
                    "end_sec": seg["end_sec"],
                    "method": method,
                    "preview_rel": None,
                }
            )
        return

    frame_cfg = cfg.get("frame_extraction", {})
    report_cfg = cfg.get("report", {})

    for seg in detected["segments"]:
        seg_dir, _manifest_path, rows, preview_path = extract_segment(
            video_path=video_path,
            meta=meta,
            roi_rect=roi_rect,
            seg=seg,
            frame_cfg=frame_cfg,
            report_cfg=report_cfg,
            video_out_dir=video_out_dir,
            naming_state=naming_state,
        )
        if frame_cfg.get("crop_output", True):
            frames_count = len(list((seg_dir / "frames").glob("*")))
            crops_count = len(list((seg_dir / "crops").glob("*")))
            if frames_count != crops_count:
                raise PipelineError(f"{video_id}/{seg['segment_id']}: frames({frames_count}) != crops({crops_count})")
        if not rows:
            raise PipelineError(f"{video_id}/{seg['segment_id']}: empty manifest")

        report_rows.append(
            {
                "video_id": video_id,
                "segment_id": seg["segment_id"],
                "start_sec": seg["start_sec"],
                "end_sec": seg["end_sec"],
                "method": method,
                "preview_rel": None if preview_path is None else os.path.relpath(preview_path, out_root / "_run").replace("\\", "/"),
            }
        )


def run() -> int:
    args = parse_args()
    cfg = load_config(args.config)
    cfg = apply_cli_overrides(cfg, args)

    input_dir = Path(cfg["input_dir"]).expanduser().resolve()
    output_dir = Path(cfg["output_dir"]).expanduser().resolve()
    run_dir = output_dir / "_run"
    ensure_dir(run_dir)

    logger = setup_logging(run_dir, str(cfg.get("runtime", {}).get("log_level", "INFO")))
    write_versions(run_dir)

    cfg["input_dir"] = str(input_dir)
    cfg["output_dir"] = str(output_dir)
    write_yaml(run_dir / "config_resolved.yaml", cfg)

    fail_fast = bool(cfg.get("runtime", {}).get("fail_fast", False))
    dry_run = bool(cfg.get("runtime", {}).get("dry_run", False))
    frame_cfg = cfg.get("frame_extraction", {}) or {}
    naming_mode = str(frame_cfg.get("naming_mode", "per_segment")).strip().lower()
    if naming_mode not in ("per_segment", "global"):
        raise PipelineError("frame_extraction.naming_mode must be 'per_segment' or 'global'")
    global_start_index = int(frame_cfg.get("global_start_index", 0))
    if global_start_index < 0:
        raise PipelineError("frame_extraction.global_start_index must be >= 0")
    naming_state: dict[str, Any] = {
        "mode": naming_mode,
        "next_index": int(global_start_index),
    }

    failures: list[dict[str, Any]] = []
    report_rows: list[dict[str, Any]] = []
    roi_rows: list[dict[str, Any]] = []

    videos = list_videos(input_dir)
    max_videos = cfg.get("runtime", {}).get("max_videos")
    if max_videos is not None:
        max_videos = int(max_videos)
        if max_videos > 0:
            videos = videos[:max_videos]
    if not videos:
        raise PipelineError(f"No mp4 files found in {input_dir}")

    roi_ctx = build_auto_roi_context(videos, cfg, logger)

    logger.info("pipeline start: videos=%d dry_run=%s", len(videos), dry_run)
    logger.info("input_dir=%s", input_dir)
    logger.info("output_dir=%s", output_dir)
    logger.info("roi_mode=%s", str(cfg.get("roi", {}).get("mode", "fixed")))
    logger.info(
        "frame_naming_mode=%s start_index=%d",
        naming_mode,
        global_start_index if naming_mode == "global" else 1,
    )

    for idx, video_path in enumerate(videos, start=1):
        video_id = video_path.stem
        logger.info("[%d/%d] processing %s", idx, len(videos), video_path.name)
        try:
            process_video(
                video_path=video_path,
                cfg=cfg,
                out_root=output_dir,
                roi_ctx=roi_ctx,
                logger=logger,
                dry_run=dry_run,
                report_rows=report_rows,
                roi_rows=roi_rows,
                naming_state=naming_state,
            )
        except Exception as exc:  # noqa: BLE001
            err = {
                "video_id": video_id,
                "video_path": str(video_path),
                "error": str(exc),
                "traceback_tail": "\n".join(traceback.format_exc().strip().splitlines()[-10:]),
            }
            failures.append(err)
            logger.error("[%s] failed: %s", video_id, exc)
            if fail_fast:
                write_json(run_dir / "failures.json", failures)
                write_json(run_dir / "roi_overrides.json", roi_rows)
                if cfg.get("report", {}).get("enable_html", True):
                    make_index_html(run_dir, report_rows)
                return 1

    if cfg.get("report", {}).get("enable_html", True):
        make_index_html(run_dir, report_rows)
    write_json(run_dir / "failures.json", failures)
    write_json(run_dir / "roi_overrides.json", roi_rows)

    logger.info("pipeline done: success=%d failure=%d", len(videos) - len(failures), len(failures))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except PipelineError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        raise SystemExit(2)
