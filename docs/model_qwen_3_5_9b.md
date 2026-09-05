# Qwen3.5-9B FP8 输入 Token 与独立 Worker

> 状态：NVIDIA/WSL 原型实现，单图冒烟和长上下文压力测试已通过
> 记录日期：2026-09-03  
> 模型：`Qwen/Qwen3.5-9B`，ModelScope `master` revision  
> 目标环境：vLLM 0.28.0、RTX 4070 Ti Super 16GB

实际参数对照、三轮显存记录与当前推荐值见
[Qwen3.5-9B FP8 参数实测与推荐](model_qwen_3_5_9b_parameters.md)。

Qwen3.5-9B 在 Capture2Doc 中用于关联页面解析结果、过滤无关内容并映射到
C2D-XML。本阶段先把它作为独立的单图 VLM Worker 验证，不与 PaddleOCR-VL 同时
常驻，也不把原始模型输出视为已经通过 C2D-XML 校验的文档。

2026-09-05 流程边界补充：目标调度在服务端接受最终页面集合、顺序与不可变内容引用，且所需 OCR 结果齐备后才调用 Qwen。Android 可离线先拍摄和完成本地页序冻结；这不触发 Qwen，须待真实文档 ID、1280 派生图上传和会话提交完成。模型遵循用户确认页序恢复跨页结构，不按上传/OCR 回调顺序处理，也不自行重排采集页。服务端本地 CLI 已连接 Paddle → Qwen 串行推理、完整提示词、真实 token 预算、XML 组装与原子检查点恢复。现已完成五轮有序八图实测，并冻结 [CLI 首版基线](server_pipeline_baseline_v0_1.md)：第五轮 57 块中 56 ok、1 局部 OCR 兜底，仍有内容和样式质量欠账。完成恢复与旧 V1 SIGTERM 测试分别记录，不能等同于 Android HTTP 联通。操作见 [服务端 Pipeline](server_pipeline.md)，证据见 [五轮报告](server_pipeline_evaluation_20260905.md)。 独立 Worker 的参数实验与 CLI 验证分别记录，见 [扫描 Pipeline](capture_pipeline.md) 和 [XML 增量组装](c2d_xml_assembly.md)。

## 当前配置

| 参数 | 默认值 | 说明 |
|---|---:|---|
| 权重来源 | 官方 BF16 | ModelScope 缓存保留原始权重 |
| vLLM 量化 | `fp8_per_channel` | 加载时在线量化，不使用 BitsAndBytes |
| `max_pixels` | 1310720 | `1280 × 32²` |
| 最大 image tokens | 约 1280 | 实际值受长宽比和网格对齐影响 |
| `max_model_len` | 16384 | prompt 与 output 的总上限 |
| `max_tokens` | 8192 | 默认最大生成长度 |
| `max_num_batched_tokens` | 4096 | 通过 chunked prefill 控制单次调度峰值 |
| `max_num_seqs` | 1 | 独立验证阶段单并发 |
| KV cache | 1 GiB | 代码默认值；当前实测推荐通过 CLI 覆盖为 640 MiB |

当前 vLLM 0.28.0 中的 `int8_per_channel_weight_only` 只面向 MoE expert；
Qwen3.5-9B 是 dense 模型，因此 NVIDIA 首轮选择在线 FP8。磁盘快照仍约为 BF16
体积，只有加载后的受支持线性层按 FP8 运行。是否达到预期显存和质量必须从 Worker
日志、实际显存和项目评测确认。

