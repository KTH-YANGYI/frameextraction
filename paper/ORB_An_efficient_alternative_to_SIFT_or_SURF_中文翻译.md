# ORB：SIFT或SURF的高效替代方案

**Ethan Rublee, Vincent Rabaud, Kurt Konolige, Gary Bradski**

Willow Garage, Menlo Park, California

{erublee}{vrabaud}{konolige}{bradski}@willowgarage.com

---

## 摘要

特征匹配是许多计算机视觉问题的基础，例如物体识别或运动恢复结构。当前的方法依赖于代价高昂的描述子进行检测和匹配。在本文中，我们提出了一种基于BRIEF的非常快速的二进制描述子，称为ORB，它具有旋转不变性和抗噪性。我们通过实验证明，ORB比SIFT快两个数量级，同时在许多情况下表现同样出色。该效率在多个实际应用中得到了测试，包括智能手机上的物体检测和图像块跟踪。

![图1 占位符 - ORB在真实世界图像上使用视点变化的典型匹配结果。绿线是有效匹配；红色圆圈表示未匹配的点。]

**关键词**：FAST和旋转BRIEF。这两种技术因其良好的性能和低成本而具有吸引力。在本文中，我们解决了这些技术相对于SIFT的几个局限性，最显著的是BRIEF缺乏旋转不变性。我们的主要贡献包括：

- 为FAST添加了一个快速且准确的方向分量。
- 有向BRIEF特征的高效计算。
- 有向BRIEF特征的方差和相关性分析。
- 一种用于在旋转不变性下去相关BRIEF特征的学习方法，从而在最近邻应用中获得更好的性能。

为了验证ORB，我们进行了实验，测试ORB相对于SIFT和SURF的特性，包括原始匹配能力和图像匹配应用中的性能。我们还通过在智能手机上实现图像块跟踪应用程序来说明ORB的效率。ORB的另一个额外好处是它不受SIFT和SURF的许可限制。

---

## 1. 引言

SIFT关键点检测器和描述子[17]，虽然已有十多年的历史，但在许多使用视觉特征的应用中已被证明非常成功，包括物体识别[17]、图像拼接[28]、视觉地图构建[25]等。然而，它带来了巨大的计算负担，特别是对于实时系统（如视觉里程计）或低功耗设备（如手机）。这促使人们积极寻找计算成本更低的替代方案；可以说其中最好的是SURF[2]。也有研究旨在加速SIFT的计算，最显著的是使用GPU设备[26]。

在本文中，我们提出了一种计算效率高的SIFT替代方案，它具有相似的匹配性能，较少受图像噪声影响，并且能够用于实时性能。我们的主要动机是增强许多常见的图像匹配应用，例如使没有GPU加速的低功耗设备能够执行全景拼接和图像块跟踪，并减少标准PC上基于特征的物体检测的时间。

我们的描述子在这些任务上表现与SIFT一样好（比SURF更好），同时快了近两个数量级。我们提出的特征建立在著名的FAST关键点检测器[23]和最近开发的BRIEF描述子[6]之上；因此我们称之为ORB（Oriented FAST and Rotated BRIEF，即有向FAST和旋转BRIEF）。

---

## 2. 相关工作

### 关键点

FAST及其变体[23,24]是在实时系统中查找关键点的方法选择，这些系统匹配视觉特征，例如并行跟踪和地图构建[13]。它效率高，能找到合理的角点，尽管必须通过金字塔方案增强以支持尺度[14]，在我们的情况下，还需要Harris角点滤波器[11]来拒绝边缘并提供合理的分数。

许多关键点检测器包括方向算子（SIFT和SURF是两个突出的例子），但FAST不包括。有多种方法可以描述关键点的方向；其中许多涉及梯度直方图计算，例如SIFT[17]和SURF[2]中的块模式近似。这些方法要么计算量大，要么在SURF的情况下产生较差的近似。Rosin的参考论文[22]给出了测量角点方向的各种方法的分析，我们借鉴了他的质心技术。与SIFT中的方向算子（可能在单个关键点上有多个值）不同，质心算子给出一个单一的主要结果。

### 描述子

BRIEF[6]是一种最近的特征描述子，它使用平滑图像块中像素之间的简单二进制测试。它在许多方面的性能与SIFT相似，包括对光照、模糊和透视失真的鲁棒性。然而，它对平面内旋转非常敏感。

