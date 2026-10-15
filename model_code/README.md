# GuardAId 模型代码整理

这里是从项目 notebook 与运行时实现整理出的独立版本，方便复习、面试演示和单独运行。代码没有替换原本的 `guardaid_monitor` 实现。

| 文件 | 用途 |
|---|---|
| `yolov9_weapon_train.py` | 使用官方 YOLOv9 `train_dual.py` 训练最终选定的 YOLOv9-T 武器检测模型 |
| `yolov9_weapon_infer.py` | 对图片、图片目录或视频进行武器检测，并输出 bounding box |
| `mc3_18_train.py` | 按 RWF-2000 协议训练 MC3-18 二分类视频模型 |
| `mc3_18_infer.py` | 对视频做 5 秒滑动窗口推理，输出 Fight probability |
| `mc3_18_mil_pipeline.py` | 新的多数据集 MC3-18 MIL 训练、验证阈值校准和最终测试入口 |
| `mc3_18_reverse_concat_pipeline.py` | 独立的两秒/八帧倒放拼接方案：复用原有五个数据集、UCF 以及外部留出集 |
| `mc3_18_dashboard.py` / `mc3_18_dashboard.html` | 本机网页工作台：图形化训练、验证监控、checkpoint 恢复和单视频推理 |
| `yolov9_weapon_pipeline.py` | YOLOv9-T 数据审计、标签合并、COCO 权重迁移、训练、阈值校准与测试 |
| `yolov9_weapon_dashboard.py` / `yolov9_weapon_dashboard.html` | YOLOv9-T 本机训练工作台：样本浏览、实时进度、安全暂停/恢复、图片推理和运行历史 |
| `yolov9_mall_finetune.html` / `yolov9_mall_inference.html` | Mall 负样本后训练页面及基线／已完成两类模型单图推理页面 |
| `yolov9_selected_sources_finetune.py` / `yolov9_selected_sources_50epoch.html` | 固定九来源、保留来源划分的 Knife/Gun 独立训练页面；当前新一轮为 10 epochs、F0.5/fitness 组合早停 |
| `yolov9_weapon_workspace.py` / `yolov9_weapon_hooks.py` | 训练副本质量审计、缓存及与官方训练器隔离的进度 hooks |
| `MODEL_INTERVIEW_GUIDE.md` | 面试解释、参数、指标、常见问题和项目限制 |

## YOLOv9-T 五类武器训练工作台

先用当前项目虚拟环境安装工作台及官方 YOLOv9 所需的非 PyTorch 依赖。依赖文件不会改装现有 `torch` / `torchvision` CUDA 环境：

```powershell
python -m pip install -r model_code/requirements-yolov9-dashboard.txt
```

从项目根目录启动本机网页：

```powershell
python model_code/yolov9_weapon_dashboard.py
```

浏览器默认打开 `http://127.0.0.1:8767/`。不自动打开浏览器时可加 `--no-browser`；端口可通过 `--port` 修改。工作台默认读取 `Weapon Detection Model Dataset/` 和 `results (4)/yolov9/`，训练输出写入 `model_runs/yolov9_weapon_workspace/`。也可先运行数据审计而不启动训练：

```powershell
python model_code/yolov9_weapon_pipeline.py audit
```

数据审计建立独立、带指纹的缓存并保留原始图片和标签；训练页中的样本浏览器显示映射后的五类框与 negative 空标签。安全暂停在完整 epoch 后写入可恢复的 `checkpoints/last.pt`；若最终验证或测试中断，可以从已锁定的最佳模型继续评估。工作台不会在启动或审计时自动开始 GPU 训练。

工作台的隔离 hooks 兼容 NumPy 2.x 中移除的 `trapz`，并使用 `.item()` 提取单元素指标数组。Windows 数据加载子进程不会写入训练状态。时间预算依据完整 epoch 耗时或预热后的速度探测判断，避免首次 CUDA／数据加载启动时间造成过早结束。`checkpoints/last.pt` 保留优化器与 EMA，`checkpoints/resume_state.pt` 保存调度器、AMP 和随机状态；`best_inference.pt` 是另行导出的推理模型。最终验证和测试会检查逐图片预测记录数量后再生成阈值及报告。

