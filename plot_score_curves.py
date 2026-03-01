#!/usr/bin/env python3
"""绘制所有视频的 score 曲线（raw + smooth），并标注阈值线和检测到的 segment 区间。

用法:
    python plot_score_curves.py                          # 默认读取 out_rawdata_fullcrack2
    python plot_score_curves.py --outdir out_global_name_test
    python plot_score_curves.py --videos 1 2 3           # 只画指定视频
    python plot_score_curves.py --single                 # 每个视频单独一张图
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # 无 GUI 后端，兼容无显示器环境
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np


def load_score_curve(csv_path: Path) -> dict:
    """读取 score_curve.csv，返回 {t, score, score_smooth} numpy 数组。"""
    t, score, smooth = [], [], []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t.append(float(row["t_sec"]))
            score.append(float(row["score"]))
            smooth.append(float(row["score_smooth"]))
    return {"t": np.array(t), "score": np.array(score), "smooth": np.array(smooth)}


def load_segments(seg_path: Path) -> dict | None:
    """读取 segments.json，返回阈值和 segment 列表。"""
    if not seg_path.exists():
        return None
    with open(seg_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def plot_single_video(vid_id: str, curve: dict, seg_data: dict | None,
                      ax: plt.Axes, show_legend: bool = True):
    """在给定 Axes 上绘制单个视频的 score 曲线。"""
    t = curve["t"]
    ax.plot(t, curve["score"], alpha=0.35, linewidth=0.8, color="steelblue", label="raw score")
    ax.plot(t, curve["smooth"], linewidth=1.5, color="orangered", label="smoothed score")

    if seg_data:
        # 阈值线
        thresh_val = seg_data.get("threshold", {}).get("value")
        if thresh_val is not None:
            ax.axhline(y=thresh_val, color="green", linestyle="--", linewidth=1.0,
                        label=f"threshold={thresh_val:.3f}")

        # segment 区间高亮
        segments = seg_data.get("segments", [])
        for i, seg in enumerate(segments):
            s = seg["start_sec"]
            e = seg["end_sec"]
            ax.axvspan(s, e, alpha=0.12, color="gold",
                       label="segment" if i == 0 else None)
            # peak 标记
            peak_t = seg.get("peak_t_sec")
            peak_s = seg.get("peak_smooth_score", seg.get("peak_score"))
            if peak_t is not None and peak_s is not None:
                ax.plot(peak_t, peak_s, "v", color="red", markersize=8,
                        label="peak" if i == 0 else None)

    ax.set_xlabel("Time (sec)", fontsize=9)
    ax.set_ylabel("Score", fontsize=9)
    ax.set_title(f"Video {vid_id}", fontsize=10, fontweight="bold")
    ax.set_xlim(t[0], t[-1])
    ax.set_ylim(bottom=0)
    if show_legend:
        ax.legend(fontsize=7, loc="upper right")
    ax.grid(True, alpha=0.3)


def gather_videos(outdir: Path, video_ids: list[str] | None = None) -> list[tuple[str, dict, dict | None]]:
    """收集所有视频的 score 数据。返回 [(vid_id, curve, seg_data), ...]"""
    results = []
    # 列出所有数字命名的子目录
    subdirs = sorted(
        [d for d in outdir.iterdir() if d.is_dir() and d.name.isdigit()],
        key=lambda d: int(d.name)
    )
    for d in subdirs:
        vid_id = d.name
        if video_ids and vid_id not in video_ids:
            continue
        csv_path = d / "metrics" / "score_curve.csv"
        seg_path = d / "segments.json"
        if not csv_path.exists():
            print(f"[WARN] Video {vid_id}: score_curve.csv 不存在，跳过")
            continue
        curve = load_score_curve(csv_path)
        seg_data = load_segments(seg_path)
        results.append((vid_id, curve, seg_data))
    return results


def plot_overview(videos: list, save_path: Path):
    """所有视频的 score 曲线汇总在一张大图中（子图网格）。"""
    n = len(videos)
    if n == 0:
        print("没有可绘制的视频数据。")
        return

    cols = min(4, n)
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5.5 * cols, 3.5 * rows),
                              squeeze=False)
    fig.suptitle("Score Curves — All Videos", fontsize=14, fontweight="bold", y=1.01)

    for idx, (vid_id, curve, seg_data) in enumerate(videos):
        r, c = divmod(idx, cols)
        plot_single_video(vid_id, curve, seg_data, axes[r][c], show_legend=(idx == 0))

    # 隐藏多余的子图
    for idx in range(n, rows * cols):
        r, c = divmod(idx, cols)
        axes[r][c].set_visible(False)

    fig.tight_layout()
    fig.savefig(str(save_path), dpi=150, bbox_inches="tight")
    print(f"✅ 汇总图已保存: {save_path}")
    plt.close(fig)


def plot_individual(videos: list, save_dir: Path):
    """每个视频单独保存一张图。"""
    save_dir.mkdir(parents=True, exist_ok=True)
    for vid_id, curve, seg_data in videos:
        fig, ax = plt.subplots(figsize=(10, 4))
        plot_single_video(vid_id, curve, seg_data, ax)
        out = save_dir / f"score_curve_video_{vid_id}.png"
        fig.tight_layout()
        fig.savefig(str(out), dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  📊 Video {vid_id} -> {out}")
    print(f"✅ 共保存 {len(videos)} 张单独图到 {save_dir}")


def plot_overlay(videos: list, save_path: Path):
    """将所有视频的 smoothed score 叠加到一张图上进行对比。"""
    fig, ax = plt.subplots(figsize=(12, 5))
    cmap = plt.cm.get_cmap("tab20", len(videos))
    for idx, (vid_id, curve, seg_data) in enumerate(videos):
        ax.plot(curve["t"], curve["smooth"], linewidth=1.0,
                color=cmap(idx), alpha=0.7, label=f"V{vid_id}")

    ax.set_xlabel("Time (sec)", fontsize=10)
    ax.set_ylabel("Smoothed Score", fontsize=10)
    ax.set_title("Smoothed Score Overlay — All Videos", fontsize=12, fontweight="bold")
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    # 图例放在外侧
    ax.legend(fontsize=6, ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    fig.tight_layout()
    fig.savefig(str(save_path), dpi=150, bbox_inches="tight")
    print(f"✅ 叠加对比图已保存: {save_path}")
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description="绘制 score 曲线")
    parser.add_argument("--outdir", type=str, default="out_rawdata_fullcrack2",
                        help="pipeline 输出目录")
    parser.add_argument("--videos", nargs="*", default=None,
                        help="只画指定视频 ID（空格分隔）")
    parser.add_argument("--single", action="store_true",
                        help="每个视频单独保存一张图")
    parser.add_argument("--overlay", action="store_true",
                        help="叠加所有视频 smoothed score 到一张图")
    parser.add_argument("--save-dir", type=str, default=None,
                        help="图片保存目录（默认在 outdir/_plots）")
    args = parser.parse_args()

    base = Path(__file__).parent
    outdir = base / args.outdir
    if not outdir.exists():
        print(f"❌ 输出目录不存在: {outdir}")
        sys.exit(1)

    save_dir = Path(args.save_dir) if args.save_dir else outdir / "_plots"
    save_dir.mkdir(parents=True, exist_ok=True)

    videos = gather_videos(outdir, args.videos)
    print(f"📂 找到 {len(videos)} 个视频的 score 数据")

    if not videos:
        print("没有找到任何 score_curve.csv，请检查输出目录。")
        sys.exit(1)

    # 1) 汇总子图
    plot_overview(videos, save_dir / "score_curves_overview.png")

    # 2) 叠加图
    if args.overlay or len(videos) <= 20:
        plot_overlay(videos, save_dir / "score_curves_overlay.png")

    # 3) 单独图
    if args.single:
        plot_individual(videos, save_dir / "individual")

    print(f"\n🎉 所有图表已保存到: {save_dir}")


if __name__ == "__main__":
    main()