BRIEF源于使用二进制测试来训练一组分类树的研究[4]。一旦在约500个典型关键点上训练完成，这些树可以用于为任意关键点返回签名[5]。以类似的方式，我们寻找对方向最不敏感的测试。找到不相关测试的经典方法是主成分分析；例如，已经证明SIFT的PCA可以帮助去除大量冗余信息[12]。然而，可能的二进制测试空间太大，无法执行PCA，因此使用穷举搜索。

视觉词汇方法[21,27]使用离线聚类来找到不相关的样本，可用于匹配。这些技术也可能有助于找到不相关的二进制测试。

与ORB最接近的系统是[3]，它提出了一种多尺度Harris关键点和有向图像块描述子。该描述子用于图像拼接，并显示出良好的旋转和尺度不变性。然而，它的计算效率不如我们的方法。

---

## 3. oFAST：FAST关键点方向

FAST特征因其计算特性而被广泛使用。然而，FAST特征没有方向分量。在本节中，我们添加了一个高效计算的方向。

### 3.1 FAST检测器

我们首先在图像中检测FAST点。FAST接受一个参数，即中心像素与围绕中心的圆环中像素之间的强度阈值。我们使用FAST-9（圆半径为9），它具有良好的性能。

FAST不产生角点性度量，我们发现它在边缘上有很大的响应。我们采用Harris角点度量[11]来对FAST关键点进行排序。对于目标数量N的关键点，我们首先将阈值设置得足够低以获得超过N个关键点，然后根据Harris度量对它们进行排序，并选择前N个点。

FAST不产生多尺度特征。我们采用图像的尺度金字塔，并在金字塔的每一层产生FAST特征（通过Harris过滤）。

### 3.2 通过强度质心确定方向

我们使用一种简单但有效的角点方向度量，即强度质心[22]。强度质心假设角点的强度偏离其中心，这个向量可以用来推断方向。

Rosin将图像块的矩定义为：

$$m_{pq} = \sum_{x,y} x^p y^q I(x,y) \quad (1)$$

利用这些矩，我们可以找到质心：

$$C = \left( \frac{m_{10}}{m_{00}}, \frac{m_{01}}{m_{00}} \right) \quad (2)$$

我们可以构造一个从角点中心O到质心C的向量OC。图像块的方向则简单地是：

$$\theta = \text{atan2}(m_{01}, m_{10}) \quad (3)$$

其中atan2是反正切的象限感知版本。Rosin提到要考虑角点是暗还是亮；然而，对于我们的目的，我们可以忽略这一点，因为无论角点类型如何，角度测量都是一致的。

为了改进这个度量的旋转不变性，我们确保矩是在半径r的圆形区域内计算的，x和y保持在该区域内。我们根据经验选择r为图像块大小，使x和y的范围为[-r, r]。当$I_{01}$接近0时，该度量变得不稳定；对于FAST角点，我们发现这种情况很少发生。

我们将质心方法与两种基于梯度的度量BIN和MAX进行了比较。在这两种情况下，X和Y梯度在平滑图像上计算。MAX选择关键点图像块中最大的梯度；BIN以10度间隔形成梯度方向直方图，并选择最大的bin。BIN类似于SIFT算法，尽管它只选择一个方向。在模拟数据集（平面内旋转加上添加的噪声）中，方向的方差如图2所示。两种基于梯度的度量表现都不太好，而质心即使在较大的图像噪声下也能给出一致良好的方向。

![图2 占位符 - 旋转度量。强度质心（IC）在恢复人工旋转噪声图像块的方向方面表现最佳，与直方图（BIN）和MAX方法相比。]

---

## 4. rBRIEF：旋转感知的BRIEF

在本节中，我们首先介绍一个导向BRIEF描述子，展示如何高效计算它，并演示为什么它在旋转下实际上表现不佳。然后我们介绍一个学习步骤来找到相关性较低的二元测试，从而产生更好的描述子rBRIEF，我们将其与SIFT和SURF进行比较。

### 4.1 BRIEF算子的高效旋转

**BRIEF概述**

BRIEF描述子[6]是由一组二元强度测试构建的图像块的位串描述。考虑一个平滑的图像块p。二元测试τ定义为：

$$\tau(p; x, y) := \begin{cases} 1 & \text{如果 } p(x) < p(y) \\ 0 & \text{其他} \end{cases} \quad (4)$$

其中p(x)是p在点x处的强度。特征定义为n个二元测试的向量：