## Knife/Gun 扩展负样本后训练

在运行同一个 dashboard 后打开 `http://127.0.0.1:8767/mall-negative-finetune`，或从五类工作台／完整数据集 V1 页面的导航进入。页面默认选择 BREV 导入的 `best_training.pt`；模型目录是服务端白名单，不接收任意权重路径。此页面当前配置为扩展负样本版本；此前 Mall-only 的运行与缓存仍保留并可查看。

本流程从上一版 Mall-negative 缓存继续构建一个新的不可变数据缓存：Full Dataset V1 原有切分不变，Mall 的 2,000 张影像也沿用上一版 1,600／200／200 切分。再把 AIRTLab NonViolent 的 640 张、BusViolence NonFight 的 700 张，以及移除一张已确认真刀照片后的 NoGUN 2,946 张加入为负样本。AIRTLab 与 Bus Violence 使用已准备好的 train／validation／test 切分；NoGUN 原有 train／valid／test 切分保留，`valid` 映射成 `val`。NoGUN 原标签不会沿用，而是为所有剩余照片生成空 Knife/Gun 标签。已确认的刀具文件在源数据集和训练配方中均排除。所有源数据、旧缓存和模型权重保持不变；新运行与缓存写入 `model_runs/yolov9_mall_negative_finetune/`。

当前数据配方合计 41,684 张：train 33,598、validation 4,712、test 3,374。其中四类负样本（Mall、AIRTLab、Bus NonFight、剩余 NoGUN）合计 6,286 张，分别为 train 5,496、validation 416、test 374。新增数据的精确样本及切分数会在页面和每次运行的审计报告中显示。

开始前需要明确确认一个假设：这些来源按其既定语义作为武器负样本，程序会给新增负样本写空 Knife/Gun 标签；如果其余图片中仍有刀或枪，就会产生错误的空标注。这个负样本假设与每次运行的数据来源都会写入报告。

后训练固定为 5 epochs、patience 0（禁用 early stopping），默认输入尺寸 640、batch 16、4 workers、AdamW、初始学习率 `0.0001`、weight decay `0.0005`、mosaic `0.2`、warmup `0.5`。启动后先用所选权重做独立的训练和验证 batch 探测；batch 会先按页面要求实测，显存不足时才自动减半。用户可在页面调整其他暴露的超参数，但服务端锁定类别、数据配方、输出目录、5 epochs 和无早停。该过程从所选两类 checkpoint 复制模型权重，然后创建新的优化器和学习率计划；不是恢复旧优化器状态。

训练完成后使用 YOLOv9-T 当前训练流程按每轮验证 fitness 选择 `best.pt`，再在 validation split 为 Sensitive（F2）、Moderate（F1）和 Certain（F0.5）分别校准每类阈值，最后将锁定阈值用于 test split。页面报告 Knife 与 Gun 的 precision、recall、mAP50、mAP50–95，并按三个 profile、负样本来源及 validation/test split 报告误报情况。输出、权重和结果报告与旧工作台隔离保存，可从页面下载。训练由用户点击开始；启动 dashboard、查看状态或运行自动化测试不会开始训练。

在该页面的 **Image Inference** 导航中可打开单图推理页。页面列出导入的 BREV baseline 及已完成、具有完整三档验证阈值的本地 Knife/Gun 训练运行；用户可选择模型与 Sensitive / Moderate / Certain profile，也可分别编辑 Knife、Gun 阈值。上传单张 JPG、PNG、WebP 或 BMP（最大 12 MB、32 MP）后，本机推理会显示标注图、类别、置信度、所用阈值和 bounding-box 坐标；页面不会保存上传图像。NMS IoU 固定为 0.70，以匹配阈值 profile 校准时的配置。模型对比和 Mall 测试数据的详细结果见项目根目录的 `YOLO_MALL_NEGATIVE_FINETUNING_COMPARISON.md`；Mall crowd-counting 数据没有 weapon-specific ground truth，Mall false-positive 结论依赖于全部图像均为武器负样本这一假设。

