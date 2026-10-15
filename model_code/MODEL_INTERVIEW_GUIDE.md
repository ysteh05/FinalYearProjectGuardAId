# YOLOv9-T 与 MC3-18 面试复习说明

## 1. 一句话介绍整个方案

GuardAId 使用两个互补的视觉模型：YOLOv9-T 负责在单帧中检测并定位危险物品，MC3-18 负责从连续视频帧中判断是否存在可观察的打斗或身体接触。前者是空间目标检测，后者是时空视频分类。

```text
单帧 CCTV ──> YOLOv9-T ──> 类别 + bounding box + confidence
视频窗口 ──> 20 帧预处理 ──> MC3-18 ──> Fight probability
```

## 2. YOLOv9-T：训练流程

### 任务和标签

这是 multi-class object detection，不是普通 image classification。每张图片可以有零个或多个目标，每个标签是：

```text
class_id  x_center  y_center  width  height
```

坐标使用 YOLO 的归一化格式，范围是 0 到 1。项目的五个类别是：

| ID | 类别 |
|---:|---|
| 0 | `parang_machete` |
| 1 | `knife` |
| 2 | `baseball_bat` |
| 3 | `long_gun` |
| 4 | `gun` |

空的 `.txt` 标签文件表示这是一张没有目标的负样本，不能人为建立一个 background box。

### 主要训练设置

| 项目 | 设置 |
|---|---|
| 输入 | 640 × 640 |
| 最大 epochs | 100 |
| batch size | 16 |
| optimizer | SGD |
| initial learning rate | 0.01 |
| momentum | 0.937 |
| weight decay | 0.0005 |
| scheduler | cosine decay |
| warm-up | 3 epochs |
| early stopping patience | 10 |
| seed | 2026 |
| AMP | 开启 |
| augmentation | horizontal flip、translate、scale、mosaic |

训练从 COCO pretrained weights 开始，而不是完全随机初始化。YOLOv9 使用官方 `train_dual.py`，脚本会先读取 YOLOv9 原生 hyperparameter YAML，再覆盖项目中需要统一的参数，避免丢失 `obj`、`anchor_t` 等版本相关字段。

### 为什么选择 YOLOv9-T

项目先比较了 YOLOv5n、YOLOv8n、YOLOv9-T、YOLOv10n 和 YOLO11n。选择不是只看 FPS，而是优先比较验证集的 mAP@0.50:0.95，再看 mAP@0.50、速度和其他指标。当前记录中 YOLOv9-T 的验证结果为：

- Precision：0.904
- Recall：0.860
- F1：约 0.881
- mAP@0.50：0.915
- mAP@0.50:0.95：0.690
- batch-size 1 推理速度：约 52.91 FPS

它不是最快的模型，但定位质量最好，更符合“不能漏掉武器，同时要知道武器在哪里”的需求。

### YOLO 推理流程

1. 读取 BGR frame。
2. 使用 letterbox 调整到 640 × 640，尽量保持原始宽高比。
3. BGR 转 RGB、HWC 转 CHW、像素除以 255。
4. 前向传播取得候选框。
5. 使用 NMS 去除同一目标的重复框。
6. 把框坐标从 640 输入空间映射回原图空间。
7. 使用类别专属 confidence threshold，保留最终检测结果。

这里的 `candidate_floor=0.001` 只是让低置信度候选进入后续筛选；真正的 operational threshold 在每个类别上单独应用。NMS 的 IoU threshold 和 confidence threshold 是两件不同的事：前者控制重复框抑制，后者控制结果是否足够可信。

### 为什么使用每类别阈值

不同类别的外观、样本数量、遮挡程度和误报风险不同，因此一个全局阈值并不理想。项目在 validation set 上为五个类别分别搜索：

- Sensitive：最大化 F2，偏重 recall，减少漏报；
- Moderate：最大化 F1，平衡 precision 和 recall；
- Certain：最大化 F0.5，偏重 precision，减少误报。

例如当前 moderate profile 是：`parang_machete=0.42`、`knife=0.42`、`baseball_bat=0.50`、`long_gun=0.54`、`gun=0.42`。阈值只用 validation 搜索，锁定后才在 test 上评估，避免 test leakage。

## 3. MC3-18：训练流程

### 任务和数据

MC3-18 是 binary video classification。RWF-2000 的目录类别是：

```text
NonFight -> label 0
Fight    -> label 1
```

与 YOLO 不同，MC3-18 不输出 bounding box；它输出两个类别的 logits，再通过 softmax 取 index 1 的 `Fight probability`。

### 视频预处理

每一个 video clip 的处理顺序是：

1. 均匀采样 20 帧，覆盖整个视频；
2. BGR 转 RGB；
3. 将短边 resize 到 128；
4. 得到 112 × 112 crop；训练集使用随机 crop 和 0.5 概率水平翻转；
5. 像素除以 255；
6. 使用 `mean=(0.43216, 0.394666, 0.37645)` 和 `std=(0.22803, 0.22145, 0.216989)` 标准化；
7. 将数组从 `T,H,W,C` 转成 PyTorch video model 需要的 `C,T,H,W`；
8. DataLoader 加 batch 后输入形状是 `B,C,T,H,W`。

训练时把预处理后的 clip cache 成 `.pt` 文件，减少每个 epoch 重复解码 AVI 的成本。validation 和 test 使用 centre crop，保证评估确定性。

### 模型和优化

```python
model = torchvision.models.video.mc3_18(weights=MC3_18_Weights.DEFAULT)
model.fc = torch.nn.Linear(model.fc.in_features, 2)
```

MC3-18 的含义可以这样解释：它是 3D convolution video architecture 的一种混合设计，部分层使用 2D spatial convolution，部分层使用 3D convolution 建模时间变化，因此在空间特征、动作信息和模型规模之间取得折衷。

