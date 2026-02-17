# 图像质量评估：从误差可见性到结构相似性

**Zhou Wang**，会员，IEEE，**Alan Conrad Bovik**，会士，IEEE，**Hamid Rahim Sheikh**，学生会员，IEEE，以及 **Eero P. Simoncelli**，会员，IEEE

---

## 摘要

提出了一种新的基于结构退化的全参考图像质量指数，用于测量图像失真。所提出的方法基于这样一种假设：人类视觉系统高度适应于从视野中提取结构信息。因此，结构信息丢失的度量应与感知图像质量有很好的相关性。我们提出了一种结构相似性（SSIM）质量评估方法，基于对结构信息从参考图像到失真图像的退化进行分解，包括三个部分：亮度、对比度和结构对比。基于从主观图像数据库导出的心理学实验，我们证明了所提出的方法在预测感知图像质量方面优于传统方法，如均方误差（MSE）。

**索引词**——图像质量评估，图像失真，人类视觉系统（HVS），结构相似性，误差可见性。

---

## I. 引言

数字图像在获取、处理、压缩、存储、传输和复制过程中经常遭受各种退化。图像质量评估在图像处理应用中起着基础性作用，既可以作为质量监控手段，也可以作为算法性能的基准。图像质量度量可以根据参考图像的可用性分为三类：1）全参考方法，其中可以访问到原始"完美"图像；2）无参考方法，其中没有参考图像；3）部分参考方法，其中参考图像仅部分可用或提取出一组特征作为参考。

最简单且最广泛使用的全参考质量度量是均方误差（MSE），通过平均被测试图像和参考图像中像素值的平方差来计算。MSE之所以广泛使用，是因为它具有清晰的物理意义——它是能量误差的度量——并且易于在数学上优化。然而，MSE（及其平方根PSNR）并不能很好地与感知质量相关联。如图1所示，具有显著不同视觉质量的图像可能具有相同的MSE（以及相同的PSNR）。这一观察结果是许多致力于开发更好的图像质量度量方法的研究工作的动力，这些方法试图结合人类视觉系统（HVS）的知识。

在过去的三十年里，人们投入了大量努力来理解人类视觉系统以及它如何感知图像质量。使用最广泛的图像质量相关HVS特性是失真的敏感度，它取决于局部图像内容的局部均值（亮度）、对比度和结构[1]-[5]。大多数图像质量评估方法被设计为误差可见性的度量[6]-[12]。典型的方法是首先根据已知的HVS特性量化信号误差，然后合并这些误差。信号误差的非线性变换（如对比度掩蔽和亮度掩蔽）被广泛使用。其他心理物理学现象，如视觉注意和频率选择性，也被结合到质量评估过程中[13]-[16]。视觉神经科学和心理学研究进展表明，初级视觉皮层和视网膜神经节细胞的处理可以通过使用线性滤波器来建模，这些滤波器在空间位置和频率上是局部化的，在方向上是选择性的[17]，[18]。受这些发现的启发，提出了许多基于多尺度滤波器组分解的质量度量方法[19]-[22]。

尽管这些基于误差敏感度的方法在许多应用中取得了成功，但它们也有局限性。首先，误差敏感度方法依赖于视觉心理物理学来理解HVS的功能，但人类视觉系统的复杂性和目前知识的局限性限制了误差敏感度模型的质量。其次，当前的HVS模型大多基于简单的模式和基础的心理物理学实验，这些实验可能无法很好地推广到现实世界的复杂自然图像。第三，问题的公式化通常涉及参数（如加权指数和截断常数）的选择，这些参数必须调整以使模型输出与感知质量良好匹配，但模型构建缺乏原则性的方法。第四，大多数方法假设误差的可见性等同于质量的损失，但在实践中，并非所有可见的差异都会影响感知质量。例如，通过简单缩放对比度（线性缩放）获得的图像与其原始版本可能有显著差异，但众所周知，人类视觉系统对整体强度的变化不太敏感，因此整体强度变化可能几乎不会损害图像质量。