$$f_n(p) := \sum_{i=1}^{n} 2^{i-1} \tau(p; x_i, y_i) \quad (5)$$

在[6]中考虑了许多不同类型的测试分布；这里我们使用表现最好的之一，即围绕图像块中心的高斯分布。我们还选择向量长度n = 256。

在执行测试之前平滑图像很重要。在我们的实现中，平滑使用积分图像实现，其中每个测试点是31×31像素图像块的5×5子窗口。这些是根据我们自己的实验和[6]中的结果选择的。

**导向BRIEF**

我们希望使BRIEF对平面内旋转具有不变性。BRIEF的匹配性能在超过几度的平面内旋转时会急剧下降（见图7）。Calonder[6]建议为每个图像块的一组旋转和透视扭曲计算BRIEF描述子，但这个解决方案显然代价高昂。一种更有效的方法是根据关键点的方向来导向BRIEF。对于位置$(x_i, y_i)$的n个二元测试的任何特征集，定义2×n矩阵：

$$S = \begin{pmatrix} x_1 & \cdots & x_n \\ y_1 & \cdots & y_n \end{pmatrix}$$

使用图像块方向θ和相应的旋转矩阵$R_\theta$，我们构造S的"导向"版本$S_\theta$：

$$S_\theta = R_\theta S \quad (6)$$

现在导向BRIEF算子变为：

$$g_n(p, \theta) := f_n(p) | (x_i, y_i) \in S_\theta$$

我们将角度离散化为$2\pi/30$（12度）的增量，并构造预计算BRIEF模式的查找表。只要关键点方向θ在视图之间一致，正确的点集$S_\theta$将被用于计算其描述子。

### 4.2 方差和相关性

BRIEF的一个令人满意的特性是每个位特征都有较大的方差和接近0.5的均值。图3显示了10万个样本关键点上典型的256位高斯BRIEF模式的均值分布。0.5的均值给出位特征的最大样本方差0.25。另一方面，一旦BRIEF沿关键点方向定向得到导向BRIEF，均值就会转移到更分布的模式（同样，见图3）。理解这一点的一种方式是有向角点关键点向二元测试呈现更均匀的外观。

高方差使特征更具辨别力，因为它对输入有差异地响应。另一个理想的特性是使测试不相关，因为这样每个测试都会对结果有贡献。为了分析BRIEF向量中测试的方差和相关性，我们查看了BRIEF和导向BRIEF对10万个关键点的响应。结果如图4所示。使用PCA对数据进行分析，我们绘制了最高的40个特征值（之后两个描述子收敛）。BRIEF和导向BRIEF都表现出较高的初始特征值，表明二元测试之间存在相关性——基本上所有信息都包含在前10或15个分量中。然而，导向BRIEF的方差显著较低，因为特征值较低，因此辨别力较差。显然BRIEF依赖于关键点的随机方向才能获得良好的性能。导向BRIEF效应的另一个视角显示在内点和异常值之间的距离分布中（图5）。注意对于导向BRIEF，异常值的均值向左推移，与内点有更多的重叠。

![图3 占位符 - 特征向量均值分布：BRIEF、导向BRIEF（第4.1节）和rBRIEF（第4.3节）。X轴是到均值0.5的距离]

![图4 占位符 - PCA分解中三个特征向量：BRIEF、导向BRIEF（第4.1节）和rBRIEF（第4.3节）的10万个关键点的特征值分布]

![图5 占位符 - 虚线显示关键点到异常值的距离，而实线表示三种特征向量：BRIEF、导向BRIEF（第4.1节）和rBRIEF（第4.3节）的内点匹配之间的距离]

### 4.3 学习良好的二元特征

为了从导向BRIEF的方差损失中恢复，并减少二元测试之间的相关性，我们开发了一种学习方法来选择二元测试的良好子集。一种可能的策略是使用PCA或其他降维方法，从大量二元测试开始，识别256个在大量训练集上具有高方差且不相关的新特征。然而，由于新特征由更多数量的二元测试组成，它们的计算效率将低于导向BRIEF。相反，我们在所有可能的二元测试中搜索，找到既具有高方差（均值接近0.5）又不相关的测试。