## 九来源 Knife/Gun F0.5 Benchmark 训练页面

在同一个 dashboard 打开 `http://127.0.0.1:8767/selected-sources-50epoch`，或从现有 YOLO 页面导航进入。页面固定使用 AIRTLab Non-Violent、Bus Violence Non-Fight、Dangerous Items、Knife Dataset、Long Gun Dataset、Mall Negative、NoGUN、OD SOHAS 和 ShanghaiTech Negative；V11、OD Knife Detection、OD Pistol Detection 不纳入本轮。缓存从最近审计过的来源缓存筛选，图片与标签采用硬链接复用（不支持时复制），原始来源缓存不修改。训练由页面按钮显式启动。

建议保留每个来源已有的 split，不再把图片统一随机重分成 80/10/10：AIRT/Bus/NoGUN 等有来源划分，ShanghaiTech 与 Mall 还包含视频或时间片分组。沿用现有划分可避免相邻帧跨 train/val/test 泄漏，也能与之前评估直接比较。当前 manifest 统计为 train 10,585、validation 1,487、test 2,221，共 14,293 张。Knife / Gun 数量按“含该类标注的图片”统计，同图两类会分别计数；空标注 negative 共 9,081 张。页面和 run manifest 会保存来源级样本数及拆分规则。

本轮从 BREV Best Knife/Gun checkpoint 开始，最多 10 epochs、patience 3。每轮验证记录 F0.5、YOLO fitness 与 `0.5 × F0.5 + 0.5 × fitness`；该组合分数用于早停，best checkpoint 仍按 validation fitness 选择，测试集只用于最终评估。推荐参数为 YOLOv9-T、640 输入、batch 8、2 workers、AdamW、`lr0=0.0001`、`lrf=0.1`、weight decay `0.0005`、warmup `0.5`、mosaic `0.2`、最后 3 epochs 关闭 mosaic、mixup 0、seed 2026。Batch/workers 延续此前稳定配置；不会额外对空标注样本乘 loss 权重，以免牺牲 knife/gun recall。训练结束自动做验证集阈值校准与测试集评估，并按 Sensitive / Moderate / Certain 报告逐来源 negative FPR；测试集不用于早停、阈值校准或 checkpoint 选择。新 run 复用 `model_runs/yolov9_selected_sources_50epoch/` 下已审计的数据缓存。

## 新的 MC3-18 MIL 训练流程

`mc3_18_mil_pipeline.py` 是独立入口，不会改写旧的 `mc3_18_train.py` 或运行时推理代码。它使用 TorchVision 的 Kinetics-400 预训练 MC3-18，替换为 `NonFight=0`、`Fight=1` 二分类头，并扫描数据集根目录下五个规范数据集的 `train/val/test/Fight/NonFight` 文件夹：RWF-2000、Real Life Violence、bus-violence、seymanurakti 与 ShanghaiTech。AVI 和 MP4 都会读取；RLVD 的重复存档目录会忽略。

ShanghaiTech 的 330 段原始训练视频按场景编号整体分组，作为 `NonFight` 数据加入：训练场景 `01/05/06/08/10/11/12` 共 264 段，验证场景 `02/07/09` 共 33 段，测试场景 `03/04/13` 共 33 段。每个场景只属于一个 split。项目数据目录中的视频使用硬链接指向原始 ShanghaiTech 文件，划分明细保存在 `ShanghaiTech/split_manifest.csv`；不要通过任一链接路径覆盖或修改视频内容。

