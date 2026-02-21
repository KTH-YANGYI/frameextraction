# 裂缝视频自动片段定位与ROI抽帧项目实现详解

## 0. 文档定位

本文档面向你当前项目 `frameextraction` 的**工程实现说明**，目标是把“代码到底怎么工作”讲清楚，包括：

1. 端到端执行链路。
2. 外部接口（CLI、配置、输出产物）输入输出。
3. 核心算法方法原理（ROI自动适配、SSIM评分、分段检测等）。
4. 关键函数级别的实现映射。
5. 当前版本运行现状与注意事项。
6. 理论参考文献。

文档基于当前代码 `crack_analyze.py` 与近期运行产物目录 `out_rawdata_fullcrack2/`（48 个视频，含自适应 ROI 后处理）及 `out_rawdata_autoroi/` 编写。

---

## 1. 项目目标与边界

### 1.1 目标

给定一批 `mp4` 视频，自动完成：

1. 粗扫定位裂缝出现/扩展的候选时间段（每视频最多 TopK 段）。
2. 对候选段高帧率抽帧。
3. 按 ROI 裁剪输出 `crops`。
4. 生成每段拼图 `preview.jpg`。
5. 生成全局 `index.html` 汇总报告。

### 1.2 已实现方法

1. `segment_detection.method = ssim_ref`（主方法）。
2. `segment_detection.method = diff_prev`（基线方法）。
3. `flow` 仅占位，MVP 中会抛错阻止执行。

### 1.3 ROI模式

1. `fixed`：固定 ROI。
2. `auto_per_video`：每视频自动 ROI（ORB 匹配 + 仿射映射 + 可选局部细化 + 可选后处理平移）。

`tracking`、`mask_bbox` 尚未在当前代码实现。

---

## 2. 代码与文件结构

### 2.1 核心文件

1. `crack_analyze.py`：主程序，包含所有处理流程。
2. `config.example.yaml`：参数模板。
3. `config.rawdata.autoroi.yaml`：当前主用全量跑批配置（自动 ROI）。
4. `config.rawdata.fullcrack.yaml`：增强“完整裂缝过程覆盖”的配置（`segment_strategy=run` + 自适应 ROI 后处理 + 640 裁剪输出）。
5. `run_pilot.bat`：5 视频 pilot 入口。
6. `run_all.bat`：全量入口。
7. `run_fullcrack.bat`：完整裂缝覆盖配置的一键入口。
8. `README.md`：运行说明与调参建议。

### 2.2 依赖

见 `requirements.txt`：

1. `opencv-python-headless`
2. `numpy`
3. `PyYAML`

---

## 3. 端到端执行链路

主入口 `run()` 的顺序如下（`crack_analyze.py` 末尾 `run()` 附近）：

1. 解析 CLI 参数 `parse_args()`。
2. 加载配置 `load_config()`，再应用 CLI 覆盖 `apply_cli_overrides()`。
3. 建立输出目录 `_run/`，初始化日志与版本记录：
   1. `out/_run/run.log`
   2. `out/_run/versions.json`
   3. `out/_run/config_resolved.yaml`
4. 枚举输入目录下全部 `.mp4`，按自然排序。
5. 在 `auto_per_video` 模式下先构建 ROI 参考池 `build_auto_roi_context()`。
6. 逐视频调用 `process_video()`：
   1. 预检并写 `meta.json`。
   2. 解析/估计 ROI。
   3. 计算 `score_curve`。
   4. 检测 `segments`。
   5. 写 `metrics/score_curve.csv` 与 `segments.json`。
   6. 非 `dry_run` 时抽帧、裁剪、写 manifest、拼 preview。
   7. 累积用于 `index.html` 的 report 行与 `roi_overrides.json` 行。
7. 汇总写出：
   1. `out/_run/index.html`（可配置关闭）
   2. `out/_run/failures.json`
   3. `out/_run/roi_overrides.json`
8. 返回码：
   1. 正常 0。
   2. `PipelineError` 致命错误退出码 2。
   3. `fail_fast=true` 时中途失败立即返回 1。

---

## 4. 外部接口定义

## 4.1 CLI 接口

命令格式：