方法如下。我们首先建立一个约30万个关键点的训练集，从PASCAL 2006数据集[8]中的图像中提取。我们还枚举了31×31像素图像块上所有可能的二元测试。每个测试是图像块的一对5×5子窗口。如果我们记图像块的宽度为$w_p = 31$，测试子窗口的宽度为$w_t = 5$，那么我们有$N = (w_p - w_t)^2$个可能的子窗口。我们想从这些中选择成对的两个，所以我们有$\binom{N}{2}$个二元测试。我们消除重叠的测试，所以最终得到$M = 205590$个可能的测试。算法是：

1. 对所有训练图像块运行每个测试。
2. 根据测试与均值0.5的距离对它们进行排序，形成向量T。
3. 贪婪搜索：
   - (a) 将第一个测试放入结果向量R，并从T中移除它。
   - (b) 从T中取出下一个测试，并与R中的所有测试进行比较。如果其绝对相关性大于阈值，则丢弃它；否则将其添加到R。
   - (c) 重复上一步，直到R中有256个测试。如果少于256个，提高阈值并重试。

该算法是对一组均值接近0.5的不相关测试的贪婪搜索。结果称为rBRIEF。

rBRIEF在导向BRIEF的方差和相关性方面有显著改进（见图4）。PCA的特征值更高，而且它们下降得慢得多。有趣的是看到算法产生的高方差二元测试（图6）。在未学习的测试（左图）中有一个非常明显的垂直趋势，它们高度相关；学习后的测试显示出更好的多样性和更低的相关性。

![图6 占位符 - 考虑方向下的高方差生成的二元测试子集（左）和运行学习算法减少相关性（右）。注意测试围绕关键点方向轴的分布，方向朝上。颜色编码显示每个测试的最大成对相关性，黑色和紫色最低。学习后的测试明显具有更好的分布和更低的相关性。]

### 4.4 评估

我们使用两个数据集评估oFAST和rBRIEF的组合，我们称之为ORB：具有合成平面内旋转和添加高斯噪声的图像，以及从不同视点捕获的纹理平面图像的真实世界数据集。对于每个参考图像，我们计算oFAST关键点和rBRIEF特征，目标是每张图像500个关键点。对于每个测试图像（合成旋转或真实世界视点变化），我们做同样的事情，然后执行暴力匹配以找到最佳对应。

结果以正确匹配的百分比与旋转角度的关系给出。图7显示了添加10高斯噪声的合成测试集的结果。注意标准BRIEF算子在约10度后急剧下降。SIFT优于SURF，后者由于Haar小波组成在45度角处显示量化效应。ORB具有最佳性能，超过70%的内点。

![图7 占位符 - SIFT、SURF、带FAST的BRIEF和ORB（oFAST + rBRIEF）在添加10高斯噪声的合成旋转下的匹配性能]

ORB相对不受高斯图像噪声的影响，不同于SIFT。如果我们绘制内点性能与噪声的关系，SIFT表现出每增加5个噪声增量就稳定下降10%。ORB也下降，但速度要低得多（图8）。

![图8 占位符 - SIFT和rBRIEF在噪声下的匹配行为。噪声级别为0、5、10、15、20和25。SIFT性能快速下降，而rBRIEF相对不受影响]

为了在真实世界图像上测试ORB，我们拍摄了两组图像，一组是我们自己室内的桌子上高度纹理化的杂志（图9），另一组是室外场景。数据集具有尺度、视点和光照变化。在这组图像上运行简单的内点/异常值测试，我们测量ORB相对于SIFT和SURF的性能。测试按以下方式进行：

1. 选择一个参考视图$V_0$。
2. 对于所有$V_i$，找到一个将$V_i$映射到$V_0$的单应性扭曲$H_{i0}$。
3. 现在，使用$H_{i0}$作为SIFT、SURF和ORB描述子匹配的基本真值。

| 数据集 | 方法 | 内点% | 点数N |
|--------|------|-------|-------|
| Magazines | ORB | 36.1 | 548.5 |
| Magazines | SURF | 38.3 | 513.6 |
| Magazines | SIFT | 34.0 | 584.2 |
| Boat | ORB | 45.8 | 789 |
| Boat | SURF | 28.6 | 795 |
| Boat | SIFT | 30.2 | 714 |

ORB在室外数据集上优于SIFT和SURF。在室内数据集上大致相同；[6]指出像SIFT这样的斑点检测关键点在涂鸦类图像上往往更好。

![图9 占位符 - 满是杂志的桌子和室外场景的真实世界数据。第一列的图像与第二列的匹配。最后一列是第一列到第二列的扭曲结果。]

