# Crack Pipeline 详细流程（执行版）

本文档按 `crack_analyze.py` 的真实执行顺序描述项目 pipeline，适合用于答辩/PPT 讲解和新人上手。

---

## 1. 一句话概括

该 pipeline 做三件事：
1. 在 ROI 内对视频做时序变化打分（`score_curve`）。
2. 从打分曲线自动定位 1~TopK 个候选片段（`segments`）。
3. 对候选片段精抽帧并导出可复核结果（`frames/crops/manifest/preview/index.html`）。

---

## 2. 入口与配置合并（`run()` 前半段）

入口函数：`run()`（`crack_analyze.py:1589`）

### 2.1 参数来源

配置最终由三层合并得到：
1. `DEFAULT_CONFIG`（代码内默认）
2. `--config xxx.yaml`（YAML 覆盖默认）
3. CLI 参数二次覆盖（如 `--method`、`--roi`、`--dry_run`）

对应函数：
- `parse_args()`（`crack_analyze.py:143`）
- `load_config()`（`crack_analyze.py:159`）
- `apply_cli_overrides()`（`crack_analyze.py:170`）

### 2.2 运行级初始化

`run()` 会先做以下初始化：
1. 创建 `output_dir/_run/`
2. 初始化日志 `run.log`
3. 写环境信息 `versions.json`
4. 写最终配置快照 `config_resolved.yaml`
5. 扫描输入目录中的 `.mp4`，按自然顺序排序

关键函数：
- `setup_logging()`（`crack_analyze.py:189`）
- `write_versions()`（`crack_analyze.py:212`）
- `list_videos()`（`crack_analyze.py:226`）

---

## 3. ROI 上下文构建（仅 `auto_per_video`）

函数：`build_auto_roi_context()`（`crack_analyze.py:532`）

当 `roi.mode != auto_per_video` 时，该阶段直接跳过。

当 `roi.mode == auto_per_video` 时：
1. 选择横屏/竖屏参考视频（每类尽量找 1 个）
2. 读取参考帧（`sample_sec` + `sample_frames`）
3. ORB 提特征（`orb_nfeatures`）
4. 为每个参考视频绑定“基础 ROI”（来自 fixed 规则）
5. 形成 ROI 参考池，供后续每个视频匹配映射

这一步输出的是“上下文”，不是最终 ROI。

---

## 4. 单视频处理主流程（`process_video()`）

函数：`process_video()`（`crack_analyze.py:1451`）

对每个视频按固定 7 步处理：

### Step 1. 视频预检与元数据

1. 读取宽高、fps、总帧数、时长等元信息
2. 写出 `out/<video_id>/meta.json`

### Step 2. ROI 决策（最关键）

函数：`resolve_video_roi()`（`crack_analyze.py:630`）

ROI 优先级如下：
1. `roi.overrides[video_id]`（手工覆盖）  
2. 非 `auto_per_video` 模式：走 fixed 规则  
3. `auto_per_video` 模式：走 ORB 匹配映射，失败则回退 fixed

`auto_per_video` 具体逻辑：
1. 读取代表帧
2. 当前帧提 ORB 特征
3. 与参考池做 KNN + ratio test 匹配
4. 用 `estimateAffinePartial2D + RANSAC` 估计仿射
5. 若 `inlier_count >= min_inliers`：将参考 ROI 仿射映射到当前视频
6. 否则 fallback 到 fixed ROI
7. 可选 `refine` 再做局部搜索微调

结果会记录：
- `roi_rect`
- `roi_source`
- `roi_details`（匹配数、内点数、参考来源等）

### Step 3. 粗扫打分曲线（`score_curve`）

函数：`compute_score_curve()`（`crack_analyze.py:853`）

核心流程：
1. 按 `coarse_fps` 在全时长采样时间点
2. 每个采样点只在 ROI 内取灰度图并可缩放到 `resize_w`
3. 按方法计算 score：
   - `ssim_ref`：`score = 1 - SSIM(I_ref, I_t)`
   - `diff_prev`：`score = mean(abs(I_t - I_{t-1})) / 255`
4. 写出时间序列 `t_arr, score_arr`

说明：
- `flow` 在当前版本被明确拦截（预留 v2）
- 如果局部采样失败产生 NaN，会用有限值均值补齐，保证曲线可用

