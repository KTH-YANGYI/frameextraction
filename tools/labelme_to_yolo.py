#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class YoloBox:
    cls: int
    xc: float
    yc: float
    w: float
    h: float

    def to_line(self) -> str:
        return f"{self.cls} {self.xc:.6f} {self.yc:.6f} {self.w:.6f} {self.h:.6f}"


def _clamp01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def _bbox_from_points(points: Iterable[Iterable[float]]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for pt in points:
        if len(pt) < 2:
            continue
        xs.append(float(pt[0]))
        ys.append(float(pt[1]))
    if not xs or not ys:
        raise ValueError("no valid points")
    x_min = min(xs)
    y_min = min(ys)
    x_max = max(xs)
    y_max = max(ys)
    return x_min, y_min, x_max, y_max


def _shape_to_yolo_box(
    shape: dict,
    class_to_id: dict[str, int],
    img_w: float,
    img_h: float,
) -> YoloBox | None:
    label = shape.get("label")
    if not label:
        return None
    if label not in class_to_id:
        raise KeyError(f"unknown label '{label}' (known: {sorted(class_to_id)})")

    points = shape.get("points") or []
    x_min, y_min, x_max, y_max = _bbox_from_points(points)

    # Clamp to image bounds to avoid negative / >image issues.
    x_min = max(0.0, min(x_min, img_w))
    x_max = max(0.0, min(x_max, img_w))
    y_min = max(0.0, min(y_min, img_h))
    y_max = max(0.0, min(y_max, img_h))

    bw = x_max - x_min
    bh = y_max - y_min
    if bw <= 0.0 or bh <= 0.0:
        return None

    xc = (x_min + x_max) / 2.0 / img_w
    yc = (y_min + y_max) / 2.0 / img_h
    w = bw / img_w
    h = bh / img_h

    return YoloBox(
        cls=class_to_id[label],
        xc=_clamp01(xc),
        yc=_clamp01(yc),
        w=_clamp01(w),
        h=_clamp01(h),
    )


def convert_split(
    dataset_root: Path,
    split: str,
    classes: list[str],
    image_exts: tuple[str, ...] = (".jpg", ".jpeg", ".png"),
    overwrite: bool = True,
    create_empty_for_missing: bool = True,
) -> dict[str, int]:
    images_dir = dataset_root / split / "images"
    labels_dir = dataset_root / split / "labels"
    if not images_dir.is_dir():
        raise FileNotFoundError(f"missing images dir: {images_dir}")
    if not labels_dir.is_dir():
        raise FileNotFoundError(f"missing labels dir: {labels_dir}")

    class_to_id = {name: i for i, name in enumerate(classes)}

    images = [
        p
        for p in images_dir.iterdir()
        if p.is_file() and p.suffix.lower() in image_exts
    ]
    images.sort(key=lambda p: p.name)

    written = 0
    missing_json = 0
    skipped_shapes = 0
    total_shapes = 0

    for img_path in images:
        json_path = labels_dir / f"{img_path.stem}.json"
        txt_path = labels_dir / f"{img_path.stem}.txt"

        if not overwrite and txt_path.exists():
            continue

        if not json_path.exists():
            missing_json += 1
            if create_empty_for_missing:
                txt_path.write_text("", encoding="utf-8")
                written += 1
            continue

        data = json.loads(json_path.read_text(encoding="utf-8"))
        img_w = float(data.get("imageWidth") or 0)
        img_h = float(data.get("imageHeight") or 0)
        if img_w <= 0 or img_h <= 0:
            raise ValueError(f"invalid image size in {json_path}: {img_w}x{img_h}")

        yolo_lines: list[str] = []
        for shape in data.get("shapes") or []:
            total_shapes += 1
            try:
                box = _shape_to_yolo_box(shape, class_to_id, img_w, img_h)
            except Exception:
                raise
            if box is None:
                skipped_shapes += 1
                continue
            yolo_lines.append(box.to_line())

        txt_path.write_text("\n".join(yolo_lines) + ("\n" if yolo_lines else ""), encoding="utf-8")
        written += 1

    return {
        "images": len(images),
        "txt_written": written,
        "missing_json": missing_json,
        "total_shapes": total_shapes,
        "skipped_shapes": skipped_shapes,
    }


def write_dataset_yaml(
    yaml_path: Path,
    dataset_root: Path,
    classes: list[str],
    splits: tuple[str, ...] = ("train", "val", "test"),
) -> None:
    # Use forward slashes for Ultralytics/YOLO portability.
    rel_root = os.fspath(dataset_root.as_posix())
    lines: list[str] = [f"path: {rel_root}"]
    if "train" in splits:
        lines.append("train: train/images")
    if "val" in splits:
        lines.append("val: val/images")
    if "test" in splits:
        lines.append("test: test/images")
    lines.append("names:")
    for i, name in enumerate(classes):
        lines.append(f"  {i}: {name}")
    yaml_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert LabelMe (per-image .json) rectangles to YOLO detection labels (.txt)."
    )
    parser.add_argument(
        "--dataset_root",
        type=Path,
        default=Path("..") / "dataset",
        help="Dataset root containing train/val/test folders (default: ../dataset).",
    )
    parser.add_argument(
        "--splits",
        nargs="+",
        default=["train", "val", "test"],
        help="Splits to convert (default: train val test).",
    )
    parser.add_argument(
        "--classes",
        nargs="+",
        default=["crack", "broken"],
        help="Class names in index order (default: crack broken).",
    )
    parser.add_argument(
        "--write_yaml",
        type=Path,
        default=None,
        help="If set, write a YOLO dataset YAML file to this path (relative to CWD).",
    )
    parser.add_argument(
        "--no_overwrite",
        action="store_true",
        help="Do not overwrite existing .txt label files.",
    )
    args = parser.parse_args()

    dataset_root = args.dataset_root.resolve()
    splits = tuple(args.splits)
    classes = list(args.classes)

    print(f"Dataset root: {dataset_root}")
    print(f"Splits: {', '.join(splits)}")
    print(f"Classes: {classes}")

    for split in splits:
        stats = convert_split(
            dataset_root=dataset_root,
            split=split,
            classes=classes,
            overwrite=(not args.no_overwrite),
        )
        print(
            f"[{split}] images={stats['images']} txt_written={stats['txt_written']} "
            f"missing_json={stats['missing_json']} shapes={stats['total_shapes']} skipped_shapes={stats['skipped_shapes']}"
        )

    if args.write_yaml is not None:
        yaml_path = args.write_yaml.resolve()
        dataset_root_for_yaml = Path(os.path.relpath(dataset_root, yaml_path.parent))
        write_dataset_yaml(yaml_path, dataset_root_for_yaml, classes, splits=splits)
        print(f"Wrote dataset YAML: {yaml_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