每条原视频是一个 MIL 包。标准窗口为 1 秒、步长 0.5 秒，每窗把时间分成 4 个等长区间并各取区间中心帧。在线部署到达 1 秒时才产生第一次预测，之后每过 0.5 秒滑出一个完整窗口。训练包可在末尾追加一个结束于视频结尾的完整窗口；验证和测试只使用时间表上完整的标准窗口，不补末尾。视频分数为包内所有窗口 Fight 概率的最大值；正包的最高分窗口承担正类损失，负包的最高分窗口承担负类损失。低于 1 秒的视频保留在原处，但不进入训练、验证或测试，并会按数据集、切分和类别报告。

Frame Cache 是可持久复用的：默认保存在 `model_runs/_frame_cache/mc3-1s-4f-128short-112crop-v1/`，不同训练输出目录与断点恢复共用。缓存只保存每个视频被窗口采样到的缩放 RGB 帧，不会复制整段视频；缓存键会检查视频路径、大小、修改时间、视频元数据和预处理协议，变更的视频会生成新条目。缺失缓存最多启动 4 个独立进程并行解码和压缩，每个 OpenCV 进程限制为单线程；已存在的条目会跳过并复用。Dashboard 实时日志显示复用数、创建数、失败数、进程池大小、已实际参与的 worker 数及总缓存大小。四进程仅用于生成缺失缓存；缓存全部命中时不会启动 worker，缓存完成后的模型训练也不因此变为多进程。可在 dashboard 的“帧缓存目录”字段指定其他磁盘位置；留空时使用共享默认目录。加入 ShanghaiTech 后约有 5,811 条合格视频；实际缓存大小以生成时日志报告为准。

## MC3-18 两秒倒放拼接方案

此方案在 dashboard 的独立页面卡中运行，不修改原有 1 秒 MIL 方案。默认使用项目目录中的五个原有数据集（RWF-2000、Real Life Violence Dataset、bus-violence、seymanurakti 与 ShanghaiTech），原样沿用各自 `train/val/test/Fight/NonFight` 文件夹划分；UCF 按来源组、类别和子类别分层后单独划分 80/10/10。AIRTLab 与 GuardAId 仅作为训练后的外部测试集。验证阈值从原有五个数据集与 UCF 的合并验证集校准；最终报告仍按数据来源展示测试指标。

窗口为 2 秒、步长 1 秒、每窗 8 帧。所有有标签训练数据中 1–2 秒片段采用“原片后接倒放”的采样方式；不足 1 秒的训练样本排除。验证和测试均使用自然播放，少于 2 秒的样本排除。程序会按文件内容指纹审计完全重复视频：UCF 与原有数据集重复时保留原有样本；原有 split 间出现完全重复时保留一个原始 split 中的副本；与 AIRTLab/GuardAId 重复的模型样本排除以保持外部留出独立。详细纳入/排除原因写入每次运行的 `dataset_manifest.csv`。

训练每个 epoch 对每条视频生成一个由 `seed + epoch + 视频路径` 确定的新增强视图，包括随机裁切、水平翻转、轻微颜色变化和低概率模糊。同一视图内的裁切与颜色参数在各帧和窗口间共享。增强结果不作为额外视频样本计数；验证与测试不做增强。模型选择和早停只看验证集视频级 ROC-AUC。最佳模型确定后，再仅用验证集校准三档阈值：Sensitive 以 F2 优先降低漏报，Moderate 以 F1 平衡 precision/recall，Certain 以 F0.5 优先降低误报，并约束 `Sensitive ≤ Moderate ≤ Certain`；同分时 Sensitive 取较低阈值，另两档取较高阈值。测试集只在权重与阈值锁定后运行。

## MC3-18 四秒短尾窗口方案

