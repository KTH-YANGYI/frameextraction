# frameextraction 项目展示与实现详解（答辩/汇报用）

> 项目仓库：`KTH-YANGYI/frameextraction`  
> 核心脚本：`crack_analyze.py`  
> 核心目标：**裂缝视频的自动候选片段定位 + ROI（兴趣区域）抽帧导出 + 可复核报告**  
> （本文基于仓库文档与代码实现整理：`README.md`、`PIPELINE_DETAILED.md`、`PROJECT_IMPLEMENTATION_DETAILS.md`、`crack_analyze.py`。）

---

## 1. 你这个项目“解决了什么问题”

你面对的是一批 MP4 视频：裂缝在某个局部区域逐渐出现/扩展，但视频里往往还包含：

- 背景（绿色台架、手部遮挡、光照变化）
- 相机位置变化（角度、距离、轻微旋转、裁切）
- 不同分辨率、横竖屏差异

如果直接“全画面”去做变化检测，误报和算力都会很高。因此你把问题分解成三步：

1. **先把目标区域找准（ROI 自适应对齐）**：让不同视频的“同一物理区域”尽量对齐。
2. **只在 ROI 里做时序变化打分（score curve）**：得到一个“随时间变化强度”的一维曲线。
3. **在曲线上找 TopK 段最可能发生裂缝变化的时间片段，再对这些片段精抽帧导出**：把计算和人工复核成本降下来。

这就是你 pipeline 的整体思路（仓库 README 对项目 scope 的一句话概括也与此一致）。[^repo_readme]

---

## 2. 整体 pipeline（从输入视频到输出结果）

下面这个顺序就是你代码的真实执行链路（项目内也有对应的“执行版流程说明”）：[^pipeline_detailed][^proj_impl]

```
输入：input_dir/*.mp4
  |
  | ① 读取配置 + 初始化输出目录 + 写版本信息/日志
  |
  | ②（可选）构建 auto_per_video 的 ROI 参考池（横屏/竖屏各一组）
  |
  | ③ 对每个视频：
  |     ③.1 读取视频元信息（宽高、帧率、总帧数、时长）
  |     ③.2 决策 ROI（override > fixed > auto_per_video；auto 失败回退 fixed）
  |     ③.3 在 ROI 内粗采样，计算“变化分数曲线 score_curve”
  |     ③.4 平滑 + 自适应阈值，检测 TopK 候选片段 segments
  |     ③.5 对每个片段精抽帧（frames/）+ ROI 裁剪（crops/）+ manifest + preview
  |
  | ④ 汇总生成全局 HTML 报告（可关闭），落盘 failures/roi_overrides 等
  v
输出：output_dir/_run/ + output_dir/<video_id>/
```

你输出的证据链非常完整：`meta.json`、`metrics/score_curve.csv`、`segments.json`、`manifest.csv`、每段的 `preview.jpg`，加上 `_run/index.html` 总览。[^pipeline_detailed][^proj_impl]

---

## 3. 你做了哪些关键设计，以及“为什么这么做”

### 3.1 为什么一定要先做 ROI（兴趣区域）

核心原因是：**把“全画面变化检测”变成“目标区域变化检测”**，相当于做了强先验约束与降噪。

- 背景变化（曝光、抖动、手部等）在全画面里占比很大，会把裂缝信号淹没。
- ROI 限制后，统计量（比如结构相似性下降、帧差上升）更可能由裂缝导致，而不是由背景导致。

这类“先做空间约束再做时间检测”的思想，在工程视觉里非常常见：先把关注区域对齐/裁切，再在该区域提取更稳定的时序特征。[^pipeline_detailed]

---

### 3.2 为什么用 ORB 做“自适应 ROI”（auto_per_video）

你希望面对**不同分辨率、不同角度、不同裁切**的视频时，ROI 仍然能落在同一物理部位附近。  
你的做法不是训练一个检测网络，而是用“经典几何配准”：

1. 用 ORB（定向快速特征与旋转二值特征）在参考视频与当前视频的代表帧上提特征点与二值描述子；
2. 做特征匹配并通过 **比值检验（ratio test）**过滤误匹配；
3. 用 **随机采样一致性（RANSAC）**鲁棒估计相似变换/部分仿射；
4. 把参考 ROI 用估计出来的变换矩阵映射到当前视频坐标系。

ORB 的优势是：速度快、对旋转有一定不变性、描述子是二值的（便于 Hamming 距离快速匹配）。[^orb_paper]

