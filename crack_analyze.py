#!/usr/bin/env python3
# 中文注释：本文件实现裂纹视频分析 MVP 流程。
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


# 中文注释：默认配置：统一定义 ROI、分段、抽帧和报告参数。
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


# 中文注释：自定义异常类型，用于统一错误处理。
class PipelineError(RuntimeError):
    """A controlled pipeline exception."""


# 中文注释：函数 natural_key，用于当前步骤处理。
def natural_key(text: str) -> list[Any]:
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", text)]


# 中文注释：函数 deep_merge，用于当前步骤处理。
def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    merged = dict(base)
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for key, value in override.items():
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            merged[key] = deep_merge(merged[key], value)
        else:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            merged[key] = value
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return merged


# 中文注释：函数 ensure_dir，用于当前步骤处理。
def ensure_dir(path: Path) -> None:
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    path.mkdir(parents=True, exist_ok=True)


# 中文注释：函数 write_json，用于当前步骤处理。
def write_json(path: Path, data: Any) -> None:
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(path.parent)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# 中文注释：函数 write_yaml，用于当前步骤处理。
def write_yaml(path: Path, data: Any) -> None:
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(path.parent)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# 中文注释：函数 parse_roi_text，用于当前步骤处理。
def parse_roi_text(roi_text: str) -> list[int]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    parts = [p.strip() for p in roi_text.split(",")]
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if len(parts) != 4:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise argparse.ArgumentTypeError("ROI must be x,y,w,h")
    # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
    try:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        vals = [int(float(p)) for p in parts]
    except ValueError as exc:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise argparse.ArgumentTypeError("ROI must be integers") from exc
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if vals[2] <= 0 or vals[3] <= 0:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise argparse.ArgumentTypeError("ROI width/height must be > 0")
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return vals


# 中文注释：函数 parse_args，用于当前步骤处理。
def parse_args() -> argparse.Namespace:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    parser = argparse.ArgumentParser(description="Crack MVP pipeline")
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--input_dir", type=Path, default=None)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--output_dir", type=Path, default=None)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--method", choices=["ssim_ref", "diff_prev", "flow"], default=None)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--roi", type=parse_roi_text, default=None, help="Override fixed ROI x,y,w,h")
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--dry_run", action="store_true", help="Only produce score_curve + segments")
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--report_html", dest="report_html", action="store_true")
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--no-report_html", dest="report_html", action="store_false")
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.set_defaults(report_html=None)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--fail_fast", action="store_true")
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    parser.add_argument("--max_videos", type=int, default=None, help="Only process first N videos")
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return parser.parse_args()


# 中文注释：函数 load_config，用于当前步骤处理。
def load_config(config_path: Path | None) -> dict[str, Any]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cfg = dict(DEFAULT_CONFIG)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if config_path is not None:
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if not config_path.exists():
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError(f"Config not found: {config_path}")
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if not isinstance(loaded, dict):
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError("Config root must be a mapping")
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        cfg = deep_merge(cfg, loaded)
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return cfg