在本文中，我们提出了一种全新的方法来进行图像质量测量。我们不再寻找心理物理学或视觉神经科学来为信号误差的可见性建模，而是转向针对自然图像信号的结构。我们假设人类视觉系统高度适应于从视野中提取结构信息。因此，结构信息丢失的度量应与感知图像质量有很好的相关性。当然，经典的失真度量如MSE从某种意义上说也是在度量结构信息的丢失——但许多类型的结构退化（如对比度拉伸）并没有被MSE很好地捕捉。

在第二节中，我们制定了一个结构相似性指数，作为图像质量的一种度量方法，并讨论了其属性。第三节介绍了SSIM方法的一种实现，其中我们讨论了如何结合许多局部度量来创建全局质量度量。第四节描述了为测试SSIM方法并与其他度量进行比较而进行的实验。第五节总结本文。

---

## II. 结构相似性指数

让 $x$ 和 $y$ 分别是原始信号和失真信号。如果我们认为其中一个信号具有"完美"质量，那么这两个信号之间的相似性可以被解释为另一个信号的质量度量。我们提出的系统将相似性测量分为三个部分：亮度比较、对比度比较和结构比较，如图2所示。设 $l(x,y)$、$c(x,y)$ 和 $s(x,y)$ 分别为亮度、对比度和结构的比较函数。那么，$x$ 和 $y$ 之间的整体相似度可以定义为三个部分的乘积：

$$S(x,y) = f(l(x,y), c(x,y), s(x,y)) \quad (1)$$

我们期望 $l(x,y)$、$c(x,y)$ 和 $s(x,y)$ 以及整体函数 $f(\cdot)$ 满足以下属性：

1. **对称性**：$S(x,y) = S(y,x)$；
2. **有界性**：$S(x,y) \leq 1$；
3. **唯一最大值**：$S(x,y) = 1$ 当且仅当 $x = y$（对于离散信号，$x_i = y_i$ 对所有 $i=1,\ldots,N$）。

### A. 亮度比较函数

亮度被估计为信号强度的平均值。设 $\mu_x$、$\mu_y$ 分别为 $x$、$y$ 的平均值，则亮度比较函数定义为：

$$l(x,y) = \frac{2\mu_x\mu_y + C_1}{\mu_x^2 + \mu_y^2 + C_1} \quad (2)$$

其中常数 $C_1$ 用于在 $\mu_x^2 + \mu_y^2$ 接近零时避免数值不稳定。具体来说：

$$C_1 = (K_1 L)^2 \quad (3)$$

其中 $L$ 是像素值的动态范围（对于8位灰度图像，$L=255$），$K_1$ 是一个小常数（我们使用的值为 $K_1 \ll 1$）。

### B. 对比度比较函数

信号的标准差被用作对比度的估计。设 $\sigma_x$ 和 $\sigma_y$ 分别是 $x$ 和 $y$ 的标准差的无偏估计。对比度比较函数取类似的形式：

$$c(x,y) = \frac{2\sigma_x\sigma_y + C_2}{\sigma_x^2 + \sigma_y^2 + C_2} \quad (4)$$

其中：

$$C_2 = (K_2 L)^2 \quad (5)$$

且 $K_2 \ll 1$。注意，在（2）和（4）中使用常数 $C_1$ 和 $C_2$ 的方式遵循韦伯定律，该定律已被广泛用于模拟HVS中的亮度掩蔽效应[1]，[3]。

### C. 结构比较函数

结构比较在亮度归一化（减去均值）和对比度归一化（除以标准差）后的信号上进行。我们用 $x$ 和 $y$ 与其自身之间的相关性来量化两个信号之间的结构相似性：

$$s(x,y) = \frac{\sigma_{xy} + C_3}{\sigma_x\sigma_y + C_3} \quad (6)$$