---

## 5. 二元特征的可扩展匹配

在本节中，我们展示ORB在大型图像数据库的最近邻匹配中平均优于SIFT/SURF。ORB的一个关键部分是方差的恢复，这使得NN搜索更高效。

### 5.1 rBRIEF的局部敏感哈希

由于rBRIEF是二进制模式，我们选择局部敏感哈希[10]作为我们的最近邻搜索。在LSH中，点存储在几个哈希表中，并在不同的桶中哈希。给定一个查询描述子，检索其匹配的桶，并使用暴力匹配比较其元素。该技术的威力在于，只要有足够的哈希表，它就能以高概率检索最近邻。

对于二元特征，哈希函数只是签名的位子集：哈希表中的桶包含具有共同子签名的描述子。距离是汉明距离。

我们使用多探测LSH[18]，它通过查看查询描述子落入的相邻桶来改进传统LSH。虽然这可能导致更多需要检查的匹配，但它实际上允许更少的表（因此更少的RAM使用）和更长的子签名，从而产生更小的桶。

### 5.2 相关性和均衡化

rBRIEF通过使哈希表的桶更均匀来提高LSH的速度：由于位相关性较低，哈希函数在划分数据方面做得更好。如图10所示，与导向BRIEF或普通BRIEF相比，桶要小得多。

![图10 占位符 - 使用两个不同的数据集（来自PASCAL 2009数据集[9]的7818张图像和来自Caltech 101[29]的9144张低分辨率图像）在BRIEF、导向BRIEF和rBRIEF描述子上训练LSH。训练只需不到2分钟，受磁盘I/O限制。rBRIEF产生了最均匀的桶，从而提高了查询速度和准确性。]

### 5.3 评估

我们将rBRIEF LSH与使用FLANN[20]的SIFT特征kd树进行比较。我们在Pascal 2009数据集上训练不同的描述子，并使用与[1]相同的仿射变换在这些图像的采样扭曲版本上测试它们。

我们的多探测LSH使用位集来加速哈希映射中键的存在检查。它还使用SSE 4.2优化的popcount计算两个描述子之间的汉明距离。

图11建立了SIFT的kd树（SURF等效）与rBRIEF的LSH之间速度和准确性的相关性。测试图像的成功匹配发生在正确数据库图像中找到超过50个描述子时。我们注意到LSH比kd树更快，这很可能是由于其简单性和距离计算的速度。LSH在准确性方面也提供了更大的灵活性，这在词袋方法[21, 27]中可能很有趣。我们还可以注意到，导向BRIEF由于其不均匀的桶而慢得多。

![图11 占位符 - 速度与准确性。描述子在它们训练的图像的扭曲版本上进行测试。我们对SIFT使用了1、2和3个kd树（自动调优的FLANN kd树性能更差），对rBRIEF使用4到20个哈希表，对导向BRIEF使用16到40个表（两者都使用16位的子签名）。SIFT搜索了160万个条目，rBRIEF搜索了180万个条目的最近邻。]

---

## 6. 应用

### 6.1 基准测试

ORB的一个重点是标准CPU上检测和描述的效率。我们的规范ORB检测器使用oFAST检测器和rBRIEF描述子，每个在图像的五个尺度上单独计算，缩放因子为$\sqrt{2}$。我们使用基于区域的插值进行高效抽取。

ORB系统分解为以下每个典型640×480帧的时间。代码在Intel i7 2.8 GHz处理器上以单线程运行：

| ORB | rBRIEF |
|-----|--------|
| 时间(ms) | 2.12 |

当在2686张图像上以5个尺度计算ORB时，它能够在42秒内检测和计算超过$2 \times 10^6$个特征。与在相同数据上比较SIFT和SURF，对于相同数量的特征（约1000）和相同数量的尺度，我们得到以下时间：

| 检测器 | ORB | SURF | SIFT |
|--------|-----|------|------|
| 每帧时间(ms) | 15.3 | 217.3 | 5228.7 |

这些时间是在Pascal数据集[9]的24张640×480图像上平均的。ORB比SURF快一个数量级，比SIFT快超过两个数量级。

### 6.2 纹理物体检测

我们将rBRIEF应用于物体识别，实现类似于[19]的传统物体识别流水线：我们首先检测oFAST特征和rBRIEF描述子，将它们与我们的数据库匹配，然后执行PROSAC[7]和EPnP[16]进行姿态估计。

