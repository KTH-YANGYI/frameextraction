# Crack 视频关键片段/抽帧/裁剪 —— 可交付给 Coding Agent 的实现计划（Windows）

> 目的：你把这份文档直接发给 coding agent，他照着做就能把工程跑起来。  
> 说明：本计划不含具体代码实现细节，但把“要做什么、为什么、输出什么、验收什么”写清楚。

---

## 0. 目标与交付物

### 0.1 输入
- `input_dir/` 下的 `*.mp4`（约 50 个视频）
- 可选：`config.yaml`（全参数）
- 可选：`roi.json`（ROI 配置，若不用 YAML 也可）

### 0.2 输出（每个视频）
1) `segments.json`：每视频 1~3 个候选片段（start/end/peak_score 等）
2) `seg_xxx/frames/`：对候选片段按 `fine_fps` 抽帧
3) `seg_xxx/crops/`：对每帧裁剪出裂痕区域 ROI（默认开启，便于分割数据集）
4) `seg_xxx/manifest.csv`：每帧的时间戳、路径、ROI 等追溯信息
5) `seg_xxx/preview.jpg`：候选片段的拼图预览（用于快速人工核验）
6) `out/_run/index.html`：汇总所有视频候选片段预览的 HTML 报告（默认开启）
7) `out/_run/run.log` + `out/_run/failures.json`：全流程日志与失败列表

---

## 1. 输出目录规范（必须遵守）

输出根目录：`out/`

```
out/
  _run/
    config_resolved.yaml
    versions.json
    run.log
    failures.json
    index.html
  <video_id>/
    meta.json
    segments.json
    metrics/
      score_curve.csv
      (optional) ssim.log
    seg_001/
      frames/
        000001.jpg
        ...
      crops/
        000001.jpg
        ...
      manifest.csv
      preview.jpg
    seg_002/...
```

- `video_id = 视频文件名（不含扩展名）`
- `segment_id = seg_001, seg_002, ...`
- `frame_id = 000001, 000002, ...`（固定 6 位）

---

## 2. 配置文件（建议 YAML）

实现要求：读取 `config.yaml`，补全默认值，写回 `out/_run/config_resolved.yaml`。

示例配置（另见 config.example.yaml）：
- ROI：默认 fixed（固定 ROI）
- 片段定位默认方法：`ssim_ref`
- 阈值：每视频分位数（quantile）自适应
- 输出：抽帧 + 裁剪 + HTML 报告

---

## 3. 端到端流程（必须实现）

### Step 1：Preflight（体检）
**做什么**
- 遍历 `input_dir/*.mp4`
- 用 ffprobe 获取：duration、fps、width、height、codec
- 记录异常：打不开/时长为 0/fps 缺失等

**为什么**
- 批处理 50 个视频必须先体检，避免跑到一半卡死
- 后续截段/抽帧需要可靠 duration

**产出**
- `out/<video_id>/meta.json`
- `out/_run/run.log`

**验收**
- 正常视频均产出 meta.json；异常视频写入 failures.json，不影响继续处理（fail_fast=false）

---

### Step 2：ROI 策略与校验（强烈建议默认 fixed）
**做什么**
- 支持 ROI 模式：
  - `fixed`：固定矩形 ROI（x,y,w,h）
  - `tracking`：跟踪 ROI（可后续增强）
  - `mask_bbox`：mask/热力图→bbox（可后续增强）
- 若用户没提供 ROI：允许 fallback 全图，但必须 WARN（不推荐）

**为什么**
- 弯折过程全局变化大，裂痕是局部变化；ROI 是稳定性上限

**产出**
- ROI 写入 `segments.json` 与 `manifest.csv`（用于追溯）

**验收**
- ROI 越界自动裁剪（clip）并 WARN

---

### Step 3：粗扫生成 score 曲线（score_curve）
**做什么**
- 每视频按 `coarse_fps`（默认 2fps）采样
- 仅在 ROI 上计算（并可降采样到 `resize_w`，默认 320）
- 生成一条时间序列：`(t, score)`，保存为 `metrics/score_curve.csv`