其中 $\sigma_{xy}$ 可以估计为：

$$\sigma_{xy} = \frac{1}{N-1}\sum_{i=1}^{N}(x_i - \mu_x)(y_i - \mu_y) \quad (7)$$

### D. 结构相似性指数的组合

重要的是，这三个部分是相对独立的。例如，亮度的变化不会影响对比度和结构比较。这种分离使我们能够灵活地调整每个部分的权重。

将（2）、（4）和（6）组合到（1）中的通用形式，我们得到：

$$SSIM(x,y) = [l(x,y)]^\alpha \cdot [c(x,y)]^\beta \cdot [s(x,y)]^\gamma \quad (8)$$

其中 $\alpha>0$、$\beta>0$ 和 $\gamma>0$ 是用于调整三个部分相对重要性的参数。为简单起见，我们设 $\alpha=\beta=\gamma=1$，并设 $C_3 = C_2/2$。这导致SSIM指数的一种特殊形式：

$$SSIM(x,y) = \frac{(2\mu_x\mu_y + C_1)(2\sigma_{xy} + C_2)}{(\mu_x^2 + \mu_y^2 + C_1)(\sigma_x^2 + \sigma_y^2 + C_2)} \quad (9)$$

### E. 与其他度量的关系

SSIM指数可以与现有的质量度量相关联。例如，如果设 $C_1 = C_2 = 0$，（9）式变为：

$$SSIM(x,y) = \frac{2\mu_x\mu_y \cdot 2\sigma_{xy}}{(\mu_x^2 + \mu_y^2)(\sigma_x^2 + \sigma_y^2)} \quad (10)$$

这与通用图像质量指数[23]具有相似的形式。

SSIM指数满足第一节中提出的三个属性：
1. 对称性是显而易见的；
2. 由于分子和分母的选择，$SSIM(x,y) \leq 1$ 总是成立；
3. $SSIM(x,y) = 1$ 当且仅当 $x = y$。

---

## III. SSIM在图像质量评估中的应用

### A. 基于SSIM的图像质量度量

由于图像统计特征具有高度的空间非平稳性，我们预期图像质量（局部地）也是空间变化的。因此，有必要将SSIM指数应用于局部图像区域而不是全局应用。我们使用一个 $11 \times 11$ 的对称高斯加权函数 $w = \{w_i | i=1,2,\ldots,N\}$，标准差为1.5样本，形成一个圆形对称的加权模式，单位体积归一化为 $\sum_{i=1}^{N}w_i = 1$。局部统计量 $\mu_x$、$\sigma_x$ 和 $\sigma_{xy}$ 可以估计为：

$$\mu_x = \sum_{i=1}^{N}w_i x_i \quad (11)$$

$$\sigma_x = \sqrt{\sum_{i=1}^{N}w_i(x_i - \mu_x)^2} \quad (12)$$

$$\sigma_{xy} = \sum_{i=1}^{N}w_i(x_i - \mu_x)(y_i - \mu_y) \quad (13)$$

使用这些基于局部统计量的局部SSIM指数，可以计算整个图像的质量测度。

### B. 全局质量度量

设 $X$ 和 $Y$ 分别是参考图像和失真图像。局部SSIM指数 $SSIM(X,Y)$ 在整个图像上计算。总体质量度量可以定义为局部SSIM指数的平均值：

$$MSSIM(X,Y) = \frac{1}{M}\sum_{j=1}^{M}SSIM(X_j,Y_j) \quad (14)$$

其中 $X_j$ 和 $Y_j$ 是位置 $j$ 处的图像块，$M$ 是图像块的数量。

### C. 参数选择

我们建议使用以下默认参数设置：
- $K_1 = 0.01$
- $K_2 = 0.03$
- $L = 255$（对于8位灰度图像）
- 高斯加权函数窗口大小：$11 \times 11$
- 高斯加权函数标准差：1.5