你在项目里把它用成了“跨视频 ROI 对齐工具”，而不是用来做分类/识别。

---

### 3.3 为什么主打分方法用 SSIM（结构相似性指数），而不是简单帧差

你做的是“裂缝/纹理结构变化”检测，而不是纯运动检测。

- **帧差**（相邻帧像素差）对光照波动、轻微抖动、噪声很敏感，容易误报。
- **结构相似性指数（SSIM）**把图像相似性拆成亮度、对比度、结构三部分，更接近“结构变化”的感知，对裂缝这种结构类变化更友好。[^ssim_paper]

因此你的主方法是 `ssim_ref`：把稳定状态作为参考，检测“相对参考的结构退化”。

---

### 3.4 为什么还要保留一个 baseline（diff_prev 基线方法）

即使 SSIM 是主方法，你仍然保留 `diff_prev`，原因很工程化：

1. **可对照**：当你调参（阈值、平滑窗、ROI）时，基线可以帮助判断“SSIM 是否真的带来收益”。
2. **可兜底**：若 SSIM 受某些情况影响（例如强压缩伪影、亮度突变导致均值/方差项异常），帧差至少还能给一个粗略的变化提示。
3. **可解释**：答辩时你可以说“我们有主方法，也有传统方法作为对照与可靠性保障”。

仓库 README 里也明确写了：`ssim_ref` 是 MVP 主方法，`diff_prev` 是 baseline。[^repo_readme]

---

## 4. 模块级“你具体怎么做的”（实现细节 + 角色解释）

下面按代码真实执行顺序，把每个模块的输入/输出、做了什么、为什么这样做讲清楚。

---

## 4.1 配置与运行入口：三层合并的参数体系

你的配置最终来自三层合并：

1. **代码内默认配置 `DEFAULT_CONFIG`**
2. **`--config xxx.yaml` 覆盖默认**
3. **命令行参数再覆盖（最高优先级）**

这是典型工程化做法：默认可跑，配置可复用，命令行可快速试验。[^pipeline_detailed][^proj_impl]

---

## 4.2 ROI 决策模块（最关键模块）

### 4.2.1 ROI 优先级（你是怎么保证“可控 + 可复现”的）

你给 ROI 设计了清晰优先级：

1. `roi.overrides[video_id]`：手工覆盖（最高优先级）
2. `roi.mode != auto_per_video`：直接用 fixed 规则（横屏/竖屏分别配置）
3. `roi.mode == auto_per_video`：走 ORB 匹配映射；失败就 fallback 回 fixed

这保证了：  
- **一旦自动 ROI 对某个视频不稳定，你可以立刻用 override 钉死**；  
- 失败不会导致整个 pipeline 崩掉（有 fallback）。[^pipeline_detailed][^code_roi_resolve]

---

### 4.2.2 auto_per_video 的“参考池”（reference pool）怎么建

你的 auto ROI 不是“拿第一帧当参考”，而是先选一批参考视频（横屏/竖屏尽量各一个），为每个参考视频存：

- 参考帧的 ORB 关键点 `kp`
- ORB 描述子 `des`
- 参考视频的“基础 ROI”（来自 fixed 规则）
- 参考来源、形状等元信息

这一步的意义是：**先把“参考坐标系 + 参考 ROI”固定住**，后续每个视频只需要“估计到参考的几何映射”。[^code_build_roi_ctx][^pipeline_detailed]

---

### 4.2.3 ORB 在你项目里的角色：从视频里“抽出可匹配的几何锚点”

#### ORB 做了什么

你对代表帧（灰度图）运行 ORB：

- 检测关键点（关键点是局部可重复检测的兴趣点）
- 计算二值描述子（每个关键点对应一个二值向量）

ORB 描述子属于二值特征，匹配时通常用 **Hamming 距离**衡量相似度：[^brief_paper][^orb_paper]

\[
d_H(\mathbf{b}_1,\mathbf{b}_2)=\sum_{j=1}^{L}\mathbb{I}(b_{1j}\neq b_{2j})
\]
[^brief_paper]

这里的 \(d_H\) 就是你在匹配时看到的 `m.distance`（OpenCV 内部用 Hamming 距离），它越小表示两个描述子越相似。[^code_roi_match]

#### “最近邻距离”是什么

你用的是 K 近邻匹配（KNN，k=2）。对于每个参考描述子，会在当前帧的描述子集合里找：