```powershell
python crack_analyze.py --config config.yaml [--input_dir ...] [--output_dir ...] `
  [--method ssim_ref|diff_prev|flow] [--roi x,y,w,h] [--dry_run] `
  [--report_html|--no-report_html] [--fail_fast] [--max_videos N]
```

参数定义（`crack_analyze.py:140-153`）：

1. `--config Path`：配置文件路径。
2. `--input_dir Path`：覆盖配置中的输入目录。
3. `--output_dir Path`：覆盖配置中的输出目录。
4. `--method`：覆盖 `segment_detection.method`。
5. `--roi x,y,w,h`：强制 `roi.mode=fixed`，并写入 `roi.rect`。
6. `--dry_run`：仅生成分数曲线与分段，不抽帧。
7. `--report_html / --no-report_html`：控制是否生成 `index.html`。
8. `--fail_fast`：遇到单视频错误是否立即终止。
9. `--max_videos N`：仅处理前 N 个视频（便于 pilot）。

配置优先级：

1. 内置默认 `DEFAULT_CONFIG`。
2. `--config` YAML 覆盖默认。
3. CLI 参数再覆盖配置（最高优先级）。

---

## 4.2 配置文件接口（YAML）

当前结构如下：

```yaml
input_dir: ./videos
output_dir: ./out

roi:
  mode: fixed | auto_per_video
  rect: [x, y, w, h] | null
  rect_landscape: [x, y, w, h] | null
  rect_portrait: [x, y, w, h] | null
  rect_norm: [x/w, y/h, w/w, h/h] | null
  rect_norm_landscape: [...]
  rect_norm_portrait: [...]
  overrides:
    "video_id": [x, y, w, h]
  auto:
    sample_sec: 2.0
    sample_frames: 15
    orb_nfeatures: 3000
    ratio_test: 0.75
    min_matches: 20
    min_inliers: 8
    ransac_thresh: 5.0
    refine: true
    search_ratio: 0.18
    search_steps: 7
    scales: [0.9, 1.0, 1.1]
    max_refs: 10
    min_ref_score: 0.45
    post_shift_mode: fixed | adaptive_copper | none
    post_shift_ratio: [dx_w_ratio, dy_h_ratio]
    post_shift_px: [dx_px, dy_px]
    post_shift_target_x: 0.48
    post_shift_min_ratio: 0.01
    post_shift_min_pixels: 120
    post_shift_max_abs_ratio: 0.35
    post_shift_direction: left_only | right_only | both
    post_shift_fallback: none | fixed

segment_detection:
  method: ssim_ref | diff_prev | flow
  coarse_fps: 2.0
  resize_w: 320
  smooth_win: 5
  ssim_ref_n_ref: 5
  segment_strategy: peak | run
  run_pre_pad_sec: 1.0
  run_post_pad_sec: 1.5
  run_max_len_sec: 20.0
  threshold:
    mode: quantile | mad
    q: 0.995
    mad_k: 6.0
  topk: 3
  fill_to_topk: true
  segment_len_sec: 8.0
  merge_gap_sec: 0.5

frame_extraction:
  fine_fps: 10.0
  image_ext: jpg | png
  jpg_quality_q: 2
  keep_full_frames: false
  crop_output: true
  crop_resize: null | 640 | [640, 640] | "640x640"
  crop_resize_mode: letterbox | stretch
  crop_resize_pad_value: 114

report:
  enable_html: true
  contact_sheet_fps: 1.0
  tile: [4, 3]

runtime:
  workers: 1
  fail_fast: false
  log_level: INFO
  dry_run: false
  max_videos: null
```

ROI 选择优先顺序（`resolve_roi_rect`）：

1. `rect_landscape` / `rect_portrait`
2. `rect`
3. `rect_norm_landscape` / `rect_norm_portrait`
4. `rect_norm`
5. 无可用值则回退全图。

且 `roi.overrides[video_id]` 在 `resolve_video_roi` 中拥有最高优先级，可直接覆盖。

分段策略参数说明（`segment_detection`）：