这些参数是基于我们的实验经验选择的，并且在广泛的应用中被证明是有效的。

---

## IV. 实验结果

为了验证所提出的SSIM方法的有效性，我们使用了来自实验室图像和视频工程（LIVE）数据库的主观质量评分。该数据库包含各种类型的失真图像，包括：
- JPEG压缩
- JPEG2000压缩
- 高斯噪声污染
- 高斯模糊
- 快衰落信道传输误差

### A. 实验方法

我们计算了SSIM指数与主观评分（以差异平均意见分DMOS的形式给出）之间的相关性。为了进行比较，我们还计算了PSNR与DMOS之间的相关性。

### B. 结果分析

实验结果表明，SSIM指数在所有测试的失真类型上都表现出与主观质量评分的更强相关性。特别是：

1. **JPEG压缩**：SSIM指数比PSNR更好地捕捉了块效应的感知影响。
2. **JPEG2000压缩**：SSIM指数有效地检测了由小波压缩引起的人工痕迹。
3. **高斯噪声**：SSIM指数与感知噪声水平良好相关。
4. **高斯模糊**：SSIM指数正确地量化了细节丢失。
5. **快衰落传输**：SSIM指数成功地度量了传输错误引起的局部退化。

### C. 与其他方法的比较

我们将SSIM与以下传统图像质量度量进行了比较：
- 均方误差（MSE）
- 峰值信噪比（PSNR）
- Sarnoff的视觉差异预测器（VDP）
- 其他感知质量度量

结果表明，SSIM在预测主观质量方面始终优于这些传统方法。

---

## V. 结论

本文提出了一种基于结构相似性的新型全参考图像质量评估方法。该方法基于这样一种假设：人类视觉系统的主要功能是从视野中提取结构信息，因此结构相似性的度量应与感知图像质量良好相关。

所提出的SSIM指数具有以下优点：
1. **简单直观**：数学公式简洁明了，易于实现
2. **计算效率高**：不需要复杂的多尺度分解或迭代优化
3. **对各种失真类型有效**：对多种类型的图像退化都能提供良好的质量预测
4. **与主观感知一致**：在广泛的测试中显示出与人类主观评分的良好相关性

未来的工作可能包括：
- 扩展到彩色图像
- 将SSIM集成到图像处理算法中作为优化目标
- 开发视频质量评估的SSIM变体

我们相信，SSIM方法为图像质量评估领域提供了一个有价值的工具，可以广泛应用于图像处理、压缩和传输系统中。

---

## 参考文献

[1] A. J. Ahumida and J. P. Thomas, "Image quality assessment," in *Handbook of Perception and Human Performance*, K. Boff, L. Kaufman, and J. Thomas, Eds. New York: Wiley, 1986, vol. 1, ch. 22.

[2] T. N. Pappas and R. J. Safranek, "Perceptual criteria for image quality evaluation," in *Handbook of Image and Video Processing*, A. C. Bovik, Ed. New York: Academic, 2000, pp. 669–684.

[3] B. Girod, "What's wrong with mean-squared error?," in *Digital Images and Human Vision*, A. B. Watson, Ed. Cambridge, MA: MIT Press, 1993, pp. 207–220.

[4] A. B. Watson, "DCT quantization matrices visually optimized for individual images," in *Proc. SPIE Conf. Human Vision, Visual Processing, and Digital Display IV*, 1993, vol. 1913, pp. 202–216.

[5] P. C. Teo and D. J. Heeger, "Perceptual image distortion," in *Proc. SPIE Conf. Human Vision, Visual Processing, and Digital Display V*, 1994, vol. 2179, pp. 127–141.

[6] J. L. Mannos and D. J. Sakrison, "The effects of a visual fidelity criterion of the encoding of images," *IEEE Trans. Inform. Theory*, vol. IT-20, pp. 525–536, July 1974.

[7] J. O. Limb, "Distortion criteria of the human viewer," *IEEE Trans. Syst., Man, Cybern.*, vol. SMC-9, pp. 778–793, 1979.