- **最近邻（nearest neighbor）**：距离最小的匹配，记为 \(d_1\)
- **次近邻（second nearest）**：距离第二小的匹配，记为 \(d_2\)

这两个距离就是你比值检验要用到的。[^code_roi_match]

---

### 4.2.4 ratio test（比值检验）在你项目里的角色：过滤“看起来像但其实不靠谱”的匹配

你在代码里使用了 Lowe 提出的比值检验：[^lowe2004]

\[
\text{accept match} \iff d_1 < r\cdot d_2
\]
[^lowe2004]

- \(d_1\)：最近邻距离
- \(d_2\)：次近邻距离
- \(r\)：比值阈值（你配置里默认 `0.75`）

直观解释：

- 如果最近邻和次近邻“差不多近”，说明这个特征点在目标图像里没有唯一匹配（容易错配）。
- 如果最近邻明显更近（通过比值检验），说明匹配更“有辨识度”。

你把 ratio test 放在 RANSAC 之前，是为了**先把明显的错配干掉**，减少 RANSAC 的离群点比例，提高几何估计稳定性。[^code_roi_match][^lowe2004]

---

### 4.2.5 RANSAC 在你项目里的角色：从“有噪声的匹配点对”里鲁棒估计几何变换

比值检验后仍可能存在错配（离群点）。于是你用 RANSAC 做鲁棒拟合：[^ransac1981]

RANSAC 的基本思想（非常适合“离群点很多”的匹配问题）：

1. 随机采样最小点集估计模型参数
2. 用该模型计算所有点的重投影误差
3. 把误差小于阈值的点当作内点（inliers）
4. 迭代多次，选择内点最多的模型

你代码里对应 OpenCV 的：

- `cv2.estimateAffinePartial2D(..., method=cv2.RANSAC, ransacReprojThreshold=...)`
- 并统计 `inlier_count`，只有 `inlier_count >= min_inliers` 才认为几何估计可信。[^code_roi_match][^opencv_calib3d]

RANSAC 的经典出处是 Fischler & Bolles 1981。[^ransac1981]

---

### 4.2.6 相似变换（部分仿射）在你项目里的角色：把“参考 ROI”映射成“当前视频 ROI”

你使用 `estimateAffinePartial2D` 得到一个 \(2\times 3\) 矩阵：

\[
\mathbf{A}=
\begin{bmatrix}
a & b & t_x\\
c & d & t_y
\end{bmatrix}
\]
[^opencv_calib3d]

当它是“相似变换”（旋转 + 等比缩放 + 平移）时，可以写成更直观的形式：[^opencv_calib3d]

\[
\begin{bmatrix}
x'\\y'
\end{bmatrix}
=
\begin{bmatrix}
s\cos\theta & -s\sin\theta\\
s\sin\theta & s\cos\theta
\end{bmatrix}
\begin{bmatrix}
x\\y
\end{bmatrix}
+
\begin{bmatrix}
t_x\\t_y
\end{bmatrix}
\]
[^opencv_calib3d]

**你是怎么把它作用到 ROI 矩形上的：**

- 先取 ROI 四个角点 \((x,y),(x+w,y),(x+w,y+h),(x,y+h)\)
- 用仿射变换把这四点变到新位置
- 再取变换后四点的最小外接矩形作为新 ROI

对应你的实现逻辑：[^code_rect_transform]

\[
x_0=\min_i x'_i,\quad y_0=\min_i y'_i,\quad
x_1=\max_i x'_i,\quad y_1=\max_i y'_i
\]
\[
\text{roi}'=[x_0,y_0,x_1-x_0,y_1-y_0]
\]
[^code_rect_transform]

---

### 4.2.7 refine（局部细化）在你项目里的角色：在“几何对齐结果附近”做小范围搜索，提高 ROI 贴合度

即便有 ORB + RANSAC，ROI 仍可能有轻微偏移（比如构图差异、工件在画面中位置偏左/偏右）。你又加了一个局部搜索：

- 在初始 ROI 中心附近做网格搜索（`search_ratio`, `search_steps`）
- 同时尝试不同尺度（`scales`）
- 用一个启发式评分函数 `roi_candidate_score` 评价每个候选 ROI
- 取分数最高的那一个作为 refined ROI

这是典型的“粗对齐 + 局部优化”结构：ORB 给你一个大致落点，refine 把框贴得更准。[^code_refine]