### Step 4. 分段检测（`segments`）

函数：`detect_segments()`（`crack_analyze.py:966`）

处理顺序：
1. 对 score 做滑动平均平滑（`smooth_win`）
2. 计算阈值（两种）：
   - `quantile`：`threshold = quantile(smoothed, q)`
   - `mad`：`threshold = median + mad_k * MAD`
3. 找 `smoothed >= threshold` 的连续 run
4. run 转 segment（两种策略）：
   - `segment_strategy=peak`：围绕峰值截固定长度 `segment_len_sec`
   - `segment_strategy=run`：run 前后扩展 `run_pre_pad_sec/run_post_pad_sec`，并限制 `run_max_len_sec`
5. 若没有 run：回退到“全局峰值”片段（`fallback=true`）
6. 可选 `fill_to_topk`：从局部峰补足到 `topk`
7. 对重叠/近邻片段按 `merge_gap_sec` 合并
8. 以 `peak_smooth_score` 排序，保留前 `topk`

最终写入字段：
- `segment_id, start_sec, end_sec, peak_t_sec, peak_score, peak_smooth_score, fallback, merged_count`

### Step 5. 落盘评分与分段结果

1. 写 `metrics/score_curve.csv`（列：`t_sec, score, score_smooth`）
2. 写 `segments.json`（包含 ROI 来源、阈值信息、segments 列表）

对应代码：
- `score_csv_write()`（`crack_analyze.py:1389`）
- `write_json(...segments.json...)`（`crack_analyze.py:1502`）

### Step 6. 精抽帧、裁剪、manifest、预览图

函数：`extract_segment()`（`crack_analyze.py:1286`）

对每个 segment：
1. 用 `fine_fps` 生成抽帧时间点
2. 输出 `frames/%06d.jpg`
3. 若 `crop_output=true`，按 ROI 输出 `crops/%06d.jpg`
4. 写 `manifest.csv`
5. 选取代表帧拼接 `preview.jpg`（contact sheet）

一致性检查：
- 若开启裁剪，要求 `frames` 与 `crops` 数量一致
- `manifest` 不能为空

### Step 7. 记录汇总行（供 index.html）

无论 `dry_run` 与否，都会把 segment 信息加入 `report_rows`：
- `dry_run=true`：只有文字行，无预览图
- `dry_run=false`：附带 `preview_rel` 用于 HTML 展示

---

## 5. 运行结束阶段（`run()` 尾段）

所有视频处理完成后：
1. 可选生成 `_run/index.html`
2. 写 `_run/failures.json`
3. 写 `_run/roi_overrides.json`

异常策略：
- `fail_fast=false`（默认）：单视频失败不影响后续视频，失败记录进 `failures.json`
- `fail_fast=true`：首个失败即提前退出并落盘当前结果

---

## 6. 输出契约（目录结构）

### 6.1 运行级输出

`out/_run/`
- `config_resolved.yaml`
- `versions.json`
- `run.log`
- `failures.json`
- `roi_overrides.json`
- `index.html`（可关闭）

### 6.2 视频级输出

`out/<video_id>/`
- `meta.json`
- `segments.json`
- `metrics/score_curve.csv`
- `seg_001/`
  - `frames/`
  - `crops/`
  - `manifest.csv`
  - `preview.jpg`

---

## 7. 可直接用于答辩的流程话术

“我们先读取配置并做运行初始化，然后为每个视频先确定 ROI。接着在 ROI 里做粗扫打分，得到 score 曲线。再用平滑和自适应阈值检测候选 run，并转成 1 到 TopK 个片段。之后对每个片段精抽帧，输出全帧、ROI 裁剪、manifest 和 preview。最后汇总为全局 HTML 报告，同时记录失败视频和最终 ROI 决策，保证可复核与可追溯。”

---

## 8. 对应代码索引（便于追踪）

- 入口：`crack_analyze.py:1589`
- 单视频主流程：`crack_analyze.py:1451`
- ROI 决策：`crack_analyze.py:630`
- 粗扫打分：`crack_analyze.py:853`
- 分段检测：`crack_analyze.py:966`
- 抽帧导出：`crack_analyze.py:1286`
- 报告汇总：`crack_analyze.py:1403`


---

## 9. 理论支撑（可用于答辩/论文）

下面将本项目各模块与经典理论对应起来，回答“为什么这个 pipeline 是合理的”。