# 中文注释：函数 apply_cli_overrides，用于当前步骤处理。
def apply_cli_overrides(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    merged = deep_merge({}, cfg)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if args.input_dir is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        merged["input_dir"] = str(args.input_dir)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if args.output_dir is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        merged["output_dir"] = str(args.output_dir)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if args.method is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        merged["segment_detection"]["method"] = args.method
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if args.roi is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        merged["roi"]["mode"] = "fixed"
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        merged["roi"]["rect"] = args.roi
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if args.report_html is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        merged["report"]["enable_html"] = bool(args.report_html)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if args.fail_fast:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        merged["runtime"]["fail_fast"] = True
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    merged["runtime"]["dry_run"] = bool(args.dry_run)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if args.max_videos is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        merged["runtime"]["max_videos"] = args.max_videos
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return merged


# 中文注释：函数 setup_logging，用于当前步骤处理。
def setup_logging(run_dir: Path, level: str) -> logging.Logger:
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(run_dir)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    logger = logging.getLogger("crack_pipeline")
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.handlers.clear()
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    logger.propagate = False

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    file_handler = logging.FileHandler(run_dir / "run.log", encoding="utf-8")
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    file_handler.setFormatter(fmt)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    stream_handler = logging.StreamHandler(sys.stdout)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    stream_handler.setFormatter(fmt)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.addHandler(file_handler)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.addHandler(stream_handler)
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return logger


# 中文注释：函数 check_tool，用于当前步骤处理。
def check_tool(name: str) -> str | None:
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return shutil.which(name)


# 中文注释：函数 write_versions，用于当前步骤处理。
def write_versions(run_dir: Path) -> None:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
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
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    write_json(run_dir / "versions.json", data)


# 中文注释：函数 list_videos，用于当前步骤处理。
def list_videos(input_dir: Path) -> list[Path]:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not input_dir.exists():
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError(f"Input directory not found: {input_dir}")
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    videos = [p for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"]
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    videos.sort(key=lambda p: natural_key(p.name))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return videos


# 中文注释：函数 fourcc_to_str，用于当前步骤处理。
def fourcc_to_str(fourcc_int: int) -> str:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    chars = [chr((fourcc_int >> (8 * i)) & 0xFF) for i in range(4)]
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return "".join(chars).strip("\x00")


# 中文注释：函数 probe_video，用于当前步骤处理。
def probe_video(video_path: Path) -> dict[str, Any]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cap = cv2.VideoCapture(str(video_path))
    # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
    try:
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if not cap.isOpened():
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError("VideoCapture open failed")
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        fourcc_raw = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
    finally:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        cap.release()

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if width <= 0 or height <= 0:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("Invalid video dimensions")
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if fps <= 0:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("Invalid fps (<=0)")
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if frame_count <= 0:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("Invalid frame_count (<=0)")

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    duration_sec = frame_count / fps
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if duration_sec <= 0:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("Invalid duration (<=0)")

    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return {
        "video_path": str(video_path),
        "fps": fps,
        "width": width,
        "height": height,
        "frame_count": frame_count,
        "duration_sec": duration_sec,
        "codec_fourcc": fourcc_to_str(fourcc_raw),
    }


# 中文注释：函数 orientation_label，用于当前步骤处理。
def orientation_label(width: int, height: int) -> str:
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return "portrait" if int(height) > int(width) else "landscape"


# 中文注释：函数 read_representative_frame，用于当前步骤处理。
def read_representative_frame(video_path: Path, sample_sec: float, sample_frames: int) -> np.ndarray:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cap = cv2.VideoCapture(str(video_path))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not cap.isOpened():
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError(f"VideoCapture open failed: {video_path}")

    # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
    try:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if fps <= 0 or frame_count <= 0:
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError(f"Invalid video metadata for representative frame: {video_path}")

        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        max_frame_idx = min(frame_count - 1, int(max(0.2, sample_sec) * fps))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        n = max(1, min(int(sample_frames), max_frame_idx + 1))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        sample_idx = np.linspace(0, max_frame_idx, num=n, endpoint=True)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        sample_idx = np.unique(np.rint(sample_idx).astype(np.int64))

        # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
        frames: list[np.ndarray] = []
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for idx in sample_idx.tolist():
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(idx))
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            ok, frame = cap.read()
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if ok and frame is not None:
                # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                frames.append(frame)

        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if not frames:
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError(f"Cannot read representative frame: {video_path}")
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if len(frames) == 1:
            # 中文注释（函数内）：返回结果：结束当前函数并输出值。
            return frames[0]

        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        median = np.median(np.stack(frames, axis=0), axis=0)
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return np.clip(median, 0, 255).astype(np.uint8)
    finally:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        cap.release()


# 中文注释：函数 to_rect4，用于当前步骤处理。
def to_rect4(values: Any) -> list[float] | None:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
    try:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        rect = [float(v) for v in values]
    except (TypeError, ValueError):
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if rect[2] <= 0 or rect[3] <= 0:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return rect


# 中文注释：函数 to_vec2，用于当前步骤处理。
def to_vec2(values: Any) -> tuple[float, float] | None:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
    try:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        x = float(values[0])
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        y = float(values[1])
    except (TypeError, ValueError):
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return x, y


# 中文注释：函数 copper_mask_stats，用于当前步骤处理。
def copper_mask_stats(crop: np.ndarray) -> dict[str, Any]:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if crop.size == 0:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return {"ratio": 0.0, "pixels": 0, "cx": None, "cy": None}
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    mask = cv2.inRange(
        hsv,
        np.array([5, 50, 40], dtype=np.uint8),
        np.array([35, 255, 255], dtype=np.uint8),
    )
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    pixels = int(np.count_nonzero(mask))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ratio = float(np.mean(mask > 0))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if pixels < 30:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return {"ratio": ratio, "pixels": pixels, "cx": None, "cy": None}
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ys, xs = np.where(mask > 0)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cx = float(np.mean(xs) / max(1.0, crop.shape[1] - 1.0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cy = float(np.mean(ys) / max(1.0, crop.shape[0] - 1.0))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return {"ratio": ratio, "pixels": pixels, "cx": cx, "cy": cy}


# 中文注释：函数 roi_from_norm，用于当前步骤处理。
def roi_from_norm(rect_norm: list[float], width: int, height: int) -> list[int]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x = int(round(rect_norm[0] * width))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    y = int(round(rect_norm[1] * height))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    w = int(round(rect_norm[2] * width))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    h = int(round(rect_norm[3] * height))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return [x, y, max(1, w), max(1, h)]


# 中文注释：函数 resolve_roi_rect，用于当前步骤处理。
def resolve_roi_rect(
    roi_cfg: dict[str, Any],
    width: int,
    height: int,
) -> tuple[list[int] | None, str]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    orientation = "portrait" if height > width else "landscape"

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    key_order = [
        f"rect_{orientation}",
        "rect",
        f"rect_norm_{orientation}",
        "rect_norm",
    ]
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for key in key_order:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        parsed = to_rect4(roi_cfg.get(key))
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if parsed is None:
            # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
            continue
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if key.startswith("rect_norm"):
            # 中文注释（函数内）：返回结果：结束当前函数并输出值。
            return roi_from_norm(parsed, width, height), key
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return [int(round(v)) for v in parsed], key
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return None, "fallback_full_frame"


# 中文注释：函数 clip_roi，用于当前步骤处理。
def clip_roi(
    roi_mode: str,
    roi_rect: list[int] | None,
    width: int,
    height: int,
    logger: logging.Logger,
    video_id: str,
) -> list[int]:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if roi_mode != "fixed":
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError(f"MVP only supports roi.mode=fixed, got: {roi_mode}")

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if roi_rect is None:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        logger.warning("[%s] roi rect missing, fallback to full frame ROI", video_id)
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return [0, 0, width, height]

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x, y, w, h = [int(v) for v in roi_rect]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x0 = max(0, min(x, width - 1))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    y0 = max(0, min(y, height - 1))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x1 = max(x0 + 1, min(x + w, width))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    y1 = max(y0 + 1, min(y + h, height))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    clipped = [x0, y0, x1 - x0, y1 - y0]
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if clipped != [x, y, w, h]:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        logger.warning(
            "[%s] roi out of bounds, clipped from %s to %s",
            video_id,
            [x, y, w, h],
            clipped,
        )
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return clipped


# 中文注释：函数 clip_rect_silent，用于当前步骤处理。
def clip_rect_silent(rect: list[int], width: int, height: int) -> list[int]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x, y, w, h = [int(v) for v in rect]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x0 = max(0, min(x, width - 1))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    y0 = max(0, min(y, height - 1))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x1 = max(x0 + 1, min(x + w, width))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    y1 = max(y0 + 1, min(y + h, height))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return [x0, y0, x1 - x0, y1 - y0]


# 中文注释：函数 shift_rect_keep_size，用于当前步骤处理。
def shift_rect_keep_size(rect: list[int], dx: int, dy: int, width: int, height: int) -> list[int]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x, y, w, h = [int(v) for v in rect]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    w = max(1, min(int(w), int(width)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    h = max(1, min(int(h), int(height)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    max_x = max(0, int(width) - w)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    max_y = max(0, int(height) - h)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    nx = min(max(0, int(x) + int(dx)), max_x)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ny = min(max(0, int(y) + int(dy)), max_y)
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return [nx, ny, w, h]


# 中文注释：函数 resolve_post_shift，用于当前步骤处理。
def resolve_post_shift(
    frame: np.ndarray,
    rect: list[int],
    auto_cfg: dict[str, Any],
) -> tuple[int, int, dict[str, Any]]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    shift_mode = str(auto_cfg.get("post_shift_mode", "fixed")).strip().lower()
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    shift_ratio = to_vec2(auto_cfg.get("post_shift_ratio")) or (0.0, 0.0)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    shift_px = to_vec2(auto_cfg.get("post_shift_px")) or (0.0, 0.0)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    w = max(1, int(rect[2]))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    h = max(1, int(rect[3]))

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    dy = int(round(float(h) * float(shift_ratio[1]) + float(shift_px[1])))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if shift_mode in ("none", "off", "disabled", ""):
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return 0, dy, {"mode": "none"}

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if shift_mode == "fixed":
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        dx_fixed = int(round(float(w) * float(shift_ratio[0]) + float(shift_px[0])))
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return (
            dx_fixed,
            dy,
            {
                "mode": "fixed",
                "ratio": [float(shift_ratio[0]), float(shift_ratio[1])],
                "px": [int(round(shift_px[0])), int(round(shift_px[1]))],
            },
        )

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if shift_mode not in ("adaptive", "adaptive_copper"):
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("roi.auto.post_shift_mode must be one of: fixed|adaptive_copper|none")

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x, y = int(rect[0]), int(rect[1])
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    crop = frame[y : y + h, x : x + w]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    stats = copper_mask_stats(crop)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    target_x = float(auto_cfg.get("post_shift_target_x", 0.48))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    min_ratio = float(auto_cfg.get("post_shift_min_ratio", 0.01))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    min_pixels = max(1, int(auto_cfg.get("post_shift_min_pixels", 120)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    max_abs_ratio = max(0.0, float(auto_cfg.get("post_shift_max_abs_ratio", 0.35)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    max_abs_px = int(round(max_abs_ratio * w))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    direction = str(auto_cfg.get("post_shift_direction", "left_only")).strip().lower()
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    fallback = str(auto_cfg.get("post_shift_fallback", "none")).strip().lower()

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    adaptive_ok = (
        stats.get("cx") is not None
        and float(stats.get("ratio", 0.0)) >= min_ratio
        and int(stats.get("pixels", 0)) >= min_pixels
    )
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if adaptive_ok:
        # If copper appears too far left in crop (cx < target), move ROI left (negative dx).
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        raw_dx = int(round((float(stats["cx"]) - target_x) * w))
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if direction == "left_only":
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            raw_dx = min(0, raw_dx)
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        elif direction == "right_only":
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            raw_dx = max(0, raw_dx)
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        elif direction not in ("both", "any"):
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError("roi.auto.post_shift_direction must be one of: left_only|right_only|both")
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        dx = int(np.clip(raw_dx, -max_abs_px, max_abs_px))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    elif fallback == "fixed":
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        dx = int(round(float(w) * float(shift_ratio[0]) + float(shift_px[0])))
    else:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        dx = int(round(float(shift_px[0])))

    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
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


# 中文注释：函数 transform_rect_with_affine，用于当前步骤处理。
def transform_rect_with_affine(rect: list[int], matrix: np.ndarray) -> list[int]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x, y, w, h = [float(v) for v in rect]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    corners = np.array(
        [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]]],
        dtype=np.float32,
    )
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    transformed = cv2.transform(corners, matrix)[0]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x0 = int(round(float(np.min(transformed[:, 0]))))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    y0 = int(round(float(np.min(transformed[:, 1]))))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x1 = int(round(float(np.max(transformed[:, 0]))))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    y1 = int(round(float(np.max(transformed[:, 1]))))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return [x0, y0, max(1, x1 - x0), max(1, y1 - y0)]


# 中文注释：函数 roi_candidate_score，用于当前步骤处理。
def roi_candidate_score(frame: np.ndarray, rect: list[int]) -> float:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    h, w = frame.shape[:2]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x, y, rw, rh = clip_rect_silent(rect, w, h)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    crop = frame[y : y + rh, x : x + rw]
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if crop.size == 0:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return -1e9

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    edge_mean = float(np.mean(np.abs(lap)) / 255.0)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    green_mask = cv2.inRange(
        hsv,
        np.array([35, 35, 35], dtype=np.uint8),
        np.array([95, 255, 255], dtype=np.uint8),
    )
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    copper_mask = cv2.inRange(
        hsv,
        np.array([5, 50, 40], dtype=np.uint8),
        np.array([35, 255, 255], dtype=np.uint8),
    )
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    metal_mask = cv2.inRange(
        hsv,
        np.array([0, 0, 35], dtype=np.uint8),
        np.array([180, 70, 230], dtype=np.uint8),
    )
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    green_ratio = float(np.mean(green_mask > 0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    copper_ratio = float(np.mean(copper_mask > 0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    metal_ratio = float(np.mean(metal_mask > 0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    copper_excess = max(0.0, copper_ratio - 0.20)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    score = (
        1.25 * edge_mean
        + 0.95 * metal_ratio
        + 0.40 * (1.0 - green_ratio)
        + 0.25 * copper_ratio
        - 1.40 * copper_excess
    )
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    copper_pixels = np.argwhere(copper_mask > 0)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if len(copper_pixels) > 30:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        cy, cx = np.mean(copper_pixels, axis=0)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        cx = float(cx) / max(1.0, crop.shape[1] - 1.0)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        cy = float(cy) / max(1.0, crop.shape[0] - 1.0)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        dist = math.sqrt((cx - 0.5) ** 2 + (cy - 0.5) ** 2)
        # 中文注释（函数内）：赋值语句：在原有值基础上增量更新。
        score += 0.22 * (1.0 - min(1.0, dist * 2.4))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return score


# 中文注释：函数 refine_roi_rect，用于当前步骤处理。
def refine_roi_rect(frame: np.ndarray, init_rect: list[int], auto_cfg: dict[str, Any]) -> list[int]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    h, w = frame.shape[:2]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    base = clip_rect_silent(init_rect, w, h)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    base_x, base_y, base_w, base_h = base
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cx0 = base_x + base_w / 2.0
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cy0 = base_y + base_h / 2.0

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    search_ratio = float(auto_cfg.get("search_ratio", 0.18))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    steps = max(3, int(auto_cfg.get("search_steps", 7)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    scales_raw = auto_cfg.get("scales", [0.9, 1.0, 1.1])
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    scales: list[float] = []
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if isinstance(scales_raw, (list, tuple)):
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for s in scales_raw:
            # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
            try:
                # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                scales.append(float(s))
            except (TypeError, ValueError):
                # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
                continue
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not scales:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        scales = [1.0]

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    dx_range = np.linspace(-search_ratio * base_w, search_ratio * base_w, num=steps)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    dy_range = np.linspace(-search_ratio * base_h, search_ratio * base_h, num=steps)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    best_rect = base
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    best_score = roi_candidate_score(frame, base)
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for scale in scales:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        test_w = max(48, int(round(base_w * scale)))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        test_h = max(48, int(round(base_h * scale)))
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for dx in dx_range.tolist():
            # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
            for dy in dy_range.tolist():
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                cx = cx0 + float(dx)
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                cy = cy0 + float(dy)
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                rect = [
                    int(round(cx - test_w / 2.0)),
                    int(round(cy - test_h / 2.0)),
                    test_w,
                    test_h,
                ]
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                clipped = clip_rect_silent(rect, w, h)
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                score = roi_candidate_score(frame, clipped)
                # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
                if score > best_score:
                    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                    best_score = score
                    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                    best_rect = clipped

    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return best_rect


# 中文注释：函数 choose_reference_videos，用于当前步骤处理。
def choose_reference_videos(videos: list[Path], logger: logging.Logger) -> dict[str, Path]:
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    refs: dict[str, Path] = {}
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for path in videos:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        cap = cv2.VideoCapture(str(path))
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if not cap.isOpened():
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            cap.release()
            # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
            continue
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        cap.release()
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if width <= 0 or height <= 0:
            # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
            continue
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        ori = orientation_label(width, height)
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if ori not in refs:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            refs[ori] = path
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if "landscape" in refs and "portrait" in refs:
            # 中文注释（函数内）：流程控制：提前跳出当前循环。
            break

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if "landscape" not in refs and videos:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        refs["landscape"] = videos[0]
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if "portrait" not in refs:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        logger.warning("no portrait reference video found; portrait clips will fallback to fixed ROI")
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return refs


# 中文注释：函数 build_auto_roi_context，用于当前步骤处理。
def build_auto_roi_context(videos: list[Path], cfg: dict[str, Any], logger: logging.Logger) -> dict[str, Any]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    roi_cfg = cfg.get("roi", {}) or {}
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    mode = str(roi_cfg.get("mode", "fixed")).lower()
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    context: dict[str, Any] = {"mode": mode, "refs": {"landscape": [], "portrait": []}, "auto_cfg": roi_cfg.get("auto", {}) or {}}
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if mode != "auto_per_video":
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return context

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    auto_cfg = context["auto_cfg"]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    sample_sec = float(auto_cfg.get("sample_sec", 2.0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    sample_frames = int(auto_cfg.get("sample_frames", 15))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    orb_nfeatures = max(500, int(auto_cfg.get("orb_nfeatures", 3000)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    max_refs = max(2, int(auto_cfg.get("max_refs", 10)))

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ref_videos = choose_reference_videos(videos, logger)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    orb = cv2.ORB_create(nfeatures=orb_nfeatures)
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for ori, video_path in ref_videos.items():
        # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
        try:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            meta = probe_video(video_path)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            ref_frame = read_representative_frame(video_path, sample_sec, sample_frames)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            gray = cv2.cvtColor(ref_frame, cv2.COLOR_BGR2GRAY)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            kp, des = orb.detectAndCompute(gray, None)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            raw_rect, source = resolve_roi_rect(roi_cfg, int(meta["width"]), int(meta["height"]))
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            base_rect = clip_roi(
                roi_mode="fixed",
                roi_rect=raw_rect,
                width=int(meta["width"]),
                height=int(meta["height"]),
                logger=logger,
                video_id=f"ref_{ori}",
            )
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if len(context["refs"][ori]) > max_refs:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                context["refs"][ori] = context["refs"][ori][-max_refs:]
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            logger.info(
                "auto ROI reference[%s]: video=%s base_rect=%s keypoints=%d",
                ori,
                video_path.name,
                base_rect,
                0 if kp is None else len(kp),
            )
        except Exception as exc:  # noqa: BLE001
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            logger.warning("auto ROI reference init failed for %s (%s): %s", ori, video_path.name, exc)
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return context


# 中文注释：函数 update_auto_roi_context，用于当前步骤处理。
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
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if roi_ctx.get("mode") != "auto_per_video":
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    auto_cfg = roi_ctx.get("auto_cfg", {}) or {}
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    min_ref_score = float(auto_cfg.get("min_ref_score", 0.45))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    max_refs = max(2, int(auto_cfg.get("max_refs", 10)))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if des is None or kp is None:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if len(kp) < 80:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if roi_score < min_ref_score:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    refs = (roi_ctx.get("refs", {}) or {}).setdefault(orientation, [])
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if len(refs) > max_refs:
        # 中文注释（函数内）：删除语句：移除变量或容器项。
        del refs[0 : len(refs) - max_refs]
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.debug(
        "auto ROI reference update[%s]: pool=%d video=%s score=%.3f",
        orientation,
        len(refs),
        video_path.name,
        roi_score,
    )


# 中文注释：函数 resolve_video_roi，用于当前步骤处理。
def resolve_video_roi(
    video_path: Path,
    meta: dict[str, Any],
    cfg: dict[str, Any],
    roi_ctx: dict[str, Any],
    logger: logging.Logger,
    video_id: str,
) -> tuple[list[int], str, dict[str, Any], dict[str, Any] | None]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    roi_cfg = cfg.get("roi", {}) or {}
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    roi_mode = str(roi_cfg.get("mode", "fixed")).lower()
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    width = int(meta["width"])
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    height = int(meta["height"])
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ori = orientation_label(width, height)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    overrides = roi_cfg.get("overrides", {}) or {}
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    override_val = None
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if isinstance(overrides, dict):
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        override_val = overrides.get(str(video_id))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    override_rect = None
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if override_val is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        parsed = to_rect4(override_val)
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if parsed is not None:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            override_rect = [int(round(v)) for v in parsed]
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if override_rect is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        rect = clip_roi(
            roi_mode="fixed",
            roi_rect=override_rect,
            width=width,
            height=height,
            logger=logger,
            video_id=video_id,
        )
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        details = {"orientation": ori, "method": "override"}
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return rect, "manual_override", details, None

    # Fixed ROI path (existing behavior).
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if roi_mode != "auto_per_video":
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        raw_rect, roi_source = resolve_roi_rect(roi_cfg=roi_cfg, width=width, height=height)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        rect = clip_roi(
            roi_mode="fixed",
            roi_rect=raw_rect,
            width=width,
            height=height,
            logger=logger,
            video_id=video_id,
        )
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        details = {"orientation": ori, "method": "fixed"}
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return rect, roi_source, details, None

    # Auto per-video path.
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    auto_cfg = roi_ctx.get("auto_cfg", {}) or {}
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    sample_sec = float(auto_cfg.get("sample_sec", 2.0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    sample_frames = int(auto_cfg.get("sample_frames", 15))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ratio_test = float(auto_cfg.get("ratio_test", 0.75))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    min_matches = int(auto_cfg.get("min_matches", 20))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    min_inliers = int(auto_cfg.get("min_inliers", 12))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ransac_thresh = float(auto_cfg.get("ransac_thresh", 5.0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    orb_nfeatures = max(500, int(auto_cfg.get("orb_nfeatures", 3000)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    do_refine = bool(auto_cfg.get("refine", True))

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    frame = read_representative_frame(video_path, sample_sec=sample_sec, sample_frames=sample_frames)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    raw_rect, raw_source = resolve_roi_rect(roi_cfg=roi_cfg, width=width, height=height)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    fallback_rect = clip_roi(
        roi_mode="fixed",
        roi_rect=raw_rect,
        width=width,
        height=height,
        logger=logger,
        video_id=video_id,
    )

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    source = f"auto_per_video:fallback:{raw_source}"
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    rect = fallback_rect
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    details: dict[str, Any] = {
        "orientation": ori,
        "fallback_source": raw_source,
        "match_count": 0,
        "inlier_count": 0,
        "reference_video": None,
        "reference_pool_size": 0,
    }
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    orb = cv2.ORB_create(nfeatures=orb_nfeatures)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    kp, des = orb.detectAndCompute(gray, None)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    refs = list(((roi_ctx.get("refs", {}) or {}).get(ori) or []))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    details["reference_pool_size"] = len(refs)

    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    best: dict[str, Any] | None = None
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if des is not None and kp is not None and len(kp) > 0 and refs:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for ref in refs:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            ref_kp = ref.get("kp")
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            ref_des = ref.get("des")
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            ref_rect = ref.get("roi_rect")
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if ref_kp is None or ref_des is None or ref_rect is None:
                # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
                continue
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            knn = bf.knnMatch(ref_des, des, k=2)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            good = []
            # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
            for pair in knn:
                # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
                if len(pair) < 2:
                    # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
                    continue
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                m, n = pair
                # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
                if m.distance < ratio_test * n.distance:
                    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                    good.append(m)
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if len(good) < min_matches:
                # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
                continue

            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            src_pts = np.float32([ref_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            dst_pts = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            matrix, inliers = cv2.estimateAffinePartial2D(
                src_pts,
                dst_pts,
                method=cv2.RANSAC,
                ransacReprojThreshold=ransac_thresh,
            )
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if matrix is None:
                # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
                continue
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            inlier_count = int(np.sum(inliers)) if inliers is not None else len(good)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            candidate = {
                "match_count": len(good),
                "inlier_count": inlier_count,
                "matrix": matrix,
                "reference_video": ref.get("video_path"),
                "reference_rect": [int(v) for v in ref_rect],
            }
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if best is None:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                best = candidate
            else:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                lhs = (candidate["inlier_count"], candidate["match_count"])
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                rhs = (best["inlier_count"], best["match_count"])
                # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
                if lhs > rhs:
                    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                    best = candidate
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    elif not refs:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        logger.warning("[%s] auto ROI reference unavailable for %s, fallback used", video_id, ori)
    else:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        logger.warning("[%s] auto ROI descriptor extraction failed, fallback used", video_id)

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if best is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        details["match_count"] = int(best["match_count"])
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        details["inlier_count"] = int(best["inlier_count"])
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        details["reference_video"] = best["reference_video"]
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if int(best["inlier_count"]) >= min_inliers:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            mapped = transform_rect_with_affine(best["reference_rect"], best["matrix"])
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            rect = clip_rect_silent(mapped, width, height)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            ref_id = Path(str(best["reference_video"])).stem if best["reference_video"] else "ref"
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            source = f"auto_per_video:orb:{ori}:{ref_id}"
        else:
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            logger.warning(
                "[%s] auto ROI low inliers (%d), fallback used",
                video_id,
                int(best["inlier_count"]),
            )
    else:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        logger.warning("[%s] auto ROI no valid match, fallback used", video_id)

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if do_refine:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        refined = refine_roi_rect(frame, rect, auto_cfg)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        rect = clip_rect_silent(refined, width, height)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        source = f"{source}+refine"

    # Optional post-shift to compensate systematic composition bias.
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    req_dx, req_dy, shift_details = resolve_post_shift(frame, rect, auto_cfg)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if req_dx != 0 or req_dy != 0:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        prev_rect = [int(v) for v in rect]
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        rect = shift_rect_keep_size(rect, req_dx, req_dy, width, height)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        applied_dx = int(rect[0] - prev_rect[0])
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        applied_dy = int(rect[1] - prev_rect[1])
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        source = f"{source}+shift"
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        shift_details["dx_req"] = int(req_dx)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        shift_details["dy_req"] = int(req_dy)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        shift_details["dx_applied"] = int(applied_dx)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        shift_details["dy_applied"] = int(applied_dy)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        shift_details["clamped"] = bool(applied_dx != req_dx or applied_dy != req_dy)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    details["post_shift"] = shift_details

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    roi_score = roi_candidate_score(frame, rect)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    details["roi_score"] = float(roi_score)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    details["feature_count"] = 0 if kp is None else int(len(kp))

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ref_payload = {
        "orientation": ori,
        "kp": kp,
        "des": des,
        "roi_score": float(roi_score),
    }
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return rect, source, details, ref_payload


# 中文注释：函数 read_gray_roi，用于当前步骤处理。
def read_gray_roi(
    cap: cv2.VideoCapture,
    frame_idx: int,
    roi_rect: list[int],
    resize_w: int,
) -> np.ndarray | None:
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frame_idx))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ok, frame = cap.read()
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not ok or frame is None:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x, y, w, h = roi_rect
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    crop = frame[y : y + h, x : x + w]
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if crop.size == 0:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if resize_w > 0 and gray.shape[1] > resize_w:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        out_h = max(1, int(round(gray.shape[0] * resize_w / gray.shape[1])))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        gray = cv2.resize(gray, (resize_w, out_h), interpolation=cv2.INTER_AREA)
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return gray.astype(np.float32)


# 中文注释：函数 ssim_global，用于当前步骤处理。
def ssim_global(img1: np.ndarray, img2: np.ndarray) -> float:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if img1.shape != img2.shape:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise ValueError("SSIM input shape mismatch")
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    c1 = (0.01 * 255) ** 2
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    c2 = (0.03 * 255) ** 2
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    mu1 = float(np.mean(img1))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    mu2 = float(np.mean(img2))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    var1 = float(np.var(img1))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    var2 = float(np.var(img2))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cov12 = float(np.mean((img1 - mu1) * (img2 - mu2)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    numerator = (2 * mu1 * mu2 + c1) * (2 * cov12 + c2)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    denominator = (mu1 * mu1 + mu2 * mu2 + c1) * (var1 + var2 + c2)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if denominator <= 0:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return 1.0 if np.allclose(img1, img2) else 0.0
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    value = numerator / denominator
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return float(max(-1.0, min(1.0, value)))


# 中文注释：函数 sample_points，用于当前步骤处理。
def sample_points(duration_sec: float, fps: float, coarse_fps: float, frame_count: int) -> tuple[np.ndarray, np.ndarray]:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if coarse_fps <= 0:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("coarse_fps must be > 0")
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    max_index = max(frame_count - 1, 0)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    n = max(2, int(math.floor(duration_sec * coarse_fps)) + 1)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    t = np.linspace(0.0, duration_sec, num=n, endpoint=True)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    idx = np.clip(np.rint(t * fps).astype(np.int64), 0, max_index)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    uniq_idx, uniq_pos = np.unique(idx, return_index=True)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    uniq_t = t[uniq_pos]
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return uniq_idx, uniq_t


# 中文注释：函数 compute_score_curve，用于当前步骤处理。
def compute_score_curve(
    video_path: Path,
    meta: dict[str, Any],
    roi_rect: list[int],
    method: str,
    coarse_fps: float,
    resize_w: int,
    ssim_ref_n_ref: int,
) -> tuple[np.ndarray, np.ndarray]:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if method == "flow":
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("method=flow is planned for v2; MVP supports ssim_ref/diff_prev")
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if method not in ("ssim_ref", "diff_prev"):
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError(f"Unsupported method: {method}")

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    idx_arr, t_arr = sample_points(meta["duration_sec"], meta["fps"], coarse_fps, meta["frame_count"])
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cap = cv2.VideoCapture(str(video_path))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not cap.isOpened():
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("VideoCapture open failed in coarse scan")

    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    scores: list[float] = []
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    ref_frame: np.ndarray | None = None
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    prev_frame: np.ndarray | None = None
    # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
    try:
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if method == "ssim_ref":
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            n_ref = max(1, int(ssim_ref_n_ref))
            # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
            ref_samples: list[np.ndarray] = []
            # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
            for idx in idx_arr[:n_ref]:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                gray = read_gray_roi(cap, int(idx), roi_rect, resize_w)
                # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
                if gray is not None:
                    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                    ref_samples.append(gray)
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if not ref_samples:
                # 中文注释（函数内）：抛出异常：向上层传递错误信号。
                raise PipelineError("No valid reference frame for ssim_ref")
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if len(ref_samples) == 1:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                ref_frame = ref_samples[0]
            else:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                ref_frame = np.median(np.stack(ref_samples, axis=0), axis=0).astype(np.float32)

        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for idx in idx_arr:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            gray = read_gray_roi(cap, int(idx), roi_rect, resize_w)
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if gray is None:
                # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                scores.append(float("nan"))
                # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
                continue
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if method == "ssim_ref":
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                ssim_val = ssim_global(ref_frame, gray)
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                score = 1.0 - ssim_val
            else:
                # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
                if prev_frame is None:
                    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                    score = 0.0
                else:
                    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                    score = float(np.mean(np.abs(gray - prev_frame)) / 255.0)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            prev_frame = gray
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            scores.append(float(score))
    finally:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        cap.release()

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    score_arr = np.array(scores, dtype=np.float64)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    valid = np.isfinite(score_arr)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not np.any(valid):
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("No valid score in score_curve")
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not np.all(valid):
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        finite_mean = float(np.nanmean(score_arr))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        score_arr[~valid] = finite_mean
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return t_arr.astype(np.float64), score_arr


# 中文注释：函数 smooth_series，用于当前步骤处理。
def smooth_series(values: np.ndarray, win: int) -> np.ndarray:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if win <= 1 or len(values) <= 1:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return values.copy()
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    win = min(int(win), len(values))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    kernel = np.ones(win, dtype=np.float64) / float(win)
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return np.convolve(values, kernel, mode="same")


# 中文注释：函数 find_runs，用于当前步骤处理。
def find_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    runs: list[tuple[int, int]] = []
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    start = None
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for i, is_on in enumerate(mask.tolist()):
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if is_on and start is None:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            start = i
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        elif not is_on and start is not None:
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            runs.append((start, i - 1))
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            start = None
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if start is not None:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        runs.append((start, len(mask) - 1))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return runs


# 中文注释：函数 find_local_peaks，用于当前步骤处理。
def find_local_peaks(values: np.ndarray) -> list[int]:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if len(values) == 0:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return []
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if len(values) == 1:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return [0]
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    peaks: list[int] = []
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for i in range(len(values)):
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        left = values[i - 1] if i > 0 else -np.inf
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        right = values[i + 1] if i < len(values) - 1 else -np.inf
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if values[i] >= left and values[i] >= right:
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            peaks.append(i)
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return peaks


# 中文注释：函数 centered_segment，用于当前步骤处理。
def centered_segment(center_t: float, seg_len: float, duration: float) -> tuple[float, float]:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if duration <= 0:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return 0.0, 0.0
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    seg_len = max(0.1, float(seg_len))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    start = max(0.0, center_t - seg_len / 2.0)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    end = min(duration, start + seg_len)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    start = max(0.0, end - seg_len)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if end <= start:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return 0.0, duration
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return float(start), float(end)


# 中文注释：函数 detect_segments，用于当前步骤处理。
def detect_segments(
    t_arr: np.ndarray,
    score_arr: np.ndarray,
    duration_sec: float,
    cfg: dict[str, Any],
    logger: logging.Logger,
    video_id: str,
) -> dict[str, Any]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    smooth_win = int(cfg.get("smooth_win", 5))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    segment_len_sec = float(cfg.get("segment_len_sec", 8.0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    merge_gap_sec = float(cfg.get("merge_gap_sec", 0.5))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    segment_strategy = str(cfg.get("segment_strategy", "peak")).lower()
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if segment_strategy not in ("peak", "run"):
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError(f"Unsupported segment_strategy: {segment_strategy}")
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    run_pre_pad_sec = max(0.0, float(cfg.get("run_pre_pad_sec", 1.0)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    run_post_pad_sec = max(0.0, float(cfg.get("run_post_pad_sec", 1.5)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    run_max_len_sec = float(cfg.get("run_max_len_sec", 20.0))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if run_max_len_sec < 0:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        run_max_len_sec = 0.0
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    topk = int(cfg.get("topk", 3))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    topk = max(1, topk)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    fill_to_topk = bool(cfg.get("fill_to_topk", True))

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    smoothed = smooth_series(score_arr, smooth_win)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    threshold_cfg = cfg.get("threshold", {}) or {}
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    threshold_mode = str(threshold_cfg.get("mode", "quantile")).lower()

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if threshold_mode == "quantile":
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        q = float(threshold_cfg.get("q", 0.995))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        q = min(0.999999, max(0.0, q))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        threshold_value = float(np.quantile(smoothed, q))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        threshold_meta = {"mode": "quantile", "q": q, "value": threshold_value}
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    elif threshold_mode == "mad":
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        k = float(threshold_cfg.get("mad_k", 6.0))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        median = float(np.median(smoothed))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        mad = float(np.median(np.abs(smoothed - median)))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        threshold_value = float(median + k * mad)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        threshold_meta = {
            "mode": "mad",
            "median": median,
            "mad": mad,
            "mad_k": k,
            "value": threshold_value,
        }
    else:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError(f"Unsupported threshold mode: {threshold_mode}")
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    threshold_meta["segment_strategy"] = segment_strategy
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if segment_strategy == "run":
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        threshold_meta["run_pre_pad_sec"] = run_pre_pad_sec
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        threshold_meta["run_post_pad_sec"] = run_post_pad_sec
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        threshold_meta["run_max_len_sec"] = run_max_len_sec

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    runs = find_runs(smoothed >= threshold_value)
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    raw_segments: list[dict[str, Any]] = []

    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for run_start, run_end in runs:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        local = smoothed[run_start : run_end + 1]
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        peak_idx = int(run_start + np.argmax(local))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        peak_t = float(t_arr[peak_idx])
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        peak_score = float(score_arr[peak_idx])
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        peak_smooth = float(smoothed[peak_idx])
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if segment_strategy == "run":
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            run_start_t = float(t_arr[run_start])
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            run_end_t = float(t_arr[run_end])
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if run_end_t < run_start_t:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                run_start_t, run_end_t = run_end_t, run_start_t
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            start = max(0.0, run_start_t - run_pre_pad_sec)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            end = min(duration_sec, run_end_t + run_post_pad_sec)
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if end - start < segment_len_sec:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                c_start, c_end = centered_segment(peak_t, segment_len_sec, duration_sec)
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                start = max(0.0, min(start, c_start))
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                end = min(duration_sec, max(end, c_end))
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if run_max_len_sec > 0.0 and (end - start) > run_max_len_sec:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                start, end = centered_segment(peak_t, run_max_len_sec, duration_sec)
        else:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            start, end = centered_segment(peak_t, segment_len_sec, duration_sec)
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not raw_segments:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        peak_idx = int(np.argmax(smoothed))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        peak_t = float(t_arr[peak_idx])
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        start, end = centered_segment(peak_t, segment_len_sec, duration_sec)
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        logger.warning("[%s] no run above threshold, using fallback around global peak", video_id)

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if fill_to_topk and len(raw_segments) < topk:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        peak_indices = find_local_peaks(smoothed)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        peak_indices = sorted(peak_indices, key=lambda i: float(smoothed[i]), reverse=True)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        min_peak_gap = max(merge_gap_sec, segment_len_sec + merge_gap_sec)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        existing_peak_ts = [float(seg["peak_t_sec"]) for seg in raw_segments]

        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for peak_idx in peak_indices:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            peak_t = float(t_arr[peak_idx])
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if any(abs(peak_t - t0) < min_peak_gap for t0 in existing_peak_ts):
                # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
                continue
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            start, end = centered_segment(peak_t, segment_len_sec, duration_sec)
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            existing_peak_ts.append(peak_t)
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if len(raw_segments) >= topk:
                # 中文注释（函数内）：流程控制：提前跳出当前循环。
                break

        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if len(raw_segments) > 0 and len(raw_segments) < topk:
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            logger.warning(
                "[%s] only found %d candidate segment(s) after supplementation (topk=%d)",
                video_id,
                len(raw_segments),
                topk,
            )

    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    raw_segments.sort(key=lambda s: s["start_sec"])
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    merged: list[dict[str, Any]] = []
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for seg in raw_segments:
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if not merged:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            seg_copy = dict(seg)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            seg_copy["merged_count"] = 1
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            merged.append(seg_copy)
            # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
            continue
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        prev = merged[-1]
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if seg["start_sec"] <= prev["end_sec"] + merge_gap_sec:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            prev["start_sec"] = min(prev["start_sec"], seg["start_sec"])
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            prev["end_sec"] = max(prev["end_sec"], seg["end_sec"])
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            prev["fallback"] = bool(prev["fallback"] and seg["fallback"])
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            prev["merged_count"] = int(prev["merged_count"]) + 1
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if seg["peak_smooth_score"] > prev["peak_smooth_score"]:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                prev["peak_smooth_score"] = seg["peak_smooth_score"]
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                prev["peak_score"] = seg["peak_score"]
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                prev["peak_t_sec"] = seg["peak_t_sec"]
        else:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            seg_copy = dict(seg)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            seg_copy["merged_count"] = 1
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            merged.append(seg_copy)

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if segment_strategy == "peak":
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for seg in merged:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            seg["start_sec"], seg["end_sec"] = centered_segment(
                float(seg["peak_t_sec"]),
                segment_len_sec,
                duration_sec,
            )
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    elif run_max_len_sec > 0.0:
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for seg in merged:
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if float(seg["end_sec"]) - float(seg["start_sec"]) > run_max_len_sec:
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                seg["start_sec"], seg["end_sec"] = centered_segment(
                    float(seg["peak_t_sec"]),
                    run_max_len_sec,
                    duration_sec,
                )

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ranked = sorted(merged, key=lambda s: s["peak_smooth_score"], reverse=True)[:topk]
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ranked.sort(key=lambda s: s["start_sec"])

    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    segments: list[dict[str, Any]] = []
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for i, seg in enumerate(ranked, start=1):
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        seg["start_sec"] = float(max(0.0, min(duration_sec, seg["start_sec"])))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        seg["end_sec"] = float(max(0.0, min(duration_sec, seg["end_sec"])))
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if seg["end_sec"] <= seg["start_sec"]:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            seg["start_sec"], seg["end_sec"] = centered_segment(seg["peak_t_sec"], segment_len_sec, duration_sec)
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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

    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return {
        "threshold": threshold_meta,
        "segments": segments,
        "smoothed_scores": smoothed,
    }


# 中文注释：函数 qscale_to_jpeg_quality，用于当前步骤处理。
def qscale_to_jpeg_quality(q: int) -> int:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    q = max(2, min(31, int(q)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    quality = int(round(100 - (q - 2) * (80.0 / 29.0)))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return max(20, min(100, quality))


# 中文注释：函数 image_write，用于当前步骤处理。
def image_write(path: Path, image: np.ndarray, ext: str, jpg_q: int) -> None:
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(path.parent)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ext = ext.lower()
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if ext == "png":
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        ok = cv2.imwrite(str(path), image, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    else:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        quality = qscale_to_jpeg_quality(jpg_q)
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        ok = cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not ok:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError(f"Failed to write image: {path}")


# 中文注释：函数 parse_crop_resize，用于当前步骤处理。
def parse_crop_resize(value: Any) -> tuple[int, int] | None:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if value is None:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if isinstance(value, bool):
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if value:
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError("frame_extraction.crop_resize=true is invalid; use int or [w,h]")
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if isinstance(value, (int, float)):
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        size = int(round(float(value)))
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if size <= 0:
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError("frame_extraction.crop_resize must be > 0")
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return size, size
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if isinstance(value, str):
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        text = value.strip().lower()
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if text in ("", "none", "null", "off"):
            # 中文注释（函数内）：返回结果：结束当前函数并输出值。
            return None
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if text.isdigit():
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            size = int(text)
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if size <= 0:
                # 中文注释（函数内）：抛出异常：向上层传递错误信号。
                raise PipelineError("frame_extraction.crop_resize must be > 0")
            # 中文注释（函数内）：返回结果：结束当前函数并输出值。
            return size, size
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        match = re.match(r"^(\d+)\s*[x,]\s*(\d+)$", text)
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if match:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            w = int(match.group(1))
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            h = int(match.group(2))
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if w <= 0 or h <= 0:
                # 中文注释（函数内）：抛出异常：向上层传递错误信号。
                raise PipelineError("frame_extraction.crop_resize dimensions must be > 0")
            # 中文注释（函数内）：返回结果：结束当前函数并输出值。
            return w, h
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("frame_extraction.crop_resize string must be '640' or '640x640'")
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if isinstance(value, (list, tuple)) and len(value) == 2:
        # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
        try:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            w = int(round(float(value[0])))
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            h = int(round(float(value[1])))
        except (TypeError, ValueError) as exc:
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError("frame_extraction.crop_resize must contain numbers") from exc
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if w <= 0 or h <= 0:
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError("frame_extraction.crop_resize dimensions must be > 0")
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return w, h
    # 中文注释（函数内）：抛出异常：向上层传递错误信号。
    raise PipelineError("frame_extraction.crop_resize must be null, int, 'WxH', or [w,h]")


# 中文注释：函数 resize_crop_image，用于当前步骤处理。
def resize_crop_image(
    crop: np.ndarray,
    target_size: tuple[int, int] | None,
    mode: str,
    pad_value: int,
) -> np.ndarray:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if target_size is None:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return crop
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    target_w, target_h = target_size
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    src_h, src_w = crop.shape[:2]
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if src_w == target_w and src_h == target_h:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return crop

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    interp = cv2.INTER_AREA if target_w < src_w or target_h < src_h else cv2.INTER_LINEAR
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    mode = str(mode).strip().lower()
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if mode == "stretch":
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return cv2.resize(crop, (target_w, target_h), interpolation=interp)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if mode != "letterbox":
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("frame_extraction.crop_resize_mode must be 'letterbox' or 'stretch'")

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    scale = min(target_w / max(1.0, float(src_w)), target_h / max(1.0, float(src_h)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    new_w = max(1, int(round(src_w * scale)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    new_h = max(1, int(round(src_h * scale)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    resized = cv2.resize(crop, (new_w, new_h), interpolation=interp)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    fill = int(np.clip(int(pad_value), 0, 255))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if crop.ndim == 2:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        canvas = np.full((target_h, target_w), fill, dtype=crop.dtype)
    else:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        canvas = np.full((target_h, target_w, crop.shape[2]), fill, dtype=crop.dtype)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    x0 = (target_w - new_w) // 2
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    y0 = (target_h - new_h) // 2
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return canvas


# 中文注释：函数 resolve_frame_name_id，用于当前步骤处理。
def resolve_frame_name_id(
    local_seq_idx: int,
    naming_state: dict[str, Any],
) -> tuple[str, int]:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    mode = str(naming_state.get("mode", "per_segment")).strip().lower()
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if mode == "global":
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        seq_idx = int(naming_state.get("next_index", 0))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        naming_state["next_index"] = seq_idx + 1
    else:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        seq_idx = int(local_seq_idx)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if seq_idx < 0:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("frame_extraction.global_start_index must be >= 0")
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return f"{seq_idx:06d}", seq_idx


# 中文注释：函数 extraction_points，用于当前步骤处理。
def extraction_points(
    start_sec: float,
    end_sec: float,
    fine_fps: float,
    src_fps: float,
    frame_count: int,
) -> tuple[list[int], list[float]]:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if fine_fps <= 0:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("fine_fps must be > 0")
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if end_sec <= start_sec:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        end_sec = start_sec + 1.0 / max(1.0, fine_fps)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    times = np.arange(start_sec, end_sec + 1e-9, 1.0 / fine_fps, dtype=np.float64)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if len(times) == 0:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        times = np.array([start_sec], dtype=np.float64)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    max_idx = max(0, frame_count - 1)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    idx = np.clip(np.rint(times * src_fps).astype(np.int64), 0, max_idx)

    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    uniq_idx: list[int] = []
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    uniq_t: list[float] = []
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    prev = None
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for f_idx, t_sec in zip(idx.tolist(), times.tolist()):
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if prev is not None and f_idx == prev:
            # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
            continue
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        uniq_idx.append(int(f_idx))
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        uniq_t.append(float(t_sec))
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        prev = int(f_idx)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not uniq_idx:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        uniq_idx = [int(np.clip(round(start_sec * src_fps), 0, max_idx))]
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        uniq_t = [float(start_sec)]
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return uniq_idx, uniq_t


# 中文注释：函数 write_manifest，用于当前步骤处理。
def write_manifest(manifest_path: Path, rows: list[dict[str, Any]]) -> None:
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(manifest_path.parent)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    fieldnames = [
        "frame_id",
        "t_sec",
        "source_frame_idx",
        "frame_path",
        "crop_path",
        "roi_rect",
    ]
    # 中文注释（函数内）：上下文管理：在受控资源作用域内执行。
    with manifest_path.open("w", encoding="utf-8", newline="") as f:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        writer.writeheader()
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for row in rows:
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            writer.writerow(row)


# 中文注释：函数 build_contact_sheet，用于当前步骤处理。
def build_contact_sheet(
    frame_paths: list[Path],
    tile_cols: int,
    tile_rows: int,
) -> np.ndarray | None:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not frame_paths:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    imgs: list[np.ndarray] = []
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for p in frame_paths:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        img = cv2.imread(str(p))
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if img is not None:
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            imgs.append(img)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not imgs:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return None

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    h0, w0 = imgs[0].shape[:2]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    tile_count = tile_cols * tile_rows
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    canvas: list[np.ndarray] = []
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for img in imgs[:tile_count]:
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if img.shape[:2] != (h0, w0):
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            img = cv2.resize(img, (w0, h0), interpolation=cv2.INTER_AREA)
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        canvas.append(img)
    # 中文注释（函数内）：循环处理：在条件成立时重复执行。
    while len(canvas) < tile_count:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        canvas.append(np.zeros((h0, w0, 3), dtype=np.uint8))

    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    rows: list[np.ndarray] = []
    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for r in range(tile_rows):
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        start = r * tile_cols
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        rows.append(np.hstack(canvas[start : start + tile_cols]))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return np.vstack(rows)


# 中文注释：函数 choose_preview_frames，用于当前步骤处理。
def choose_preview_frames(
    extracted_abs_paths: list[Path],
    segment_len_sec: float,
    contact_sheet_fps: float,
    slots: int,
) -> list[Path]:
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not extracted_abs_paths:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return []
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    target_count = int(math.ceil(max(0.1, segment_len_sec) * max(0.1, contact_sheet_fps)))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    target_count = max(1, min(slots, target_count, len(extracted_abs_paths)))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if target_count <= 1:
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return [extracted_abs_paths[0]]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    idx = np.linspace(0, len(extracted_abs_paths) - 1, num=target_count, endpoint=True)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    idx = np.unique(np.rint(idx).astype(np.int64))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return [extracted_abs_paths[int(i)] for i in idx.tolist()]


# 中文注释：函数 extract_segment，用于当前步骤处理。
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
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    segment_id = seg["segment_id"]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    seg_dir = video_out_dir / segment_id
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    frames_dir = seg_dir / "frames"
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    crops_dir = seg_dir / "crops"
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(frames_dir)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if frame_cfg.get("crop_output", True):
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        ensure_dir(crops_dir)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    ext = str(frame_cfg.get("image_ext", "jpg")).lower()
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if ext not in ("jpg", "jpeg", "png"):
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        ext = "jpg"
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if ext == "jpeg":
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        ext = "jpg"
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    jpg_q = int(frame_cfg.get("jpg_quality_q", 2))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    crop_resize = parse_crop_resize(frame_cfg.get("crop_resize"))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    crop_resize_mode = str(frame_cfg.get("crop_resize_mode", "letterbox"))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    crop_resize_pad_value = int(frame_cfg.get("crop_resize_pad_value", 114))

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    fine_fps = float(frame_cfg.get("fine_fps", 10.0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    idx_points, t_points = extraction_points(
        start_sec=float(seg["start_sec"]),
        end_sec=float(seg["end_sec"]),
        fine_fps=fine_fps,
        src_fps=float(meta["fps"]),
        frame_count=int(meta["frame_count"]),
    )

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cap = cv2.VideoCapture(str(video_path))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not cap.isOpened():
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("VideoCapture open failed in extraction")

    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    rows: list[dict[str, Any]] = []
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    extracted_abs: list[Path] = []
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    extracted_crop_abs: list[Path] = []
    # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
    try:
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for i, (src_idx, req_t) in enumerate(zip(idx_points, t_points), start=1):
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(src_idx))
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            ok, frame = cap.read()
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if not ok or frame is None:
                # 中文注释（函数内）：流程控制：跳过当前轮循环的剩余语句。
                continue
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            frame_id_str, _seq_idx = resolve_frame_name_id(i, naming_state)
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            frame_name = f"{frame_id_str}.{ext}"
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            frame_abs = frames_dir / frame_name
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            image_write(frame_abs, frame, ext, jpg_q)
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            extracted_abs.append(frame_abs)

            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            crop_rel = ""
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if frame_cfg.get("crop_output", True):
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                x, y, w, h = roi_rect
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                crop = frame[y : y + h, x : x + w]
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                crop = resize_crop_image(
                    crop=crop,
                    target_size=crop_resize,
                    mode=crop_resize_mode,
                    pad_value=crop_resize_pad_value,
                )
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                crop_abs = crops_dir / frame_name
                # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                image_write(crop_abs, crop, ext, jpg_q)
                # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                extracted_crop_abs.append(crop_abs)
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                crop_rel = Path("crops") / frame_name
                # 中文注释（函数内）：赋值语句：保存或更新中间变量。
                crop_rel = crop_rel.as_posix()

            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        cap.release()

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not rows:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError(f"{segment_id}: extracted 0 frame")

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    manifest_path = seg_dir / "manifest.csv"
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    write_manifest(manifest_path, rows)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    tile = report_cfg.get("tile", [4, 3])
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not isinstance(tile, (list, tuple)) or len(tile) != 2:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        tile = [4, 3]
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    tile_cols = max(1, int(tile[0]))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    tile_rows = max(1, int(tile[1]))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    slots = tile_cols * tile_rows
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    contact_sheet_fps = float(report_cfg.get("contact_sheet_fps", 1.0))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    preview_source = extracted_crop_abs if extracted_crop_abs else extracted_abs
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    selected = choose_preview_frames(
        preview_source,
        float(seg["end_sec"] - seg["start_sec"]),
        contact_sheet_fps,
        slots,
    )
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    preview_path = None
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    preview = build_contact_sheet(selected, tile_cols, tile_rows)
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if preview is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        preview_path = seg_dir / "preview.jpg"
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        image_write(preview_path, preview, "jpg", 2)

    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return seg_dir, manifest_path, rows, preview_path


# 中文注释：函数 score_csv_write，用于当前步骤处理。
def score_csv_write(
    out_path: Path,
    t_arr: np.ndarray,
    score_arr: np.ndarray,
    smooth_arr: np.ndarray,
) -> None:
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(out_path.parent)
    # 中文注释（函数内）：上下文管理：在受控资源作用域内执行。
    with out_path.open("w", encoding="utf-8", newline="") as f:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        writer = csv.writer(f)
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        writer.writerow(["t_sec", "score", "score_smooth"])
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for t, s, ss in zip(t_arr.tolist(), score_arr.tolist(), smooth_arr.tolist()):
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            writer.writerow([f"{t:.6f}", f"{s:.8f}", f"{ss:.8f}"])


# 中文注释：函数 make_index_html，用于当前步骤处理。
def make_index_html(run_dir: Path, rows: list[dict[str, Any]]) -> None:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
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

    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for row in rows:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        preview_html = "-"
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        preview_rel = row.get("preview_rel")
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if preview_rel:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            preview_html = (
                f"<a href='{html.escape(preview_rel)}' target='_blank'>"
                f"<img src='{html.escape(preview_rel)}' loading='lazy'/></a>"
            )
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        lines.append(
            "<tr>"
            f"<td>{html.escape(str(row['video_id']))}</td>"
            f"<td>{html.escape(str(row['segment_id']))}</td>"
            f"<td>{float(row['start_sec']):.3f} - {float(row['end_sec']):.3f}</td>"
            f"<td>{html.escape(str(row['method']))}</td>"
            f"<td>{preview_html}</td>"
            "</tr>"
        )

    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    lines.extend(["</tbody>", "</table>", "</body>", "</html>"])
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    (run_dir / "index.html").write_text("\n".join(lines), encoding="utf-8")


# 中文注释：函数 process_video，用于当前步骤处理。
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
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    video_id = video_path.stem
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    video_out_dir = out_root / video_id
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(video_out_dir)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(video_out_dir / "metrics")

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    meta = probe_video(video_path)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    meta["video_id"] = video_id
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    write_json(video_out_dir / "meta.json", meta)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    roi_rect, roi_source, roi_details, roi_ref_payload = resolve_video_roi(
        video_path=video_path,
        meta=meta,
        cfg=cfg,
        roi_ctx=roi_ctx,
        logger=logger,
        video_id=video_id,
    )

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    seg_cfg = cfg.get("segment_detection", {})
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    method = str(seg_cfg.get("method", "ssim_ref")).lower()
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    t_arr, score_arr = compute_score_curve(
        video_path=video_path,
        meta=meta,
        roi_rect=roi_rect,
        method=method,
        coarse_fps=float(seg_cfg.get("coarse_fps", 2.0)),
        resize_w=int(seg_cfg.get("resize_w", 320)),
        ssim_ref_n_ref=int(seg_cfg.get("ssim_ref_n_ref", 5)),
    )

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    detected = detect_segments(
        t_arr=t_arr,
        score_arr=score_arr,
        duration_sec=float(meta["duration_sec"]),
        cfg=seg_cfg,
        logger=logger,
        video_id=video_id,
    )

    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    score_csv_write(video_out_dir / "metrics" / "score_curve.csv", t_arr, score_arr, detected["smoothed_scores"])

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
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
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    write_json(video_out_dir / "segments.json", segments_json)

    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if roi_ref_payload is not None:
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if dry_run:
        # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
        for seg in detected["segments"]:
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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
        # 中文注释（函数内）：返回结果：结束当前函数并输出值。
        return

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    frame_cfg = cfg.get("frame_extraction", {})
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    report_cfg = cfg.get("report", {})

    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for seg in detected["segments"]:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
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
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if frame_cfg.get("crop_output", True):
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            frames_count = len(list((seg_dir / "frames").glob("*")))
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            crops_count = len(list((seg_dir / "crops").glob("*")))
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if frames_count != crops_count:
                # 中文注释（函数内）：抛出异常：向上层传递错误信号。
                raise PipelineError(f"{video_id}/{seg['segment_id']}: frames({frames_count}) != crops({crops_count})")
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if not rows:
            # 中文注释（函数内）：抛出异常：向上层传递错误信号。
            raise PipelineError(f"{video_id}/{seg['segment_id']}: empty manifest")

        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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


# 中文注释：函数 run，用于当前步骤处理。
def run() -> int:
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    args = parse_args()
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cfg = load_config(args.config)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cfg = apply_cli_overrides(cfg, args)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    input_dir = Path(cfg["input_dir"]).expanduser().resolve()
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    output_dir = Path(cfg["output_dir"]).expanduser().resolve()
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    run_dir = output_dir / "_run"
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    ensure_dir(run_dir)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    logger = setup_logging(run_dir, str(cfg.get("runtime", {}).get("log_level", "INFO")))
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    write_versions(run_dir)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cfg["input_dir"] = str(input_dir)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    cfg["output_dir"] = str(output_dir)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    write_yaml(run_dir / "config_resolved.yaml", cfg)

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    fail_fast = bool(cfg.get("runtime", {}).get("fail_fast", False))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    dry_run = bool(cfg.get("runtime", {}).get("dry_run", False))
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    frame_cfg = cfg.get("frame_extraction", {}) or {}
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    naming_mode = str(frame_cfg.get("naming_mode", "per_segment")).strip().lower()
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if naming_mode not in ("per_segment", "global"):
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("frame_extraction.naming_mode must be 'per_segment' or 'global'")
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    global_start_index = int(frame_cfg.get("global_start_index", 0))
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if global_start_index < 0:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError("frame_extraction.global_start_index must be >= 0")
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    naming_state: dict[str, Any] = {
        "mode": naming_mode,
        "next_index": int(global_start_index),
    }

    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    failures: list[dict[str, Any]] = []
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    report_rows: list[dict[str, Any]] = []
    # 中文注释（函数内）：赋值语句：带类型标注地初始化变量。
    roi_rows: list[dict[str, Any]] = []

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    videos = list_videos(input_dir)
    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    max_videos = cfg.get("runtime", {}).get("max_videos")
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if max_videos is not None:
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        max_videos = int(max_videos)
        # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
        if max_videos > 0:
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            videos = videos[:max_videos]
    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if not videos:
        # 中文注释（函数内）：抛出异常：向上层传递错误信号。
        raise PipelineError(f"No mp4 files found in {input_dir}")

    # 中文注释（函数内）：赋值语句：保存或更新中间变量。
    roi_ctx = build_auto_roi_context(videos, cfg, logger)

    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.info("pipeline start: videos=%d dry_run=%s", len(videos), dry_run)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.info("input_dir=%s", input_dir)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.info("output_dir=%s", output_dir)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.info("roi_mode=%s", str(cfg.get("roi", {}).get("mode", "fixed")))
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.info(
        "frame_naming_mode=%s start_index=%d",
        naming_mode,
        global_start_index if naming_mode == "global" else 1,
    )

    # 中文注释（函数内）：循环处理：遍历集合并逐项执行。
    for idx, video_path in enumerate(videos, start=1):
        # 中文注释（函数内）：赋值语句：保存或更新中间变量。
        video_id = video_path.stem
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        logger.info("[%d/%d] processing %s", idx, len(videos), video_path.name)
        # 中文注释（函数内）：异常处理：包装风险操作并执行兼容处理。
        try:
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
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
            # 中文注释（函数内）：赋值语句：保存或更新中间变量。
            err = {
                "video_id": video_id,
                "video_path": str(video_path),
                "error": str(exc),
                "traceback_tail": "\n".join(traceback.format_exc().strip().splitlines()[-10:]),
            }
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            failures.append(err)
            # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
            logger.error("[%s] failed: %s", video_id, exc)
            # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
            if fail_fast:
                # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                write_json(run_dir / "failures.json", failures)
                # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                write_json(run_dir / "roi_overrides.json", roi_rows)
                # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
                if cfg.get("report", {}).get("enable_html", True):
                    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
                    make_index_html(run_dir, report_rows)
                # 中文注释（函数内）：返回结果：结束当前函数并输出值。
                return 1

    # 中文注释（函数内）：条件分支：根据条件选择后续处理路径。
    if cfg.get("report", {}).get("enable_html", True):
        # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
        make_index_html(run_dir, report_rows)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    write_json(run_dir / "failures.json", failures)
    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    write_json(run_dir / "roi_overrides.json", roi_rows)

    # 中文注释（函数内）：表达式语句：执行调用或产生副作用。
    logger.info("pipeline done: success=%d failure=%d", len(videos) - len(failures), len(failures))
    # 中文注释（函数内）：返回结果：结束当前函数并输出值。
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except PipelineError as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        raise SystemExit(2)