你评分函数是你项目里的自定义线性组合（属于工程启发式），大意是：

- 奖励纹理边缘（Laplacian）
- 奖励金属区域比例，惩罚绿色背景比例
- 同时对“铜色过量”做惩罚，避免 ROI 偏到纯铜背景
- 还加了“铜区域靠近中心”的偏置项

你的实现（权重）可以写成：[^code_roi_score]

\[
\text{score}=
1.25\cdot \text{edge}
+0.95\cdot \text{metal}
+0.40\cdot (1-\text{green})
+0.25\cdot \text{copper}
-1.40\cdot \max(0,\text{copper}-0.20)
+\text{center\_bonus}
\]
[^code_roi_score]

> 注：这不是学术标准公式，而是你为具体场景设计的启发式评分；答辩时可以强调“我们把领域先验（颜色/材质/纹理）编码进了 ROI 质量评价”。

---

### 4.2.8 post_shift（自适应 ROI 后处理）在你项目里的角色：纠正“系统性构图偏差”

你还加了一个可选的 ROI 后处理平移：

- `fixed`：按固定像素/比例平移
- `adaptive_copper`：在 ROI 内统计铜色区域质心，目标是把质心推到指定的横向位置（`post_shift_target_x`），并限制最大平移比例与方向（`left_only/right_only/both`）

从实现上看，它是在 ORB 映射 + refine 之后执行的“最后校准”。[^proj_impl][^code_post_shift]

---

## 4.3 score curve：把视频压缩成一条“随时间变化强度”的曲线

### 4.3.1 你是怎么“压缩”出来的（为什么能压缩）

你没有对每一帧都算分数，而是：

1. 用 `coarse_fps` 在时间轴上均匀采样  
2. 每个采样点只处理 ROI 区域，并可缩放到固定宽度 `resize_w`  
3. 计算一个分数 \(score(t)\)

这相当于把原始高维视频序列压缩成一维时间序列（曲线），大幅降低计算量。你在代码里用类似下面的采样规则：[^code_sample_points]

\[
n=\lfloor T\cdot f_c\rfloor + 1,\quad
t_i=\frac{i}{n-1}T,\quad
\text{idx}_i=\text{round}(t_i\cdot fps)
\]
[^code_sample_points]

其中：

- \(T\) 是视频时长（秒）
- \(f_c\) 是粗采样帧率（`coarse_fps`）
- `idx_i` 是对应的帧索引

---

### 4.3.2 方法一：ssim_ref（主方法）

#### (1) 参考帧怎么来的

你不是选“第一帧”当参考，而是取前 `ssim_ref_n_ref` 个采样点对应的 ROI 灰度图，做逐像素中位数作为参考：[^code_score_curve]

\[
I_{\text{ref}}(p)=\text{median}\{I_{t_1}(p),\dots,I_{t_K}(p)\}
\]
[^code_score_curve]

这样做的意义：

- 如果一开始有轻微遮挡/抖动，中位数参考能比单帧更鲁棒。

#### (2) SSIM 你是怎么计算的

你实现的是“全局版”结构相似性指数（用全图均值、方差、协方差），核心形式是：[^ssim_paper][^code_ssim]

\[
\text{SSIM}(x,y)=
\frac{(2\mu_x\mu_y+C_1)(2\sigma_{xy}+C_2)}
{(\mu_x^2+\mu_y^2+C_1)(\sigma_x^2+\sigma_y^2+C_2)}
\]
[^ssim_paper]

你代码里用的常数形式与原论文一致（\(C_1=(0.01L)^2, C_2=(0.03L)^2\)，8 位图像 \(L=255\)）。[^code_ssim][^ssim_paper]

#### (3) 最终打分

你把“相似度”改成“变化强度”：

\[
\text{score}(t)=1-\text{SSIM}(I_{\text{ref}}, I_t)
\]
[^ssim_paper][^code_score_curve]

解释：

- 结构越接近参考，SSIM 越高，score 越低；
- 裂缝发展导致结构差异增大，SSIM 下降，score 上升。

---

### 4.3.3 方法二：diff_prev（基线）

你实现的是经典帧间差分思想：[^frame_diff_springer][^code_score_curve]

\[
\text{score}(t)=\frac{1}{N}\sum_{p\in ROI}\frac{|I_t(p)-I_{t-1}(p)|}{255}
\]
[^frame_diff_springer]

#### 分母为什么是 255？