### 9.1 ROI 先验与问题分解

核心思想：把“全画面变化检测”转化为“目标区域内变化检测”。

理论上，这是在做先验约束下的降维与降噪：
1. 全图包含大量与裂缝无关的背景变化（相机抖动、曝光、纹理噪声）。
2. ROI 限制后，背景干扰项显著减少，信噪比提升。
3. 目标事件（裂缝形态变化）在 ROI 内更容易形成可分离统计量。

对自动 ROI（`auto_per_video`）而言，本质上是跨视频几何配准：
- ORB 特征匹配 + ratio test 保留高置信对应点。
- RANSAC 估计仿射映射，抑制误匹配离群点。
- 将参考 ROI 仿射映射到当前视频，实现“同一工件区域”的对齐。

### 9.2 SSIM 评分的理论依据

`ssim_ref` 使用：
`score(t) = 1 - SSIM(I_ref, I_t)`

理论基础：SSIM（Structural Similarity）不是简单像素误差，而是比较亮度、对比度、结构三部分一致性。它比 MSE/PSNR 更接近结构变化感知，适合本项目“结构类异常（裂缝）”检测。

直观解释：
1. 当 ROI 结构稳定时，SSIM 高，`1-SSIM` 低。
2. 当裂缝扩展或纹理结构发生改变时，SSIM 下降，`1-SSIM` 升高。
3. 因此 `score_curve` 能反映结构变化强度随时间的演化。

### 9.3 `diff_prev` 基线的理论定位

`diff_prev` 使用：
`score(t) = mean(|I_t - I_{t-1}|)/255`

它对应时间差分检测（temporal differencing）思想：
1. 假设相邻帧短时间内变化小。
2. 若出现显著事件，帧间差分会抬升。
3. 计算代价低，可作为 SSIM 的工程基线与兜底方法。

### 9.4 平滑与阈值：从噪声序列到稳定事件

对原始分数序列做滑动平均（`smooth_win`）属于经典低通滤波思想：
1. 抑制高频噪声尖峰。
2. 保留持续性变化趋势。

阈值提供两种统计机制：
1. `quantile`：选取高分尾部（如 99.5% 分位），适合“异常稀有”的事件检测。
2. `MAD`：`threshold = median + k*MAD`，属于鲁棒统计，对离群值不敏感，适合噪声重尾场景。

因此该模块实现了“信号平滑 + 鲁棒异常判别”的组合。

### 9.5 run/peak 分段策略的理论依据

检测到 `score >= threshold` 后，本质是在做 1D 事件区间定位（temporal localization）：
1. `peak` 策略：围绕局部峰值固定窗口截取，强调“最高变化时刻”。
2. `run` 策略：对超阈 run 区间前后扩展，强调“完整过程覆盖”。
3. merge + topk：对应后处理中的区间融合与候选排序，避免碎片化片段。

这与时序异常检测中的标准流程一致：检测 -> 聚合 -> 排序 -> 截取。

### 9.6 为什么该流程适合工程落地

从方法论上，这个 pipeline 兼顾了：
1. 可解释性：每一步都有明确统计意义（配准、打分、阈值、分段）。
2. 稳定性：ROI、平滑、鲁棒阈值和 fallback 机制共同降低误检风险。
3. 可追溯性：`meta/score_curve/segments/manifest/roi_overrides` 形成完整证据链。
4. 可扩展性：替换打分函数（例如未来加入 flow 或学习模型）时，后续分段与导出链路可复用。

---

## 10. 参考文献（建议放到答辩末页）

1. Wang, Z., Bovik, A. C., Sheikh, H. R., & Simoncelli, E. P. (2004). Image quality assessment: From error visibility to structural similarity. IEEE Transactions on Image Processing.
2. Rublee, E., Rabaud, V., Konolige, K., & Bradski, G. (2011). ORB: An efficient alternative to SIFT or SURF. ICCV.
3. Lowe, D. G. (2004). Distinctive image features from scale-invariant keypoints. International Journal of Computer Vision.
4. Fischler, M. A., & Bolles, R. C. (1981). Random sample consensus: A paradigm for model fitting with applications to image analysis and automated cartography. Communications of the ACM.
5. Hampel, F. R. (1974). The influence curve and its role in robust estimation. Journal of the American Statistical Association.