[8] F. X. J. Lukas and Z. L. Budrikis, "Picture quality prediction based on a visual model," *IEEE Trans. Commun.*, vol. COM-30, pp. 1679–1692, July 1982.

[9] S. Daly, "The visible difference predictor: An algorithm for the assessment of image fidelity," in *Digital Images and Human Vision*, A. B. Watson, Ed. Cambridge, MA: MIT Press, 1993, pp. 179–206.

[10] J. Lubin, "A visual discrimination model for imaging system design and evaluation," in *Visual Models for Target Detection and Recognition*, E. Peli, Ed. Singapore: World Scientific, 1995, pp. 245–283.

[11] R. J. Safranek and J. D. Johnston, "A perceptually tuned sub-band image coder with image dependent quantization and post-quantization data compression," in *Proc. ICASSP*, 1989, vol. 3, pp. 1945–1948.

[12] A. B. Watson, "The cortex transform: Rapid computation of simulated neural images," *Comput. Vis., Graph., Image Processing*, vol. 39, pp. 311–327, 1987.

[13] T. G. Stockman, "Current methods of image quality assessment," *J. Soc. Motion Picture Television Eng.*, vol. 81, pp. 1054–1062, 1972.

[14] C. J. van den Branden Lambrecht and O. Verscheure, "Perceptual quality measure using a spatio-temporal model of the human visual system," in *Proc. SPIE Conf. Digital Video Compression: Algorithms and Technologies*, 1996, vol. 2668, pp. 450–461.

[15] J. G. Robson and N. Graham, "Probability summation and regional variation in contrast sensitivity across the visual field," *Vision Res.*, vol. 21, pp. 409–418, 1981.

[16] A. P. Bradley, "A wavelet visible difference predictor," *IEEE Trans. Image Processing*, vol. 8, pp. 717–730, May 1999.

[17] J. G. Daugman, "Two-dimensional spectral analysis of cortical receptive field profiles," *Vis. Res.*, vol. 20, pp. 847–856, 1980.

[18] S. Marcelja, "Mathematical description of the responses of simple cortical cells," *J. Opt. Soc. Amer.*, vol. 70, pp. 1297–1300, 1980.

[19] S. Daly, "Subroutine for the generation of a two dimensional human visual contrast sensitivity function," in *Proc. SPIE Conf. Human Vision, Visual Processing, and Digital Display*, 1989, vol. 1077, pp. 322–335.

[20] J. Lubin, "The use of psychophysical data and models in the analysis of display system performance," in *Digital Images and Human Vision*, A. B. Watson, Ed. Cambridge, MA: MIT Press, 1993, pp. 163–178.

[21] R. A. Westen, R. Lagendijk, and J. Biemond, "Perceptual image quality based on a multiple channel HVS model," in *Proc. ICASSP*, 1995, vol. 4, pp. 2351–2354.

[22] J. M. Foley and G. M. Boynton, "A new model of human luminance pattern vision mechanisms: Analysis of the effects of pattern orientation, spatial phase and temporal frequency," in *Computational Vision Based on Neurobiology*, 1994, vol. 2054, pp. 32–42.

[23] Z. Wang and A. C. Bovik, "A universal image quality index," *IEEE Signal Processing Lett.*, vol. 9, pp. 81–84, Mar. 2002.

---

## 原文信息

**Title:** Image Quality Assessment: From Error Visibility to Structural Similarity

**Authors:** Zhou Wang, Member, IEEE, Alan Conrad Bovik, Fellow, IEEE, Hamid Rahim Sheikh, Student Member, IEEE, and Eero P. Simoncelli, Member, IEEE

**Publication:** IEEE Transactions on Image Processing, Vol. 13, No. 4, April 2004, pp. 600-612

**DOI:** 10.1109/TIP.2003.819861

---

*翻译日期：2026年2月*