四秒方案在同一 dashboard 的独立“4 秒窗口方案”页面运行，使用 RWF-2000、Real Life Violence Dataset、ShanghaiTech 和 UCF；沿用前三者既有 split，并沿用 UCF 按来源组划分的 80/10/10。bus-violence 与 Seymanurakti 不纳入；AIRTLab 和 GuardAId 只作外部测试。Real Life Violence Dataset 中短于 4 秒的视频会在所有 split 排除；其他来源短于 4 秒但至少 1 秒的视频，按真实时长均匀抽 16 帧，不倒放、不补黑帧。少于 1 秒的视频排除。长视频采用 4 秒窗口、2 秒步长，若规则窗口没有覆盖视频结尾，则增加一个末尾对齐窗口。

模型、checkpoint、阈值和报告独立保存在 `model_runs/mc3_4s16f/`。训练只读复用既有两秒方案帧缓存 `model_runs/mc3_2s8f_reverse_concat/_frame_cache/mc3-2s-1s-stride-8f-128short-112crop-reverse-concat-v2/`；缺失帧写入 `_supplemental_cache/`，最多 5 GiB。两秒缓存不会被改写，也不会复制整份缓存；补充缓存写满后，剩余帧会在训练/评估时按需解码。运行清单会记录实际缓存命中与排除结果。单视频推理页根据 checkpoint 中保存的协议自动使用四秒窗口。

PyCharm 中仍运行 `model_code/mc3_18_dashboard.py` 即可打开工作台；无需启动另一个服务。点击侧栏的“4 秒窗口方案”检查数据路径、两秒缓存与补充缓存后，可使用默认参数开始独立训练。当前实现和页面可以先单独检查，不会自动启动训练。

### 环境与 PyCharm