1. `segment_strategy=peak`：以峰值为中心，固定长度 `segment_len_sec` 截段（兼容旧行为）。
2. `segment_strategy=run`：以阈值连通区间（run）为基础截段，并叠加前后留白：
   1. `run_pre_pad_sec`：向前扩展秒数。
   2. `run_post_pad_sec`：向后扩展秒数。
   3. `run_max_len_sec`：单段最大长度；`>0` 时超长会围绕峰值裁到该长度，`<=0` 表示不做上限裁剪。

---

## 4.3 输出接口（目录与字段）

### 4.3.1 运行级输出

目录：`out_xxx/_run/`

1. `config_resolved.yaml`：合并后的最终参数。
2. `versions.json`：Python/OpenCV/Numpy/平台版本以及 ffmpeg/ffprobe 路径。
3. `run.log`：全流程日志。
4. `failures.json`：失败列表。
5. `roi_overrides.json`：每视频最终 ROI 及来源。
6. `index.html`：所有候选段预览汇总。

### 4.3.2 视频级输出

目录：`out_xxx/<video_id>/`

1. `meta.json`：视频元信息。
2. `segments.json`：分段结果与 ROI/阈值信息。
3. `metrics/score_curve.csv`：`t_sec,score,score_smooth`。
4. `seg_001/..`：每段的帧、裁剪、manifest、preview。

### 4.3.3 `meta.json` 字段

示例：

```json
{
  "video_path": "...\\1.mp4",
  "fps": 30.0,
  "width": 1280,
  "height": 720,
  "frame_count": 544,
  "duration_sec": 18.133333333333333,
  "codec_fourcc": "h264",
  "video_id": "1"
}
```

### 4.3.4 `segments.json` 字段

核心字段：

1. `method`：本视频评分方法。
2. `roi.mode/rect/source/details`：本视频实际 ROI 来源与匹配细节。
3. `threshold`：阈值配置和数值；当启用 `segment_strategy=run` 时还会包含 `run_pre_pad_sec/run_post_pad_sec/run_max_len_sec`。
4. `segments[]`：每段 `segment_id/start/end/peak_t/peak_score/fallback/merged_count`。

### 4.3.5 `manifest.csv` 字段

固定列（`write_manifest`）：

1. `frame_id`
2. `t_sec`
3. `source_frame_idx`
4. `frame_path`
5. `crop_path`
6. `roi_rect`

### 4.3.6 `roi_overrides.json` 字段

每行表示一个视频最终 ROI 决策：

1. `video_id`, `video_path`, `width`, `height`
2. `roi_rect`
3. `roi_source`（例如 `manual_override`、`auto_per_video:orb:portrait:5+shift`）
4. `roi_details`（match/inlier/reference/roi_score，以及可选 `post_shift` 细节）

---

## 5. 核心函数接口（内部）

以下是最关键的函数 I/O 合同，便于二次开发时快速定位。

### 5.1 预检与配置

1. `load_config(config_path) -> dict`
2. `apply_cli_overrides(cfg, args) -> dict`
3. `probe_video(video_path) -> meta_dict`
4. `list_videos(input_dir) -> list[Path]`

异常：

1. 输入目录不存在。
2. 视频无法打开。
3. `fps/width/height/frame_count` 非法。

### 5.2 ROI 相关

1. `resolve_roi_rect(roi_cfg, width, height) -> (rect_or_none, source_key)`
2. `clip_roi(roi_mode, roi_rect, width, height, logger, video_id) -> rect`
3. `resolve_video_roi(video_path, meta, cfg, roi_ctx, logger, video_id) -> (rect, source, details, ref_payload_or_none)`
4. `build_auto_roi_context(videos, cfg, logger) -> roi_ctx`
5. `update_auto_roi_context(...) -> None`

### 5.3 打分与分段

1. `compute_score_curve(video_path, meta, roi_rect, method, coarse_fps, resize_w, ssim_ref_n_ref) -> (t_arr, score_arr)`
2. `detect_segments(t_arr, score_arr, duration_sec, cfg, logger, video_id) -> {"threshold","segments","smoothed_scores"}`
3. `score_csv_write(path, t_arr, score_arr, smooth_arr) -> None`

### 5.4 抽帧与报告