我们的数据库包含49个家居物品，每个在24个视图下用2D相机和微软的Kinect设备拍摄。测试数据由这些相同物体在不同视点和遮挡下的2D图像子集组成。要获得匹配，我们要求描述子匹配，并且可以计算姿态。最终，我们的流水线检索到61%的物体，如图12所示。

![图12 占位符 - 我们带有姿态估计的纹理物体识别的两张图像。蓝色特征是叠加在查询图像上的训练特征，表示物体的姿态被正确找到。还显示了每个物体的坐标轴和粉色标签。上图漏掉了两个物体；下图找到了所有物体。]

该算法处理200MB中的120万个描述子数据库，并且时间与我们之前展示的相当（平均14ms检测和17ms LSH匹配）。通过不将所有查询描述子匹配到训练数据，流水线可以大大加快，但我们的目标只是展示使用ORB进行物体检测的可行性。

### 6.3 嵌入式实时特征跟踪

虽然在手机上运行的实时特征跟踪器[15]可以处理非常小的图像（例如120×160）和非常少的特征。与我们系统相当的系统[30]通常每张图像需要超过1秒。我们能够在配备1GHz ARM芯片和512MB RAM的手机上以7 Hz运行640×480分辨率的ORB。使用OpenCV的Android端口进行实现。这些是每张图像约400个点的基准：

| HFit |
|------|
| 时间(ms) | 20.9 |

在手机上跟踪涉及将实时帧与先前捕获的关键帧匹配。描述子与关键帧一起存储，关键帧被假设包含纹理良好的平面表面。我们在每个传入帧上运行ORB，然后进行与关键帧的暴力描述子匹配。来自描述子距离的假定匹配用于PROSAC最佳拟合单应性H。

---

## 7. 结论

在本文中，我们定义了一种新的有向描述子ORB，并展示了其相对于其他流行特征的性能和效率。对方向下方差的调查对于构建ORB和去相关其组件以在最近邻应用中获得良好性能至关重要。我们还通过OpenCV 2.3向社区贡献了ORB的BSD许可实现。

我们在这里没有充分解决的一个问题是尺度不变性。虽然我们使用金字塔方案进行尺度处理，但我们没有探索来自深度线索的每关键点尺度、调整八度数量等。未来的工作还包括GPU/SSE优化，这可以使LSH再提高一个数量级。

---

## 参考文献

[1] M. Aly, P. Welinder, M. Munich, and P. Perona. Scaling object recognition: Benchmark of current state of the art techniques. In First IEEE Workshop on Emergent Issues in Large Amounts of Visual Data (WS-LAVD), IEEE International Conference on Computer Vision (ICCV), September 2009. 6

[2] H. Bay, T. Tuytelaars, and L. Van Gool. Surf: Speeded up robust features. In European Conference on Computer Vision, May 2006. 1, 2

[3] M. Brown, S. Winder, and R. Szeliski. Multi-image matching using multi-scale oriented patches. In Computer Vision and Pattern Recognition, pages 510–517, 2005. 2

[4] M. Calonder, V. Lepetit, and P. Fua. Keypoint signatures for fast learning and recognition. In European Conference on Computer Vision, 2008. 2

[5] M. Calonder, V. Lepetit, K. Konolige, P. Mihelich, and P. Fua. High-speed keypoint description and matching using dense signatures. In Under review, 2009. 2

[6] M. Calonder, V. Lepetit, C. Strecha, and P. Fua. Brief: Binary robust independent elementary features. In In European Conference on Computer Vision, 2010. 1, 2, 3, 5

[7] O. Chum and J. Matas. Matching with PROSAC - progressive sample consensus. In C. Schmid, S. Soatto, and C. Tomasi, editors, Proc. of Conference on Computer Vision and Pattern Recognition (CVPR), volume 1, pages 220–226, Los Alamitos, USA, June 2005. IEEE Computer Society. 7

[8] M. Everingham. The PASCAL Visual Object Classes Challenge 2006 (VOC2006) Results. http://pascallin.ecs.soton.ac.uk/challenges/VOC/databases.html. 4

[9] M. Everingham, L. Van Gool, C. K. I. Williams, J. Winn, and A. Zisserman. The PASCAL Visual Object Classes Challenge 2009 (VOC2009) Results. http://www.pascal-network.org/challenges/VOC/voc2009/workshop/index.html. 6, 7