因为 ROI 灰度图是 8 位像素，像素值范围通常是 \([0,255]\)。除以 255 就把平均绝对差归一化到 \([0,1]\) 附近，便于不同视频/不同曝光条件之间比较，也让阈值（quantile 或 MAD）更稳定。[^code_score_curve]

---

## 4.4 平滑、阈值与分段：从“噪声曲线”变成“稳定候选片段”

### 4.4.1 平滑怎么做的？什么是滑动卷积核？W 是什么？

你用的是滑动平均（moving average）：

- 取窗口长度 \(W\)（对应 `smooth_win`）
- 用长度为 \(W\) 的矩形核做卷积

一维滑动平均常见写法：[^oppenheim_ma][^code_smooth]

\[
y[n]=\frac{1}{W}\sum_{k=0}^{W-1}x[n-k]
\]
[^oppenheim_ma]

等价的卷积形式：[^oppenheim_ma]

\[
y = x * h,\quad h[k]=\frac{1}{W}\ (k=0,\dots,W-1)
\]
[^oppenheim_ma]

在实现上，你用 `np.convolve(values, kernel, mode="same")`。[^code_smooth]

**为什么要平滑：**

- score 曲线会有高频尖峰（噪声、轻微遮挡、单帧抖动），直接阈值会产生碎片化片段；
- 平滑相当于低通滤波，保留“持续性变化趋势”。[^oppenheim_ma]

---

### 4.4.2 自适应阈值用的是哪一种？

你的代码支持两种阈值模式：`quantile` 与 `mad`。[^code_detect_segments][^proj_impl]

#### A. 分位数阈值（quantile）

\[
\tau = Q_q(\{y[n]\})
\]
[^quantile_paper]

- \(Q_q\) 表示样本的 \(q\) 分位数
- 你在 `config.rawdata.autoroi.yaml` 默认用了 `q=0.98`（即取高分尾部 2%）[^code_config_autoroi]
- 在 `fullcrack` 配置里用了 `q=0.96`，配合 run 策略更强调“过程覆盖”[^code_config_fullcrack]

分位数定义在统计软件里有多个实现，Hyndman & Fan 系统整理了常见定义与差异。[^quantile_paper]

#### B. 中位数绝对偏差阈值（MAD）

你实现的是典型鲁棒阈值形式：[^mad_paper][^code_detect_segments]

\[
\text{MAD}=\text{median}(|y-\text{median}(y)|)
\]
\[
\tau=\text{median}(y)+k\cdot \text{MAD}
\]
[^mad_paper]

MAD 属于鲁棒统计尺度估计，对少量极端离群值不敏感。[^mad_paper]

---

### 4.4.3 分段（segments）怎么做的：run 与 peak 两种策略

你先找出所有满足 \(y[n]\ge \tau\) 的点，然后把连续为真的区间称为一个 run。[^code_detect_segments]

然后把 run 转成候选片段，有两种策略：

#### 策略 1：peak（峰值定长）

- 在 run 内找峰值位置 \(t_{peak}\)
- 以峰值为中心截取固定长度 \(L\)（`segment_len_sec`）[^code_detect_segments]

\[
t_{start}=\max(0, t_{peak}-L/2),\quad
t_{end}=\min(T, t_{start}+L)
\]
[^code_detect_segments]

适用：你更关心“变化最剧烈的瞬间”。

#### 策略 2：run（超阈区间 + 留白）

- 先取 run 的边界时间 \([t_a, t_b]\)
- 再加前后留白：`run_pre_pad_sec`、`run_post_pad_sec`[^code_detect_segments]

\[
t_{start}=\max(0, t_a-\Delta_{pre}),\quad
t_{end}=\min(T, t_b+\Delta_{post})
\]
[^code_detect_segments]

适用：你更关心“裂缝演化的完整过程覆盖”。  
这也是你 `fullcrack` 配置里强调的策略。[^code_config_fullcrack]

---

### 4.4.4 合并与 TopK：为什么要 merge_gap_sec、topk、fill_to_topk

- `merge_gap_sec`：相邻片段太近就合并，避免碎片化。[^code_detect_segments]
- `topk`：每个视频最多输出 K 段，保证后续抽帧量可控。[^code_detect_segments]
- `fill_to_topk`：如果 run 不够，用局部峰补齐（同时避免与已有峰太近）。[^code_detect_segments]

这是一套非常典型的“检测 → 聚合 → 排序 → 截取”的时序异常检测后处理流程。