1. `extract_segment(video_path, meta, roi_rect, seg, frame_cfg, report_cfg, video_out_dir) -> (seg_dir, manifest_path, rows, preview_path)`
2. `make_index_html(run_dir, rows) -> None`

---

## 6. 算法原理与工程实现细节

## 6.1 Preflight（视频体检）

`probe_video` 通过 OpenCV `VideoCapture` 读取元数据。若以下任一条件不满足则抛异常：

1. `width > 0 && height > 0`
2. `fps > 0`
3. `frame_count > 0`
4. `duration_sec = frame_count / fps > 0`

意义：

1. 提前发现坏视频，避免中途崩溃。
2. 后续秒到帧映射依赖 `fps` 与 `frame_count`。

## 6.2 ROI 决策机制

## 6.2.1 固定ROI模式

`fixed` 模式直接采用配置矩形，越界时自动裁剪并写 WARN 日志（`clip_roi`）。

## 6.2.2 `auto_per_video` 模式

流程如下：

1. 读取视频代表帧 `read_representative_frame`：
   1. 在前 `sample_sec` 秒均匀抽 `sample_frames` 帧。
   2. 多帧时取逐像素中位数，降低手部短时遮挡影响。
2. 从参考池里取同朝向参考（portrait/landscape）。
3. 当前帧与参考帧做 ORB 特征提取 [2]。
4. BFMatcher KNN 匹配（`k=2`），应用 Lowe ratio test [3]：
   1. 保留 `d1 < ratio_test * d2` 的匹配对。
5. 若匹配点数不足 `min_matches`，跳过该参考。
6. 用 `estimateAffinePartial2D(..., RANSAC)` 估计仿射变换 [4]：
   1. 统计内点数 `inlier_count`。
   2. 选 `inlier_count` 最大（并以 `match_count` 作为次序）候选。
7. 若 `inlier_count >= min_inliers`，将参考 ROI 通过仿射矩阵映射到当前视频。
8. 否则回退到固定 ROI（`fallback_rect`）。
9. 可选 `refine=true` 时执行局部网格搜索精调 ROI。
10. 可选执行 `post_shift` 后处理：
   1. `fixed`：按固定比例/像素平移 ROI。
   2. `adaptive_copper`：根据 ROI 内铜色区域（工件）质心自适应计算平移。
   3. 支持 `left_only/right_only/both` 方向约束与最大平移比例限制。

该模式本质是“**几何对齐 + 局部评分优化**”。

## 6.2.3 ROI 局部细化评分函数

`roi_candidate_score` 基于颜色与纹理混合打分：

1. 纹理项：Laplacian 绝对均值（edge_mean）。
2. 颜色项：HSV 下的 `green_ratio`、`copper_ratio`、`metal_ratio`。
3. 惩罚项：铜色过量 `copper_excess = max(0, copper_ratio - 0.20)`。
4. 中心偏置：若铜像素足够多，鼓励铜区域靠近框中心。

线性组合（代码权重）：

`score = 1.25*edge + 0.95*metal + 0.40*(1-green) + 0.25*copper - 1.40*copper_excess + center_bonus`

解释：

1. 抑制大量绿色台架区域。
2. 保留裂缝邻域的金属纹理结构。
3. 防止 ROI 偏到纯铜背景。
4. 注意：该评分用于 `refine` 局部搜索；`fullcrack` 当前配置已将 `refine` 关闭，避免“螺母中心化”偏差。

## 6.2.4 在线参考池更新

`update_auto_roi_context` 会把高质量新视频纳入参考池：

1. 需要 `kp/des` 有效。
2. 关键点数量至少 80。
3. `roi_score >= min_ref_score`。
4. 参考池长度上限 `max_refs`，超出时丢弃最早项。

作用：

1. 逐步适配拍摄角度变化。
2. 提升后续视频匹配稳定性。

## 6.2.5 ROI 后处理自适应平移（`adaptive_copper`）

当前代码新增 `resolve_post_shift`，在 ORB 映射（与可选 refine）之后执行 ROI 后处理，核心逻辑：

