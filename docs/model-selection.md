# Capture2Doc 模型选型记录

> 状态：初始选型，待本地复现  
> 评估基准日期：2026-09-02

## 需求与选择维度

Capture2Doc 的目标不是单页截图转 Markdown，而是多张手机文档照片到固定 XML。因此选型同时考虑：

1. 手机实拍中的倾斜、透视、反光、模糊和背景干扰；
2. 中文、中英混排、小字、表格和公式的忠实识别；
3. 多图输入、页面排序、跨页合并和层级恢复；
4. 固定 Schema 的字段映射、缺失值处理和来源证据；
5. 无依据内容、重复内容和环境背景的抑制；
6. NVIDIA & Apple Silicon 16GB 上的显存、延迟和稳定性。

公开 Benchmark 用于筛选候选，最终决策必须由 Capture2Doc 的真实数据集和实际量化部署结果决定。

## 评估集及其边界

### OmniDocBench

[OmniDocBench](https://github.com/opendatalab/OmniDocBench)评估页面级文字、表格、公式和阅读顺序，适合判断基础文档解析能力。其 Overall 由文字编辑距离、表格 TEDS 和公式 CDM 聚合而成，不直接衡量固定 XML Schema、跨页语义或手机拍摄背景过滤。

主分支当前为 v1.6。v1.5、v1.6 的数据和匹配方法不同，结果不得直接混排计算。

### MDPBench

[MDPBench](https://github.com/Yuliang-Liu/MultimodalOCR/tree/main/MDPBench)包含数字文档和真实拍摄文档，覆盖 17 种语言。它是当前选型中最接近手机拍照条件的公共评估，重点参考 `Photo`、简体中文和繁体中文分项，而不只看 Overall。

它仍然以页面解析为主，不能代替固定 XML 和业务字段评估。

### MPDocBench-Parse

[MPDocBench-Parse](https://arxiv.org/abs/2605.22100)覆盖 433 份、3,246 页中英文多页文档，评估文字、表格、公式、跨页截断合并、阅读顺序和标题层级。它适合暴露单页高分但跨页结构不稳定的问题。

公开结果主要用于证明多页恢复仍是独立难题；不同系统可能包含页面循环、pipeline 和端到端模型，不能把分数简单解释为多图联合推理能力。

### ParseBench

[ParseBench](https://huggingface.co/datasets/llamaindex/ParseBench)面向企业文档，分别评估内容忠实度、语义格式、布局定位、表格和图表。它适合发现遗漏、幻觉、阅读顺序和格式丢失，但主要输出仍偏 Markdown，不直接检查 Capture2Doc 的 XSD。

### ExtractBench

[ExtractBench](https://github.com/run-llama/ExtractBench)给定文档和用户定义的 JSON Schema，评估字段值、重复结构、完整性和证据定位。虽然目标格式是 JSON，但其 Schema-guided extraction 与固定 XML 映射最接近，可用于判断通用 VLM 编排器的字段提取能力。

## 公开结果摘要

以下数字来自对应项目截至基准日期公开的结果。`—` 表示缺少可直接引用的同版本结果。不同列的任务和版本不同，禁止求和生成新的综合分。

| 模型 | 参数规模 | OmniDocBench | MDPBench Overall / Photo | ExtractBench | 角色判断 |
|---|---:|---:|---:|---:|---|
| PaddleOCR-VL-1.6 | 0.9B VLM，完整 pipeline | v1.6：96.34 | 78.9 / 76.3 | — | 中文页面解析主选 |
| MonkeyOCRv2-B-Parsing | 0.7B | — | 83.3 / 81.7 | — | 实拍与多语言备选 |
| dots.mocr | 3B | 未采用同版结果 | 80.5 / 77.2 | — | 图文混合候选 |
| Qwen3-VL-8B-Instruct | 8B | — | 68.3 / 65.0 | — | 单模型与编排对照 |
| Qwen3.5-9B | 9B | v1.5：87.7（模型卡） | 65.7 / 62.7 | 86.1 | XML 编排主选 |
| Qwen3.5-4B | 4B | v1.5：86.2（模型卡） | — | 82.43 | Apple 16GB 编排主选 |
| Qwen3.8-27B | 27B | v1.5：91.1（模型卡） | — | 89.75 | 质量上限对照 |
| Unlimited-OCR | 3B-A0.5B | v1.6：93.92（论文） | 54.1 / 46.1 | — | 干净长文档实验组 |

主要来源：

- [OmniDocBench v1.6 官方结果](https://github.com/opendatalab/OmniDocBench#end-to-end-evaluation)
- [PaddleOCR-VL 官方说明](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md)
- [MDPBench 官方结果](https://github.com/Yuliang-Liu/MultimodalOCR/blob/main/MDPBench/README.md#main-results)
- [MonkeyOCRv2 官方仓库](https://github.com/Yuliang-Liu/MonkeyOCRv2)
- [Qwen3.5-9B 模型卡](https://huggingface.co/Qwen/Qwen3.5-9B)
- [Qwen3.8-27B 模型卡](https://huggingface.co/Qwen/Qwen3.8-27B)
- [ExtractBench 官方数据集与结果](https://huggingface.co/datasets/llamaindex/ExtractBench)
- [Unlimited-OCR 论文](https://arxiv.org/abs/2606.23050)

## Unlimited-OCR 的特殊判断

Unlimited-OCR 使用 Reference Sliding Window Attention，使生成阶段的 KV Cache 保持近似固定，并支持把多张页面图放入一次前向过程。它适合长文档连续转写，但不能等同于通用 VLM 的长程语义推理：模型持续关注所有视觉参考 token，却只保留有限的近期输出上下文。

官方多页模式使用 1024×1024 Base 输入，不能使用单页 Gundam 动态裁剪；论文也指出多页模式的小字错误主要受该分辨率限制。其多页训练样本由单页数据拼接合成，公开的长文档实验为自建测试集，因此仍需在真实手机照片上独立验证。

在 [ParseBench 公布结果](https://huggingface.co/baidu/Unlimited-OCR/commit/3e0df1252f340521a9f2cfd4d3f0edb704c9468f)中，它的 Text Content 为 86.81、Layout 为 71.52、Table 为 70.21，但 Text Formatting 为 0.97、Chart 为 1.34、均值为 46.17。结合 MDPBench 的 Photo 46.1，它暂不适合作为手机实拍或固定业务 XML 的唯一模型。

Unlimited-OCR 没有通用 chat template，依赖指定 prompt、特殊 token 和 no-repeat logits processor。正确集成方式是解析其 `<|ref|>`、`<|det|>` 结果并转换为内部块记录，再交给编排器映射 XML，而不是要求它直接遵循任意 XSD。

## 选型结论

### 主方案：PaddleOCR-VL-1.6 + Qwen3.5-9B 4-bit

PaddleOCR-VL-1.6 在页面文字、表格和公式上提供较强且成熟的 pipeline；Qwen3.5-9B 在 ExtractBench、长上下文和通用指令遵循方面更适合固定 Schema 映射。两者职责分离，也便于针对 OCR 或 XML 层分别替换和评测。

该组合是 NVIDIA 16GB 档位的主方案。Apple Silicon 16GB 使用相同页面解析器和接口，但默认把编排器调整为 Qwen3.5-4B 4-bit，以给系统、视觉编码器和中间结果保留统一内存空间。9B 在 Apple 16GB 上只作为实测候选。

### 实拍备选：MonkeyOCRv2-B-Parsing + Qwen3.5-9B 4-bit

MonkeyOCRv2-B-Parsing 在 MDPBench 的 Overall 和 Photo 分项领先当前本地候选，应进入真实低质量拍照和多语言样本的 A/B 测试。中文文档仍需与 PaddleOCR-VL-1.6 单独比较，不能从总分推断胜负。

### 长文档实验组：Unlimited-OCR + Qwen3.5-9B 4-bit

它验证一次输入多页、连续生成和固定 KV Cache 的工程价值，适用重点是干净扫描件和数字 PDF。若预处理后的手机照片、小字或格式恢复未达到门槛，则不进入默认路径。

### 质量上限：Qwen3.8-27B

Qwen3.8-27B 的公开 Schema 提取成绩更高，也支持视觉输入。它可以通过激进量化、CPU offload 或远程硬件运行，但在 16GB 单卡上会压缩视觉编码、KV Cache 和并发空间，因此不作为默认生产候选。它用于衡量 9B 方案距离更大模型的实际质量差距。

## 自建评测计划

### 对照系统

1. Qwen3.5-9B 4-bit 单模型；
2. PaddleOCR-VL-1.6 + Qwen3.5-9B 4-bit；
3. MonkeyOCRv2-B-Parsing + Qwen3.5-9B 4-bit；
4. Unlimited-OCR + Qwen3.5-9B 4-bit；
5. Apple Silicon：PaddleOCR-VL-1.6 + Qwen3.5-4B 4-bit。

Qwen3-VL-8B 仅在主候选出现速度、兼容性或质量问题时加入第二轮；Qwen3.5-4B 在 Apple Silicon 16GB 档位首轮评测，在 NVIDIA 档位只作为资源受限对照。

### 数据组成

首轮准备 100 至 300 份具有最终 XML 标注的真实文档，覆盖：

- 单页及 2、4、8 页输入；
- 中文、中英混排、小字号和密集文本；
- 简单表格、合并单元格及跨页表格；
- 正常、倾斜、透视、反光、阴影、模糊和裁切不完整；
- 桌面、键盘、手指及其他页面外物体；
- 页眉页脚、页码和跨页段落；
- 缺失字段、重复记录和无法辨认字段。

### 评分权重

| 类别 | 权重 |
|---|---:|
| XML 字段和值、树结构正确性 | 35% |
| OCR 内容准确度 | 25% |
| 页面顺序、跨页合并和标题层级 | 15% |
| 表格和公式 | 10% |
| 无关内容过滤、遗漏和幻觉 | 10% |
| 延迟、显存峰值和失败率 | 5% |

XML 评分以规范化的 XPath-value 对为基础，同时记录 XML Parse Rate、XSD Validation Rate、字段 Precision/Recall/F1、字符错误率、树编辑距离、重复节点率、证据定位正确率和无依据值比例。

### 准入门槛

- 经过约束生成和固定修复流程后，XSD 通过率为 100%；
- `cuda-16g` 与 `apple-16g` 均在单次 8 页目标分辨率输入时不发生 OOM；
- 无法确认的必填字段产生明确错误或不确定标记，不得编造；
- 每个业务值能够追溯到页面或块级证据；
- 同一输入以确定性配置重复三次，规范化 XML 结果一致；
- 所有模型使用计划部署的量化、图像分辨率、提示词和推理框架评测。

## 待验证事项

- 4-bit 量化对 Qwen3.5-9B OCR 复核和 Schema 映射准确率的影响；
- PaddleOCR-VL-1.6 与 MonkeyOCRv2-B 在中文手机照片子集上的差异；
- Unlimited-OCR 多页 Base 模式在预处理后手机照片上的小字表现；
- 页面缩略图与疑难裁剪的视觉 token 预算；
- 受约束 XML 生成、校验和无事实新增修复流程的可行性。
- PaddleOCR-VL-1.6 与 Qwen3.5-4B 在不同代际 Apple Silicon 16GB 设备上的兼容性、速度和内存峰值。