---

## 4.5 精抽帧与导出：只对候选片段做高帧率抽帧

### 4.5.1 抽帧时间点与帧索引怎么对应

你在片段 \([t_{start},t_{end}]\) 内按 `fine_fps` 生成时间点，再映射到帧索引：[^code_extract_points]

\[
t_i=t_{start}+i\cdot \frac{1}{f_f},\quad
\text{idx}_i=\text{round}(t_i\cdot fps)
\]
[^code_extract_points]

并做去重与边界裁剪，防止帧索引越界或重复。

---

### 4.5.2 输出什么

对每个 segment，你输出：

- `frames/`：原始全帧图（或按配置保留）
- `crops/`：按 ROI 裁剪图（你的主产物）
- `manifest.csv`：每张图的时间戳、来源帧号、路径、ROI 坐标
- `preview.jpg`：代表帧拼图（contact sheet），便于快速复核

这些输出在 `PIPELINE_DETAILED.md` 和实现详解里写得很明确。[^pipeline_detailed][^proj_impl]

---

### 4.5.3 crop_resize 的 letterbox 是怎么做的

如果你设置了 `crop_resize: [W,H]` 并选择 `letterbox`，其核心是：

- 先按比例缩放使得缩放后图像完全落入目标尺寸
- 再用固定像素值填充空白边（`crop_resize_pad_value`）

缩放比例：[^code_letterbox]

\[
s=\min\left(\frac{W}{w},\frac{H}{h}\right)
\]
[^code_letterbox]

这种做法的好处：**不拉伸形状**，利于后续学习模型或人工比对一致尺度。  

---

## 5. 针对你之前提的几个“容易混淆点”，在项目语境下再总结一遍

### 5.1 ORB / ratio test / RANSAC / 相似变换：它们分别对“输入视频”做了什么？

你输入的是一个视频，但在 auto ROI 阶段，你实际上只取了一个“代表帧”（或者极少量帧）来做几何对齐。流程可总结为：

1. **ORB**：把“代表帧”变成一堆可匹配的关键点 + 二值描述子（几何锚点）。[^orb_paper][^code_roi_match]
2. **ratio test**：从匹配候选里筛掉“没有唯一性”的不可靠匹配。[^lowe2004][^code_roi_match]
3. **RANSAC**：在仍包含错配的匹配点对里，鲁棒估计几何模型，并输出内点集合。[^ransac1981][^code_roi_match]
4. **相似变换/部分仿射**：把参考视频的 ROI 坐标，通过估计的变换映射到当前视频坐标，得到当前视频的 ROI。[^opencv_calib3d][^code_rect_transform]

也就是说：**它们不是“对整个视频逐帧做事情”，而是主要服务于“每个视频选一张代表帧，完成 ROI 对齐”这件事。**

---

### 5.2 分数曲线是怎么“压缩”出来的？

压缩发生在两层：

1. **时间维压缩**：每秒不处理全部帧，只处理 `coarse_fps` 个采样点。[^code_sample_points]
2. **空间维压缩**：只处理 ROI，并可缩放到 `resize_w`（比如 320）。[^code_read_roi]

所以最后变成一条很短的一维数组 `score_arr`，后续分段就很快。

---

### 5.3 SSIM 在你项目里具体怎么做的？

你实现的是全局 SSIM（而不是滑窗 SSIM），核心统计量是：

- 均值 \(\mu_x,\mu_y\)
- 方差 \(\sigma_x^2,\sigma_y^2\)
- 协方差 \(\sigma_{xy}\)

然后按论文公式计算 SSIM，再取 `1-SSIM` 作为变化分数。[^ssim_paper][^code_ssim]

---

### 5.4 平滑曲线具体又是怎么做的？

就是对 `score_arr` 做一维滑动平均卷积，窗口长度为 `smooth_win`，卷积核每个位置权重为 \(1/W\)。[^oppenheim_ma][^code_smooth]

---

## 6. “换个角度/分辨率拍的其他视频，还能用吗？”

### 6.1 分辨率变化：通常能用

只要内容相同、视角变化不是特别极端：

- auto ROI 是基于关键点坐标的几何映射，本质不依赖固定分辨率；
- 最终 ROI 会被边界裁剪（clip），抽帧与裁剪也按实际像素坐标进行。[^code_roi_resolve]

### 6.2 角度变化：取决于是否能被“相似变换/部分仿射”解释