1. 在当前 ROI 内做 HSV 铜色掩膜统计（`copper_ratio`、`copper_pixels`、`copper_cx`）。
2. 当统计满足 `post_shift_min_ratio` 与 `post_shift_min_pixels` 时，计算目标偏移：
   1. 目标是使铜色质心靠近 `post_shift_target_x`（默认 0.48）。
   2. 偏移量再用 `post_shift_max_abs_ratio` 限幅。
3. 使用 `post_shift_direction` 做方向约束：
   1. `left_only`：只允许向左纠偏（避免把 ROI 推向工件外侧）。
   2. `right_only`：只允许向右纠偏。
   3. `both`：双向都允许。
4. 若自适应条件不满足，按 `post_shift_fallback` 处理：
   1. `none`：不平移。
   2. `fixed`：退回固定平移。
5. 真正平移使用“保持宽高不变”的边界钳制逻辑，避免触边后 ROI 尺寸意外变化。

## 6.3 粗扫评分曲线

## 6.3.1 时间采样

`sample_points` 先生成均匀时间点，再映射到帧索引：

1. `n = floor(duration*coarse_fps) + 1`。
2. `idx = round(t * fps)`，并裁剪到 `[0, frame_count-1]`。
3. 去重后返回 `uniq_idx, uniq_t`。

## 6.3.2 `ssim_ref` 方法

参考帧生成：

1. 取前 `ssim_ref_n_ref` 个采样点。
2. 在 ROI 灰度域求中位数参考帧（`n_ref>1` 时）。

评分：

1. `score(t) = 1 - SSIM(I_ref, I_t)`。
2. SSIM 采用全局均值/方差/协方差实现（`ssim_global`），对应经典公式 [1]。

含义：

1. 结构越接近参考，分数越低。
2. 结构变化（裂缝、形变、遮挡）越大，分数越高。

## 6.3.3 `diff_prev` 方法

评分：

1. `score(t) = mean(abs(I_t - I_{t-1})) / 255`（ROI 灰度）。

特点：

1. 速度快。
2. 对瞬时运动、光照抖动更敏感，误报倾向更高。

## 6.4 分段检测（TopK 候选段生成）

`detect_segments` 主要步骤：

1. 平滑：均值卷积窗 `smooth_win`。
2. 阈值：
   1. `quantile`：`thr = quantile(smoothed, q)`。
   2. `mad`：`thr = median + mad_k * MAD`（MAD 为绝对偏差中位数）[7]。
3. 读取分段策略 `segment_strategy`：
   1. `peak`：旧策略，固定长度峰值截段。
   2. `run`：新策略，按阈值 run 边界截段并留白。
4. 连通域：找 `smoothed >= thr` 的连续 run。
5. run 转 segment：
   1. 找 run 内峰值时间 `peak_t`。
   2. 若 `segment_strategy=peak`：
      1. 直接以 `peak_t` 为中心截取 `segment_len_sec`。
   3. 若 `segment_strategy=run`：
      1. 先取 run 的时间边界 `[run_start_t, run_end_t]`。
      2. 再加留白：`start = run_start_t - run_pre_pad_sec`，`end = run_end_t + run_post_pad_sec`（并裁剪到视频时长内）。
      3. 若该段仍短于 `segment_len_sec`，会再与峰值中心定长段并集，保证最小覆盖长度。
      4. 若配置 `run_max_len_sec > 0` 且超长，则围绕 `peak_t` 裁到最大长度。
6. 无 run 时 fallback：
   1. 取全局最大峰值生成 1 段并标记 `fallback=true`。
7. 可选补足 `topk`：
   1. 若段数不足，按局部峰值补段（避免与已有峰过近）。
8. 合并：
   1. 时间重叠或间隔 `< merge_gap_sec` 则合并。
   2. 保留更高平滑峰值对应的 `peak_t/peak_score`。
9. 合并后整形：
   1. `peak` 策略会再次按固定时长重心截段（保持行为一致）。
   2. `run` 策略保留 run 边界语义；若设置 `run_max_len_sec > 0` 仍会执行长度上限裁剪。
10. 排序与裁剪：
   1. 按 `peak_smooth_score` 取前 `topk`。
   2. 保证 `start/end` 在 `[0, duration]` 且 `end > start`。