**为什么**
- 先便宜定位变化区间，避免全视频高 fps 抽帧导致数据量爆炸

**产出**
- `out/<video_id>/metrics/score_curve.csv`（至少两列：t_sec, score）

**验收**
- score_curve 覆盖 0~duration；score 非 NaN；点数约为 duration*coarse_fps

---

### Step 4：从 score_curve 自动生成 1~3 个候选片段（segments.json）
**做什么**
1) 平滑：对 score 做滑动平均或中值滤波（窗口 `smooth_win`）
2) 自适应阈值：
   - quantile：`thr = quantile(score, q)`（默认 q=0.995）
   - MAD：`thr = median(score) + k * MAD(score)`（可选）
3) 连通区间（runs）：找 `score >= thr` 的连续区间
4) 以 run 内峰值为中心截取固定长度 `segment_len_sec`（默认 8s）
5) 合并重叠/相邻片段（gap < merge_gap_sec）
6) 按 peak_score 排序取 TopK（默认 3）
7) 若一个片段都没有：fallback 到全曲线最大峰值附近截一段（并 WARN）

**为什么**
- 阈值必须按“每视频自适应”，否则曝光/材质变化导致不可迁移
- 以峰值为中心截固定段，便于抽帧与人工复核
- fallback 保证每视频至少产出 1 段，避免空结果

**产出**
- `out/<video_id>/segments.json`

**验收**
- 每段 start/end ∈ [0, duration] 且 end>start
- segments 数量 1~TopK

---

### Step 5：对候选片段精抽帧（frames + crops + manifest）
**做什么**
- 对每个 segment：
  - 用 ffmpeg 按 `fine_fps` 抽帧到 `frames/%06d.jpg`（或 png）
  - 若 `crop_output=true`：同时输出 ROI 裁剪到 `crops/%06d.jpg`
  - 写 `manifest.csv`：frame_id, t_sec, frame_path, crop_path, roi_rect

**为什么**
- 分割数据集不需要全视频抽帧，只需要候选片段的高密度帧
- manifest 用于追溯（后续调参/复现/排错）

**产出**
- `out/<video_id>/seg_xxx/frames/`
- `out/<video_id>/seg_xxx/crops/`
- `out/<video_id>/seg_xxx/manifest.csv`

**验收**
- frames 与 crops 数量一致（若启用 crops）
- 抽帧数 ≈ segment_len_sec * fine_fps（允许轻微误差）

---

### Step 6：生成人工核验报告（preview + index.html）
**做什么**
- 每段生成 `preview.jpg`：
  - 从该片段按 `contact_sheet_fps`（默认 1fps）抽若干帧
  - 用 tile（默认 4x3）拼成 contact sheet
- 汇总 `out/_run/index.html`：
  - 每行：video_id、segment_id、start/end、method、preview 图片

**为什么**
- 你要在几分钟内看完 50 个视频的候选片段是否命中裂痕
- 报告是调参闭环的关键

**产出**
- 每段 `preview.jpg`
- 汇总 `index.html`

**验收**
- 打开 index.html 能正确显示所有 preview（使用相对路径，兼容 Windows）

---

## 4. “自动找片段”的 3 种方法（必须可选，后续复用同一段生成逻辑）

所有方法统一输出：`score_curve(t)`，后面的 Step 4/5/6 不变。

### Method 1：ssim_ref（默认推荐）
- 参考帧 I_ref：默认取 t=0 的第一帧；建议支持“前 N 帧中位数参考”（增强鲁棒）
- score：`score(t) = 1 - SSIM(I_ref, I_t)`（仅 ROI）

**优点**：对结构变化敏感，工程实现简单，适合裂痕逐渐出现  
**风险**：高光/曝光变化会影响，需 ROI+平滑+自适应阈值缓解

### Method 2：diff_prev（最快的基线）
- score：`score(t) = mean(|I_t - I_{t-1}|)`（ROI 灰度）

**优点**：极快、实现最简单  
**风险**：更容易受曝光/高光影响导致误报