你当前用的是 `estimateAffinePartial2D`，它可以描述：

- 旋转
- 等比缩放
- 平移（以及一定程度的仿射变化）

但如果新视频的视角变化导致明显透视畸变（例如大幅度的俯仰角变化、强透视），单纯的相似/仿射模型可能不够，需要更强的模型（例如单应性 homography）或引入更多参考/更强的特征。[^opencv_calib3d]

### 6.3 工程上你已经做了“失败保护”

- 匹配不足会 fallback 到 fixed ROI；
- 你还能用 `roi.overrides` 对个别困难视频做手工纠正。[^proj_impl]

---

## 7. 调参建议（你可以直接拿去做 PPT 的“经验总结页”）

仓库 README 里给了很实用的建议，你也在配置里做了两个典型 profile：[^repo_readme][^code_config_autoroi][^code_config_fullcrack]

### 7.1 误报高（false positives 高）

- 缩小 ROI（减少背景变化）
- 提高分位数阈值 `threshold.q`（例如 0.98 → 0.995）
- 增大平滑窗 `smooth_win`（例如 7 → 11）

### 7.2 漏检（没抓到完整裂缝演化）

- 换用 `segment_strategy=run`（你 fullcrack 配置就是这么做的）
- 适当降低阈值（比如 q 从 0.98 降到 0.96）
- 增加 `run_post_pad_sec` 或 `segment_len_sec`
- 提高 `coarse_fps`（更密采样，避免错过短事件）

### 7.3 auto ROI 不稳定

- 增大 `orb_nfeatures`（特征点更多）
- 适当降低 `ratio_test`（更严格的匹配）
- 提高 `min_inliers`（更严格的几何可信度）
- 或对个别视频用 overrides 固定 ROI

---

## 8. 答辩/展示可以怎么讲（可直接照念）

> 我们的目标是从裂缝视频中自动定位“裂缝演化最明显的时间片段”，并对这些片段高帧率抽帧导出 ROI 裁剪图，形成可复核的数据集。  
> 
> 方法上，我们先用 ORB 特征匹配把不同视频的目标区域对齐：通过比值检验过滤不可靠匹配，再用 RANSAC 鲁棒估计相似变换，把参考 ROI 映射到当前视频。这样能适配不同分辨率和一定程度的角度变化。  
> 
> 接着我们只在 ROI 内做粗采样评分，把视频压缩成一条分数曲线。主方法用结构相似性指数 SSIM，相对于简单帧差更关注结构变化，更符合裂缝这种结构异常。我们也保留帧差基线用于对照与兜底。  
> 
> 在分数曲线上，我们用滑动平均平滑，再用分位数或 MAD 等自适应阈值找到异常区间，并生成 TopK 候选片段。最后只对候选片段做精抽帧，输出 frames、ROI crops、manifest 和 preview 拼图，并生成全局 HTML 报告，保证全过程可追溯与可复核。  
> 
> 因此整个系统兼顾了可解释性、鲁棒性与工程落地效率。

---

## 参考与代码索引（建议放文末，或拆成 PPT 参考页）

### 仓库/代码索引（实现依据）

- 仓库 README（scope、CLI、调参 tips）：`README.md`[^repo_readme]  
- 执行版流程说明：`PIPELINE_DETAILED.md`[^pipeline_detailed]  
- 工程实现详解：`PROJECT_IMPLEMENTATION_DETAILS.md`[^proj_impl]  
- 主程序：`crack_analyze.py`（ROI、评分、分段、抽帧、报告都在这里）[^code_crack_analyze]

### 学术参考（算法依据）