[10] A. Gionis, P. Indyk, and R. Motwani. Similarity search in high dimensions via hashing. In M. P. Atkinson, M. E. Orlowska, P. Valduriez, S. B. Zdonik, and M. L. Brodie, editors, VLDB'99, Proceedings of 25th International Conference on Very Large Data Bases, September 7–10, 1999, Edinburgh, Scotland, UK, pages 518–529. Morgan Kaufmann, 1999. 6

[11] C. Harris and M. Stephens. A combined corner and edge detector. In Alvey Vision Conference, pages 147–151, 1988. 2

[12] Y. Ke and R. Sukthankar. Pca-sift: A more distinctive representation for local image descriptors. In Computer Vision and Pattern Recognition, pages 506–513, 2004. 2

[13] G. Klein and D. Murray. Parallel tracking and mapping for small AR workspaces. In Proc. Sixth IEEE and ACM International Symposium on Mixed and Augmented Reality (ISMAR'07), Nara, Japan, November 2007. 1

[14] G. Klein and D. Murray. Improving the agility of keyframe-based SLAM. In European Conference on Computer Vision, 2008. 2

[15] G. Klein and D. Murray. Parallel tracking and mapping on a camera phone. In Proc. Eigth IEEE and ACM International Symposium on Mixed and Augmented Reality (ISMAR'09), Orlando, October 2009. 7

[16] V. Lepetit, F. Moreno-Noguer, and P. Fua. EPnP: An accurate O(n) solution to the pnp problem. Int. J. Comput. Vision, 81:155–166, February 2009. 7

[17] D. G. Lowe. Distinctive image features from scale-invariant keypoints. International Journal of Computer Vision, 60(2):91–110, 2004. 1, 2

[18] Q. Lv, W. Josephson, Z. Wang, M. Charikar, and K. Li. Multi-probe LSH: efficient indexing for high-dimensional similarity search. In Proceedings of the 33rd international conference on Very large data bases, VLDB '07, pages 950–961. VLDB Endowment, 2007. 6

[19] M. Martinez, A. Collet, and S. S. Srinivasa. MOPED: A Scalable and low Latency Object Recognition and Pose Estimation System. In IEEE International Conference on Robotics and Automation. IEEE, 2010. 7

[20] M. Muja and D. G. Lowe. Fast approximate nearest neighbors with automatic algorithm configuration. VISAPP, 2009. 6

[21] D. Nister and H. Stewenius. Scalable recognition with a vocabulary tree. In CVPR, 2006. 2, 6

[22] P. L. Rosin. Measuring corner properties. Computer Vision and Image Understanding, 73(2):291–307, 1999. 2

[23] E. Rosten and T. Drummond. Machine learning for high-speed corner detection. In European Conference on Computer Vision, volume 1, 2006. 1

[24] E. Rosten, R. Porter, and T. Drummond. Faster and better: A machine learning approach to corner detection. IEEE Trans. Pattern Analysis and Machine Intelligence, 32:105–119, 2010. 1

[25] S. Se, D. Lowe, and J. Little. Mobile robot localization and mapping with uncertainty using scale-invariant visual landmarks. International Journal of Robotic Research, 21:735–758, August 2002. 1

[26] S. N. Sinha, J. michael Frahm, M. Pollefeys, and Y. Genc. Gpu-based video feature tracking and matching. Technical report, In Workshop on Edge Computing Using New Commodity Architectures, 2006. 1

[27] J. Sivic and A. Zisserman. Video google: A text retrieval approach to object matching in videos. International Conference on Computer Vision, page 1470, 2003. 2, 6

[28] N. Snavely, S. M. Seitz, and R. Szeliski. Skeletal sets for efficient structure from motion. In Proc. Computer Vision and Pattern Recognition, 2008. 1

[29] G. Wang, Y. Zhang, and L. Fei-Fei. Using dependent regions for object categorization in a generative framework, 2006. 6

[30] A. Weimert, X. Tan, and X. Yang. Natural feature detection on mobile phones with 3D FAST. Int. J. of Virtual Reality, 9:29–34, 2010. 7

---

*2011 IEEE International Conference on Computer Vision*

*978-1-4577-1102-2/11/$26.00 ©2011 IEEE*

*Authorized licensed use limited to: KTH Royal Institute of Technology. Downloaded on February 23, 2026 at 16:10:48 UTC from IEEE Xplore. Restrictions apply.*