### Method 3：flow（光流残差）
- 计算光流得到 (u,v)
- 全局运动抑制：减去 ROI 内中位数向量 (u0,v0)
- score：`mean(sqrt((u-u0)^2+(v-v0)^2))`

**优点**：当裂痕扩展导致局部运动异常时更敏感  
**风险**：更慢、更难调参；弯折的大运动易干扰

---

## 5. 裂痕区域裁剪（3 种方案：实现优先级）

### Crop方案 1：fixed ROI（必须实现，默认）
- 抽帧时直接 crop 出 ROI，保存到 crops/

### Crop方案 2：tracking ROI（可选增强）
- 在 segment 第一帧初始化 ROI
- LK 跟踪/其他 tracker 更新 ROI
- 每帧裁剪，用 manifest 记录动态 roi_rect
- tracker 失效需 fallback（重初始化或退回固定 ROI）并记录 WARN/ERROR

### Crop方案 3：mask_bbox（高级增强）
- 输入 mask/热力图（来自模型或异常检测）
- 二值化→连通域→最大区域→bbox→margin→裁剪
- 输出动态裁剪及 bbox 记录

---

## 6. 默认参数（建议作为系统默认值）

- coarse_fps: 2.0
- resize_w: 320
- smooth_win: 5
- threshold: quantile q=0.995
- topk: 3
- segment_len_sec: 8.0
- merge_gap_sec: 0.5
- fine_fps: 10.0
- image_ext: jpg（后续若追求边界可改 png）
- contact_sheet_fps: 1.0
- tile: 4x3

### 调参规则（写入 README，方便迭代）
- 误报多：缩小 ROI；q 提高（0.995→0.997）；smooth_win 增大（5→9）
- 漏报：扩大 ROI；q 降低（0.995→0.99）；segment_len 增大（8→12~20）
- 周期性误报很强：后续 v2 可引入变化点检测（ruptures/PELT）

---

## 7. CLI 要求（Python 方案必须提供）

最少参数：
- `--config config.yaml`
- `--input_dir` `--output_dir`（可覆盖 config）
- `--method ssim_ref|diff_prev|flow`
- `--roi x,y,w,h`（可覆盖 config）
- `--dry_run`（只做 score_curve 与 segments，不抽帧）
- `--report_html / --no-report_html`
- `--fail_fast`

返回码：
- 默认 fail_fast=false：部分视频失败也返回 0，但 failures.json 记录
- fail_fast=true：遇到失败立即非 0 退出

---

## 8. 日志与异常处理（强制要求）

- `out/_run/run.log`：全流程日志（INFO/WARN/ERROR）
- `out/_run/failures.json`：结构化失败记录（video_id、原因、关键 stderr 截断）

要求：
- 路径含空格、中文必须正常
- 单视频失败不影响其他视频（默认）

---

## 9. 开发顺序（建议按此实现，最不容易返工）

1) config + 输出目录规范 + preflight（meta.json）
2) fixed ROI + ssim_ref 粗扫 score_curve
3) segments 生成（平滑/阈值/合并/TopK/fallback）
4) ffmpeg 抽帧 + crops + manifest
5) preview 拼图 + index.html
6) diff_prev 方法
7) flow 方法
8)（可选）tracking ROI / mask_bbox

---

## 10. 验收清单（交付测试）

功能验收：
- [ ] 单视频产出：meta.json / segments.json / seg_001/frames / seg_001/crops / preview.jpg
- [ ] 50 视频批处理：坏视频不中断（fail_fast=false）
- [ ] segments start/end 合法，数量 ≤ topk
- [ ] frames 与 crops 一一对应
- [ ] index.html 可打开且预览图可见

鲁棒性验收：
- [ ] 输入路径含空格与中文
- [ ] 短视频（<segment_len）仍能输出合理片段（自动截断）
- [ ] segments 找不到时 fallback 生效并 WARN

---

## 11. 备注（给实施者）
- 建议抽帧、拼图都交给 ffmpeg（速度与稳定性更好）
- Python 主要负责：配置/遍历/粗扫打分/片段生成/报告索引
- 先用 pilot（5 个代表视频）把 ROI 与 q/smooth_win 调稳，再跑全量