## 6.5 抽帧、裁剪、manifest

`extract_segment` 逻辑：

1. 用 `extraction_points` 在 `[start,end]` 以 `fine_fps` 采样。
2. 每个采样点读取源帧，输出 `frames/%06d.ext`。
3. 若 `crop_output=true`，按 ROI 裁剪输出 `crops/%06d.ext`。
4. 若配置 `crop_resize`，则裁剪后再统一尺寸：
   1. `crop_resize_mode=letterbox`：保持比例并填充（默认填充值 114）。
   2. `crop_resize_mode=stretch`：直接拉伸到目标尺寸。
5. 记录 manifest 行：
   1. `frame_id`
   2. `t_sec`（按 `source_frame_idx/fps` 回算）
   3. `source_frame_idx`
   4. `frame_path`
   5. `crop_path`
   6. `roi_rect`
6. 校验：若抽取 0 帧则抛异常。
7. 上层 `process_video` 会额外检查 `frames` 与 `crops` 数量一致。

## 6.6 预览拼图与 HTML 报告

1. `choose_preview_frames` 按 `contact_sheet_fps` 选择若干帧，最多 `tile_cols * tile_rows` 张。
2. 优先用 `crops` 拼图，若无裁剪则回退 `frames`。
3. `build_contact_sheet` 将同尺寸图像拼接为网格，空位补黑图。
4. `make_index_html` 写静态表格，使用相对路径链接预览图。

---

## 7. 错误处理与鲁棒性设计

## 7.1 单视频隔离

`run()` 对每个视频 `try/except`：

1. 单视频失败记录到 `failures.json`。
2. 默认继续处理后续视频（`fail_fast=false`）。

## 7.2 `fail_fast` 行为

开启后任一视频失败会：

1. 立即写出当前 `failures.json`、`roi_overrides.json`、可选 `index.html`。
2. 返回码 1 终止。

## 7.3 关键安全网

1. ROI 越界 clip。
2. 评分 NaN 用均值回填，避免曲线全断。
3. 分段为空时全局峰值 fallback。
4. frames/crops 一致性检查。

---

## 8. 当前版本的工程特性与注意事项

## 8.1 当前全量运行现状（`out_rawdata_autoroi`）

1. 视频数：48（因缺失 `3.mp4`）。
2. 失败数：0（`_run/failures.json` 为空数组）。
3. ROI 来源统计：
   1. `manual_override`：1 个视频（`video_id=11`）。
   2. `auto_per_video:orb:*`：45 个视频。
   3. `auto_per_video:fallback:*`：2 个视频（`13`,`40`）。

## 8.2 重复跑批的目录残留问题

当前实现不会先清理旧 `seg_*` 目录。若同一 `output_dir` 反复运行，可能出现：

1. `segments.json` 已是新结果。
2. 但旧 `seg_002` 等目录仍残留。

你当前目录里 `30`、`32` 出现了这个现象（`segments.json=1`，目录里 `seg_*` 为 2）。  
如果需要“输出与本次结果严格一一对应”，建议每次运行前清空对应 `output_dir` 或在程序中增加“每视频开始前清理旧 `seg_*`”逻辑。

## 8.3 新增“完整裂缝覆盖”运行配置（当前主用 `fullcrack`）

针对“提取帧未覆盖裂缝全过程”的问题，当前代码已新增 `run` 分段策略与配套配置：

1. 配置文件：`config.rawdata.fullcrack.yaml`
2. 执行脚本：`run_fullcrack.bat`
3. 核心变化：
   1. `segment_strategy: run`
   2. `run_pre_pad_sec` / `run_post_pad_sec`
   3. 更高 `coarse_fps` 与 `fine_fps`
   4. `roi.auto.refine: false`（避免细化评分导致 ROI 过度靠近螺母中心）
   5. `roi.auto.post_shift_mode: adaptive_copper`（自适应纠偏）
   6. `frame_extraction.crop_resize: [640, 640]`（YOLO 友好输入）

该配置当前输出到 `out_rawdata_fullcrack2/`，用于和 `out_rawdata_autoroi/` 并行对比，不覆盖原结果。