建议使用 Python 3.10。先在所用解释器中安装适合本机 CUDA/CPU 的**匹配版本** PyTorch 与 TorchVision；GPU 用户请使用 [PyTorch 官方安装选择器](https://pytorch.org/get-started/locally/)生成对应安装命令。之后在项目根目录安装视频读取依赖：

```powershell
python -m pip install -r model_code/requirements-mc3.txt
```

在 PyCharm 将项目解释器设为上述环境，然后直接打开并 Run `model_code/mc3_18_mil_pipeline.py`，无需设置参数。默认数据目录是项目根目录的 `GuardAId Video Classification Model Dataset/`；运行结果默认写入 `model_runs/mc3_1s4f/`，共享帧缓存写入 `model_runs/_frame_cache/`。第一次从头训练需要从 TorchVision 下载 Kinetics-400 权重；恢复训练会直接读取 checkpoint，不重新下载。若当前 PyCharm 解释器缺少 PyTorch/TorchVision 或版本不匹配，脚本会在启动时给出依赖错误，不会静默改用随机初始化。

默认建议参数：AdamW；骨干学习率 `2e-5`、分类头 `1e-4`、weight decay `1e-4`；最多 30 epochs；视频包梯度累积 8；CUDA 混合精度；梯度范数裁剪 1.0；验证 AUC 连续 6 轮未提升至少 0.001 时早停；ReduceLROnPlateau 因子 0.5、patience 2。每视频包按相同权重计算损失，MIL 的 max 窗口只回传该最大实例的梯度；小批量下会冻结预训练 BatchNorm 的运行统计量。Kinetics-400 权重原先使用更长的时序输入，当前协议每窗只有 4 帧；请重点检查训练曲线、验证 AUC 和各数据集的分项指标。每个 epoch 完成后会原子保存 `last.pt`；中断后从最后一个完整 epoch 恢复：

```powershell
python model_code/mc3_18_mil_pipeline.py --resume "model_runs/mc3_1s4f/run_YYYYMMDD_HHMMSS/last.pt"
```

也可以单独评估一个导出的 `best_model.pt`：程序会在验证集重新校准新协议的三个阈值，然后测试一次并写入新的评估目录；不会沿用 5 秒模型的旧阈值。

```powershell
python model_code/mc3_18_mil_pipeline.py `
  --evaluate-only `
  --checkpoint "model_runs/mc3_1s4f/run_YYYYMMDD_HHMMSS/best_model.pt"
```

可用 `--dataset-root`、`--output-root`、`--run-dir`、`--cache-dir`、`--epochs`、`--device auto|cuda|cpu` 调整位置和运行参数。CPU 训练/评估需显式添加 `--device cpu --allow-cpu`。训练目录会保存 `run.log`、`dataset_manifest.csv`、配置、训练历史、每视频和每窗口概率、阈值及最终测试报告。`best_model.pt` 中也保存了 1 秒/0.5 秒/4 帧预处理协议，供后续部署接入使用。

### 图形化训练与单视频推理工作台

安装好 MC3-18 训练环境后，在 PyCharm 中打开并运行 `model_code/mc3_18_dashboard.py`，或在项目根目录执行：

```powershell
python model_code/mc3_18_dashboard.py
```

程序会启动仅绑定本机 `127.0.0.1` 的网页服务，并自动打开 `http://127.0.0.1:8765`。PyCharm 的 Run 控制台会显示服务地址；需要保持该运行进程开启，关闭服务时在控制台按 `Ctrl+C`。如果端口已被占用，可用 `--port 8766` 指定其他端口。训练、验证与推理使用启动 dashboard 的同一个 Python 解释器，因此应在 PyCharm 的该解释器中安装 PyTorch、TorchVision、OpenCV 和 NumPy。

**训练与评测**页面的路径和超参数默认填入推荐值，可在新建训练前调整；页面会显示阶段进度、epoch、训练损失、验证 AUC/F1、数据集分项验证表、训练曲线及实时控制台日志。训练结果同时写入所选输出目录，浏览器关闭不会删除已保存的结果。点击“本轮结束后暂停”会等当前 epoch 完成并保存 `last.pt`，随后在最终阈值校准和测试前暂停；选择列表中的该运行并点“继续训练”，会从下一个 epoch 恢复。点击“立即停止”会中断当前 epoch，之后只能从最近一个已完整保存的 epoch 恢复，当前未完成 epoch 会重做。

**视频推理**页面仅列出已完成验证阈值校准的运行。选择模型、Sensitive/Moderate/Certain 模式和 MP4/AVI 文件后，页面实时显示上传与逐窗口推理进度，并展示视频级判定、整段分数和各完整窗口的分数。选择视频时，本机 FFmpeg 自动准备 H.264/AAC MP4 预览，保留原音轨，原视频不修改；无音轨时会明确提示。需要 FFmpeg 和 FFprobe（可从 PATH 或 WinGet 安装目录读取，本机已安装）。预览准备独立于模型推理；音量滑块支持 0–100%，默认 80%，窗口和全屏均可调节。切换视频或关闭页面会释放预览副本；超过一小时的旧副本在下一次准备预览时清理。

分析完成后播放预览，右上角按播放时间显示窗口级 Fight 概率；达到所选模式的验证集阈值时，卡片变红，并在进入警示状态时响一次短 beep。持续高于阈值不会反复鸣响，暂停及拖动进度条不会触发；“警示音”开关独立于视频音量，可用“试听”确认声音输出。提示框只在全屏时放大约 1.5 倍，窗口模式保持原尺寸。全屏会按视频实际宽高比计算画布尺寸，横屏、竖屏和正方形画面都会完整居中显示；音量与警示音控件在全屏时上移，避开底部的播放控制条。概率提示使用已完成推理的窗口分数随播放更新，不会在播放期间重新运行模型。文件大小上限为 300 MB；不足 1 秒的视频不会补帧。三个模式共用同一模型，使用各自在验证集上校准的阈值。更新 Python 服务代码后需要重启工作台，单纯刷新旧服务无法启用音轨转换接口。

旧的 `mc3_18_infer.py` 仍对应原 5 秒/20 帧协议；请使用工作台推理新 MIL checkpoint，避免把新模型交给旧预处理流程。

若短视频不足 1 秒、训练集不平衡或不同数据集分项指标差异较大，需先查看 manifest 和分项报告；不要用测试集反复调参或阈值。

## 当前项目中的模型和数据位置

在本项目根目录下，当前可直接找到：

- YOLO 数据集：`Dangerous Items/`
- YOLOv9 官方运行时：`results (4)/yolov9/`
- 当前 GuardAId 使用的武器 checkpoint：`model_runs/yolov9_weapon_workspace/runs/yolov9_20261002_013842_1a0543/best_inference.pt`
- 当前 GuardAId 的 15 个武器阈值：`model_runs/yolov9_weapon_workspace/runs/yolov9_20261002_013842_1a0543/deployment_weapon_thresholds.json`
- MC3-18 checkpoint：`checkpoints_rwf2000/mc3_18_best.pt`

RWF-2000 原始视频没有放在本项目根目录中。训练脚本要求外部数据集满足：

```text
RWF-2000/
├── train/Fight/*.avi
├── train/NonFight/*.avi
├── val/Fight/*.avi
├── val/NonFight/*.avi
├── test/Fight/*.avi
└── test/NonFight/*.avi
```

## 运行示例

### 1. YOLOv9-T 五类武器训练工作台

本地网页会合并 Dangerous Items、Long Gun 和 Knife 数据，并在独立缓存中完成标签映射、重复检查、争议样本排除和图片预览。Dangerous Items 保留原有 split；Long Gun 和 Knife 使用 seed 2026 按正样本／空标签分层，拆分为 train/val/test（70/15/15）。源图片与标签保持原位，逐样本分组及划分写入缓存的 `supplementary_split_manifest.csv`。训练结果、指标、检查报告和可恢复 checkpoint 写入 `model_runs/yolov9_weapon_workspace`。

使用项目 CUDA 环境安装工作台所需依赖后启动：

```powershell
python -m pip install -r model_code/requirements-yolov9-dashboard.txt
python model_code/yolov9_weapon_dashboard.py
```

浏览器打开 `http://127.0.0.1:8767/`。页面默认采用 YOLOv9-T COCO 预训练权重、640 图片尺寸、batch 16（CUDA 探测会自动降至可运行值）、AdamW 和一小时总预算。开始按钮会先生成数据审计与清理副本，再测量训练速度并计算时间预算轮数。首次安装 Python 依赖及 COCO 权重不计入任务的一小时计时。

### 1A. 完整数据集 V1（Knife / Gun）训练页面

同一个本地服务提供固定配方页面：`http://127.0.0.1:8767/full-dataset-v1`。它不提供数据源选择器，只允许调整训练超参数；图片、标签映射、划分与重复处理由后端固定。V11 中带 `other` 标注的整张样本会排除，并写入运行的 `excluded_samples.csv`；这些样本不会成为负样本。页面可在 epoch 结束后安全暂停、恢复，查看实时曲线及逐类阈值结果，并用已完成的 V1 模型上传图片推理。该页面的缓存、运行历史和 checkpoint 独立保存在 `model_runs/yolov9_full_dataset_v1`，不会混入五类工作台。

图片推理的模型列表还包含一个只用于推理的导入模型：`BREV YOLOv9-T Best · imported · Knife / Gun`。它读取 `artifacts/brev_fullv1_20261009_063503_10ce0a/best_inference.pt` 及同目录中的 `sensitivity_thresholds.json`；不会伪装成训练运行，也不会改变训练结果。三个敏感等级的六个类别阈值直接从该报告读取：Sensitive（Knife 0.1538、Gun 0.2276）、Moderate（Knife 0.5272、Gun 0.4114）、Certain（Knife 0.6443、Gun 0.5589）。

使用方法：从项目根目录启动本地服务（若服务已经运行，先在启动它的终端按 `Ctrl+C`，再重新启动以加载更新后的代码）：

```powershell
.\.venv\Scripts\python.exe model_code\yolov9_weapon_dashboard.py
```

浏览器打开 `http://127.0.0.1:8767/full-dataset-v1`，进入 **结果与图片推理 → 06 · Single image inference**，在 **推理模型** 选择 BREV imported 模型，选择 Sensitive / Moderate / Certain，上传一张不超过 12 MB 的图片并点击 **开始图片推理**。页面会显示标注预览、类别、置信度、原图 bounding-box 坐标和实际采用的阈值。推理仍在本机完成；若同一工作台正在训练或评估，需等 GPU 任务结束后再推理。这个入口只增加单张图片推理，不会切换或覆盖 GuardAId Web Dashboard 的实时监控模型。

训练完成后，Sensitive / Moderate / Certain 分别按验证集 Knife 与 Gun 的 F2 / F1 / F0.5 目标选择阈值，共六个阈值；验证集和测试集的逐类 precision、recall、F 分数及 TP/FP/FN 会分别展示和保存。测试集不参与阈值选择。

最终验证会按类别分别从验证集置信度中选出三套阈值：Sensitive 最大化 F2、Moderate 最大化 F1、Certain 最大化 F0.5；匹配标准为类别一致且 IoU ≥ 0.50。随后将这 15 个锁定阈值应用到测试集计算 precision、recall、F 分数及 TP/FP/FN，不使用测试集挑阈值。每次运行将完整结果保存为 `sensitivity_thresholds.json`，运行详情页可查看并下载；图片推理默认使用 Moderate 的验证集 F1 阈值。

可以单独运行数据审计：

```powershell
python model_code/yolov9_weapon_pipeline.py audit
```

旧版直接训练命令保留以兼容已有工作流；新的网页工作台使用同一训练模型定义，并在独立进程中调用官方 YOLOv9 训练及验证实现。

### 2. YOLOv9-T 推理（旧版 CLI）

需要先准备官方 YOLOv9-T 的 COCO pretrained checkpoint，例如 `yolov9-t-converted.pt`。脚本会生成数据 YAML、hyperparameter YAML、训练日志和 `best.pt`。

```powershell
python model_code/yolov9_weapon_train.py `
  --dataset-root "Dangerous Items" `
  --yolov9-root "results (1)/yolov9" `
  --pretrained "results (1)/yolov9/yolov9-t-converted.pt" `
  --output-dir "model_runs/yolov9_weapon" `
  --device 0
```

没有 GPU 时可以把 `--device 0` 改成 `--device cpu`，但 YOLOv9 训练会明显变慢。

### 3. MC3-18 训练

```powershell
python model_code/mc3_18_train.py `
  --dataset-root "D:/datasets/RWF-2000" `
  --output-dir "checkpoints_rwf2000" `
  --cache-dir "rwf2000_cache_20f_112"
```

默认使用 torchvision 的 MC3-18 pretrained backbone；若机器无法下载 pretrained weights，可加 `--no-pretrained`，但这会改变原实验协议。

### 4. MC3-18 推理

```powershell
python model_code/mc3_18_infer.py `
  --checkpoint "checkpoints_rwf2000/mc3_18_best.pt" `
  --video "sample.mp4" `
  --threshold-mode moderate `
  --output-json "output/mc3_predictions.json"
```

## 依赖

```text
torch
torchvision
opencv-python
numpy
pandas
PyYAML
scikit-learn
ipython
```

YOLOv9 另外需要官方仓库本身及其完整依赖（可参考 `results (1)/yolov9/requirements.txt`）。推理脚本会把 `--yolov9-root` 放进 `sys.path`，因此不需要把 YOLOv9 源码复制进 `model_code`。

## 与原项目的对应关系

- 武器运行时的原始部署实现：`guardaid_monitor/app/inference/weapon.py`
- 暴力运行时的原始部署实现：`guardaid_monitor/app/inference/mc3.py`
- YOLO 训练/比较 notebook：`weapon_detection_kaggle_model_comparison.ipynb`
- YOLO 最终比较 notebook：`weapon_detection_final_model_comparison.ipynb`
- MC3-18 训练/比较 notebook：`rwf2000_kaggle_model_comparison.ipynb`