训练参数是 AdamW、learning rate `1e-4`、weight decay `1e-4`、最多 30 epochs、patience 5、seed 42。每个 epoch 后在 validation set 计算 ROC-AUC；连续 5 个 epoch 没有至少 `0.002` 的改善就 early stop，并恢复最佳 validation checkpoint。

当前 MC3-18 validation 记录为：

- ROC-AUC：0.9841
- F1：0.9206
- Precision：0.9667
- Recall：0.8788
- 参数量：约 11.49M
- 速度：约 30.52 clips/s

它与 R3D-18、R(2+1)D-18 比较后被选中，因为验证 ROC-AUC 和 F1 较高，同时参数量较小。

### MC3-18 推理流程

1. 从视频得到一个 5 秒窗口。
2. 每隔 2.5 秒产生下一个窗口，因此相邻窗口会重叠。
3. 每个窗口均匀采样 20 帧。
4. 使用与 validation 完全相同的 resize、centre crop 和 normalization。
5. 将模型 logits 经过 `softmax`，读取 Fight 的概率。
6. 与 operational threshold 比较，再交给后续告警策略。

当前三种模式的 threshold 是：

| 模式 | threshold | 设计重点 |
|---|---:|---|
| sensitive | 0.009688 | 尽量减少漏报 |
| moderate | 0.056 | 平衡误报和漏报 |
| certain | 0.710192 | 只保留高置信度事件 |

这些 threshold 不是模型训练出来的权重，而是使用 validation prediction 做 calibration 得到的 operating points。

## 4. 两个模型如何配合

YOLO 和 MC3-18 不应该合成一个输入：YOLO 需要单帧目标的空间位置，MC3-18 需要多帧动作变化。系统将它们作为两个并行 detector：

- YOLO 以约 4 FPS 处理画面，发现武器后再经过 per-class threshold 和 N-of-M policy；
- MC3-18 以滑动窗口处理视频，输出 violence probability；
- borderline violence candidate 可以进入 VLM 辅助复核；
- 最终系统告警仍需人工判断，模型不能单凭图像证明攻击意图或事件真实性。

## 5. 高频面试问题与回答

### Q1：为什么武器用 YOLO，而暴力用 MC3-18？

因为两种任务的输入信息不同。武器的核心是“画面里有什么以及在哪里”，单帧空间信息已经很重要；暴力的核心是动作和时间变化，例如挥打、推搡和倒地，仅看单帧容易混淆，所以需要 video model 建模 temporal information。

### Q2：mAP@0.50 与 mAP@0.50:0.95 有什么区别？

mAP@0.50 只在 IoU=0.50 的匹配标准下计算 AP；mAP@0.50:0.95 会在 0.50、0.55 到 0.95 多个 IoU 上平均，更严格地反映定位框是否准确。这个项目优先 mAP@0.50:0.95，因为系统不仅要判断“有武器”，还要把位置框准。

### Q3：为什么不能只用 accuracy？

武器检测中背景图片很多，accuracy 很容易被大量 true negative 影响，也不能很好表示漏检和误报。视频二分类则同时报告 ROC-AUC、precision、recall、F1 和 balanced accuracy，让模型质量和 operational trade-off 更清楚。

### Q4：为什么用 F0.5、F1、F2 三种阈值？

F-beta 通过 beta 表示偏好：beta<1 更重视 precision，beta=1 平衡两者，beta>1 更重视 recall。安全场景通常需要同时支持低漏报和低误报的不同工作模式，因此不是强迫所有用户使用一个阈值。

### Q5：early stopping 监控什么？

YOLO 使用原生 fitness：`0.1 × mAP@0.50 + 0.9 × mAP@0.50:0.95`；MC3-18 使用 validation ROC-AUC。两者都只看 validation，不看 test。这样 test set 可以在模型和阈值锁定后进行一次较客观的最终评估。

### Q6：为什么要做 centre crop 和 normalization？

模型训练时学到的是特定输入分布。如果训练和推理的颜色空间、尺寸、crop 位置或 normalization 不一致，就会产生 distribution shift。项目把 MC3-18 的训练、validation、部署 preprocessing 尽量锁定一致，减少这种问题。

### Q7：FP16 有什么作用？

在 CUDA 上将模型和输入转换为 half precision 通常可以降低显存和提升吞吐；但需要在支持 half 的 GPU 上使用，并保留 logits/probability 的适当数值处理。CPU 路径仍使用 float32。

### Q8：模型能判断“是不是在攻击”吗？

不能直接这样说。当前 MC3-18 只学习 `Fight` 与 `NonFight` 的视觉模式，实际系统更准确的表述是“observable violence / physical-contact bullying candidate”。同样，检测到 baseball bat 或 gun 外观也不等于证明它正在被用于攻击，系统仍需要人工复核。

### Q9：如何避免 data leakage？

训练只更新 train；validation 用于 early stopping、模型比较和 threshold calibration；test 在模型和阈值全部锁定后才使用。对于视频数据，还要避免同一 source video 的相邻片段跨 train/val/test，否则模型可能记住背景或视频风格而不是学习动作。

## 6. 诚实说明项目限制

- 当前结果是 prototype 级别，不能视为生产级安全认证；
- 公开武器图片和 RWF-2000 不完全等同于马来西亚学校 CCTV；
- 低光、遮挡、模糊、小目标和摄像机角度都会影响结果；
- threshold 依赖当前数据、split 和硬件环境，换数据后应重新 calibration；
- VLM 或人工复核是降低 borderline 误报的辅助机制，不是替代 detector 的训练标签；
- test 指标只能支持当前实验设置下的结论，不能直接外推到所有学校场景。