## 8.4 自适应后处理效果（`dry_run` 对比）

以 48 视频对比“固定左移”与“自适应左向纠偏（left_only）”：

1. ROI 左边界触边（`x<=1`）数量：`32/48 -> 3/48`。
2. 中位 `roi_rect.x`：`0 -> 146`，明显降低“整体贴左”风险。
3. 仍触边的少量样本通常本身处于极限位置，已在 `roi_details.post_shift` 中记录 `clamped=true` 便于后续人工复核。

---

## 9. 与计划文档的对应关系（验收映射）

`crack_pipeline_plan.md` 的核心验收点与当前实现对应如下：

1. Preflight + 元数据：已实现（`meta.json`）。
2. ROI fixed：已实现。
3. 每视频自适应 ROI：已扩展实现（`auto_per_video`）。
4. score_curve：已实现，输出 CSV。
5. 1~TopK 段生成：已实现（阈值、自适应、fallback、补段、合并），并支持 `peak` / `run` 双策略。
6. 抽帧+裁剪+manifest：已实现。
7. preview+index.html：已实现。
8. 失败隔离与日志：已实现。
9. `flow`：保留接口，未在 MVP 启用（按预期）。

---

## 10. 二次开发建议（优先级）

1. 输出一致性增强：每视频处理前可选清理旧 `seg_*` 目录。
2. ROI 质量诊断：在 `index.html` 增加 `roi_source` 与 `inlier_count` 列。
3. 评分抗遮挡：对 `ssim_ref` 增加“手套区域抑制”或时序中值滤波。
4. `flow` 方法落地：可接 Farneback/TV-L1 并融合全局运动补偿。
5. 并行化：当前 `runtime.workers` 未实装，多进程可显著缩短全量时间。

---

## 11. 参考文献

[1] Wang, Z., Bovik, A. C., Sheikh, H. R., & Simoncelli, E. P. (2004). Image quality assessment: From error visibility to structural similarity. *IEEE Transactions on Image Processing*, 13(4), 600-612. DOI: 10.1109/TIP.2003.819861. Link: https://ieeexplore.ieee.org/document/1284395

[2] Rublee, E., Rabaud, V., Konolige, K., & Bradski, G. (2011). ORB: An efficient alternative to SIFT or SURF. *ICCV 2011*, 2564-2571. DOI: 10.1109/ICCV.2011.6126544. Link: https://ieeexplore.ieee.org/document/6126544

[3] Lowe, D. G. (2004). Distinctive image features from scale-invariant keypoints. *International Journal of Computer Vision*, 60, 91-110. DOI: 10.1023/B:VISI.0000029664.99615.94. Link: https://link.springer.com/article/10.1023/B:VISI.0000029664.99615.94

[4] Fischler, M. A., & Bolles, R. C. (1981). Random sample consensus: A paradigm for model fitting with applications to image analysis and automated cartography. *Communications of the ACM*, 24(6), 381-395. DOI: 10.1145/358669.358692. Link: https://dl.acm.org/doi/10.1145/358669.358692

[5] Bradski, G. (2000). The OpenCV Library. *Dr. Dobb's Journal of Software Tools*. Link: http://www.drdobbs.com/open-source/the-opencv-library/184404319

[6] OpenCV Documentation. `BFMatcher`, `estimateAffinePartial2D`, `ORB_create`, `VideoCapture`. Link: https://docs.opencv.org/

[7] Leys, C., Ley, C., Klein, O., Bernard, P., & Licata, L. (2013). Detecting outliers: Do not use standard deviation around the mean, use absolute deviation around the median. *Journal of Experimental Social Psychology*, 49(4), 764-766. Link: https://www.sciencedirect.com/science/article/pii/S0022103113000668

---

## 12. 结论

当前项目已经从“固定 ROI MVP”演进为“支持每视频自动 ROI + 全链路批处理”的可交付版本。  
其核心能力是：

1. 用结构变化分数快速定位候选裂缝时段，并支持 `peak/run` 双分段策略。
2. 用自动 ROI 将视角差异视频统一到工件区域。
3. 用标准化产物（JSON/CSV/HTML）形成可复核、可追溯的数据闭环。