[^ssim_paper]: Zhou Wang, Alan C. Bovik, Hamid R. Sheikh, Eero P. Simoncelli, “Image quality assessment: from error visibility to structural similarity,” *IEEE Transactions on Image Processing*, 2004. DOI: https://doi.org/10.1109/TIP.2003.819861 （可从预印本 PDF 阅读：https://www.cns.nyu.edu/pub/lcv/wang03-reprint.pdf）  
[^orb_paper]: Ethan Rublee, Vincent Rabaud, Kurt Konolige, Gary Bradski, “ORB: An efficient alternative to SIFT or SURF,” *ICCV*, 2011. DOI: https://doi.org/10.1109/ICCV.2011.6126544 （公开 PDF：https://sites.cc.gatech.edu/classes/AY2024/cs4475_summer/images/ORB_an_efficient_alternative_to_SIFT_or_SURF.pdf）  
[^lowe2004]: David G. Lowe, “Distinctive Image Features from Scale-Invariant Keypoints,” *International Journal of Computer Vision*, 2004. 论文 PDF：https://people.eecs.berkeley.edu/~malik/cs294/lowe-ijcv04.pdf  
[^ransac1981]: Martin A. Fischler, Robert C. Bolles, “Random Sample Consensus: A Paradigm for Model Fitting with Applications to Image Analysis and Automated Cartography,” *Communications of the ACM*, 1981. DOI: https://doi.org/10.1145/358669.358692  
[^brief_paper]: Michael Calonder, Vincent Lepetit, Christoph Strecha, Pascal Fua, “BRIEF: Binary Robust Independent Elementary Features,” *ECCV*, 2010. DOI: https://doi.org/10.1007/978-3-642-15561-1_56 （公开 PDF：https://www.cs.ubc.ca/~lowe/525/papers/calonder_eccv10.pdf）  
[^quantile_paper]: Rob J. Hyndman, Yanan Fan, “Sample Quantiles in Statistical Packages,” *The American Statistician*, 1996. PDF：https://robjhyndman.com/papers/sample_quantiles.pdf  
[^mad_paper]: Peter J. Rousseeuw, Christophe Croux, “Alternatives to the Median Absolute Deviation,” *Journal of the American Statistical Association*, 1993. PDF：https://wis.kuleuven.be/stat/robust/papers/publications-1993/rousseeuwcroux-alternativestomedianad-jasa-1993.pdf  
[^frame_diff_springer]: Q. L. Zhang et al., “Moving Object Detection Method Based on the Fusion of …,” *Multimedia Tools and Applications*, 2024（文中对帧差法做了概念性描述，可作为 frame differencing 的引用）。https://link.springer.com/article/10.1007/s11063-024-11463-w  
[^oppenheim_ma]: Alan V. Oppenheim 等，*Discrete-Time Signal Processing*（节选预览包含 moving-average 系统公式）。预览 PDF：https://api.pageplace.de/preview/DT0400.9781292038155_A24581738/preview-9781292038155_A24581738.pdf  
[^opencv_calib3d]: OpenCV 文档：Camera Calibration and 3D Reconstruction / calib3d（含 estimateAffinePartial2D 等函数说明）。https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html  

### 代码实现引用（对应本文中“公式/流程”的实现出处）

[^repo_readme]: https://raw.githubusercontent.com/KTH-YANGYI/frameextraction/master/README.md  
[^pipeline_detailed]: https://raw.githubusercontent.com/KTH-YANGYI/frameextraction/master/PIPELINE_DETAILED.md  
[^proj_impl]: https://raw.githubusercontent.com/KTH-YANGYI/frameextraction/master/PROJECT_IMPLEMENTATION_DETAILS.md  
[^code_crack_analyze]: https://raw.githubusercontent.com/KTH-YANGYI/frameextraction/master/crack_analyze.py  

[^code_config_autoroi]: https://raw.githubusercontent.com/KTH-YANGYI/frameextraction/master/config.rawdata.autoroi.yaml  
[^code_config_fullcrack]: https://raw.githubusercontent.com/KTH-YANGYI/frameextraction/master/config.rawdata.fullcrack.yaml  

[^code_roi_resolve]: `resolve_video_roi` / `resolve_roi_rect` / `clip_roi` 的实现位于 `crack_analyze.py`（auto ROI 与 fallback 的核心逻辑）。见：[^code_crack_analyze]  
[^code_build_roi_ctx]: `build_auto_roi_context` / `choose_reference_videos` 的实现位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_roi_match]: ORB 提取、BFMatcher KNN、ratio test、RANSAC 仿射估计位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_rect_transform]: `transform_rect_with_affine` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_refine]: `refine_roi_rect` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_roi_score]: `roi_candidate_score` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_post_shift]: `resolve_post_shift` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  

[^code_sample_points]: `sample_points` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_read_roi]: `read_gray_roi` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_ssim]: `ssim_global` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_score_curve]: `compute_score_curve` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  

[^code_smooth]: `smooth_series` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_detect_segments]: `detect_segments` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  

[^code_extract_points]: `extraction_points` 位于 `crack_analyze.py`。见：[^code_crack_analyze]  
[^code_letterbox]: `resize_crop_image`（letterbox）位于 `crack_analyze.py`。见：[^code_crack_analyze]  