参考：[Qwen3.5-9B 模型卡](https://modelscope.cn/models/Qwen/Qwen3.5-9B/summary)、
[vLLM 在线量化](https://docs.vllm.ai/en/stable/features/quantization/online/)。

## 单图 token 计算

官方 processor 使用 `patch_size=16` 和 `spatial_merge_size=2`。静态图片在空间上
每 `2×2` 个视觉 patch 合并成一个语言模型 token，因此一个 image token 对应缩放后
约 `32×32` 像素：

```text
image_tokens
  = grid_t × grid_h × grid_w / 2²
  = resized_height × resized_width / 32²
```

对已经是 `1280×960` 且宽高都能被 32 整除的派生图：

```text
grid_t = 1
grid_h = 960 / 16 = 60
grid_w = 1280 / 16 = 80
image_tokens = 1 × 60 × 80 / 4 = 1200
```

`max_pixels=1310720` 是面积上限，不要求所有图片变成固定分辨率。processor 保持原
长宽比并对齐网格，因此其他比例的图片可能产生不同数量的 token。项目使用
`image_grid_thw` 计算网格 token，同时统计展开后的 `<|image_pad|>` token；两者不一致
时直接报错。

## Chat template

单图用户消息的逻辑形式为：

```text
<|im_start|>user
<|vision_start|><|image_pad|> × N<|vision_end|>
用户提示词<|im_end|>
<|im_start|>assistant
```

模板先放置图像占位符，processor 再根据 `image_grid_thw` 将其扩展为 `N` 个图像
token。完整 prompt token 还包括角色边界、文字提示和其他特殊 token。工具定义会被
放入 system message，工具调用使用模板规定的 XML 风格 `<tool_call>` 结构，因此未来
加入工具 schema 后必须重新测量文字 token 预算。

Qwen3 系列默认进入 thinking。当前结构映射和冒烟测试默认传递：

```json
{"chat_template_kwargs":{"enable_thinking":false}}
```

启用 thinking 时，推理内容也会消耗 `max_tokens`，只能作为显式实验项。模板来源见
[官方 chat template](https://huggingface.co/Qwen/Qwen3.5-9B/blob/main/chat_template.jinja)。

## 16K 上下文预算

当前容量规划为：

```text
最大图像 token           1280
预期已有文本及提示词     4096
模板安全余量              512
最大输出                 8192
                        -----
计划总量                14080
16K 剩余                 2304
```

`max_tokens=8192` 是生成上限，不保证模型每次都生成到该长度。发送请求前必须用当前
processor 渲染真实模板；若完整 prompt 超过 `16384 - 8192 = 8192` tokens，则拒绝
请求，不自动截断 OCR 文本或缩小图片。

Qwen3.5-9B 有 8 个 full-attention 层。仅按 BF16 K/V 粗略估计，完整 16K 序列约需
512 MiB KV cache；混合 DeltaNet 状态、block 对齐和实现开销仍需余量，因此首轮固定
1 GiB。实测 640 MiB 可为 16K 单序列提供 17788 tokens 的 KV 容量，并已经完成
`5376 prompt + 8192 output` 强制长输出测试，当前作为 CLI 基线覆盖值；512 MiB
只有 14108 tokens，无法覆盖 16K。该配置只约束 cache，不代表整个 Worker 的显存
上限。

同一 640 MiB KV cache 在 `max_model_len=17728` 的实验 Worker 中报告 18207-token
容量，并完成 `9472 prompt + 8192 output`。实际总长度为 17664，只剩 64-token context
余量，输出末段 KV 使用率达到 100%，因此只作为已验证极限，不作为默认配置。

## 使用方式

准备官方快照：

```bash
uv run --extra cuda python scripts/prepare_qwen35.py
```

不加载模型权重，检查图片 token 和模板：

```bash
uv run --extra cuda python scripts/inspect_qwen35_tokens.py \
  --image /absolute/path/document.jpg
```

启动 Worker 并执行单图测试：

```bash
uv run --extra cuda python scripts/smoke_qwen35.py \
  --image /absolute/path/document.jpg
```

当前 WSL mirrored networking 如无法通过标准 loopback 访问，可增加
`--host 10.255.255.254`。smoke 会保存原始响应、token 摘要、渲染模板、vLLM 日志和
显存阶段数据到 `.cache/qwen35-smoke/`。

长上下文和强制长输出的压力测试方法、边界及命令见
[Qwen3.5-9B FP8 参数实测与推荐](model_qwen_3_5_9b_parameters.md#长上下文与长输出压力测试)。

## 后续验证

- 1280 image-token 档对手机文档小字、表格和公式复核的质量；
- 真实约 4K OCR 文本到 C2D-XML 的内容质量、Schema 合法率和实际输出长度；
- PaddleOCR-VL 与 Qwen 重复加载、推理和卸载时的资源清理与 semaphore warning；
- 8192 输出上限是否满足真实 C2D-XML 样本；
- 扩大有序多图质量样本；单请求、先 OCR 后 Qwen 的顺序加载已在 CLI 基线运行，同时常驻不在该基线内。
