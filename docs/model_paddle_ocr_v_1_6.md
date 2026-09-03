# PaddleOCR-VL-1.6 输入 Token 与 vLLM 调优记录

> 状态：NVIDIA/WSL 原型验证
> 记录日期：2026-09-03
> 对应模型：`PaddlePaddle/PaddleOCR-VL-1.6`，ModelScope `master` revision
> 已验证环境：PaddleOCR-VL-1.6 BF16、vLLM 0.28.0、RTX 4070 Ti Super 16GB

本文回答三个问题：一张图片会占多少 token、这些 token 如何进入语言模型，以及在
Capture2Doc 单图页面解析 Worker 中应如何设置 `max_model_len` 和其他 vLLM 参数。
结论来自当前 ModelScope 快照中的配置与 processor 源码，并使用两张本地测试图做了
实测。`master` 是可变 revision，模型升级后必须重新执行本文的 token 检查。

## 结论摘要

- 默认 processor 会把每张图片动态缩放到约 0.11 至 1.00 百万像素，并将尺寸对齐到
  28 像素的整数倍。
- 默认一张图片占用 **144 至 1280 个 image tokens**，而不是由 JPEG 文件大小或原始
  相机分辨率直接决定。
- 两张 `4000×3000` 测试图都产生了 **1230 image tokens**；加上当前 chat template
  和 `OCR:` prompt 后，实际输入长度为 **1243 tokens**。
- 当前请求允许最多生成 4096 tokens，因此单图最坏预算约为
  `1280 + 13 + 4096 = 5389 tokens`。当前已将 `max_model_len` 从 16384 调整到
  **8192** 仍有充分余量。
- `max_model_len` 主要限制单个请求的总长度；它不是图像清晰度开关。图像 token 数和
  OCR 质量主要由 `max_pixels` 控制。
- 当前 Worker 固定使用 **256 MiB KV cache**。单个 8192-token 序列的理论需求约为
  144 MiB，因此仍留有实现开销余量，同时避免按显存比例预留数 GiB 空间。

## 当前快照中的关键配置

| 配置 | 当前值 | 含义 |
|---|---:|---|
| 模型规模 | 0.9B | PaddleOCR-VL-1.6 VLM |
| 权重精度 | BF16 | 当前未量化 |
| `max_position_embeddings` | 131072 | 模型配置声明的理论位置上限，不等于本机应配置到该值 |
| Vision patch size | 14 | 每个原始视觉 patch 覆盖 `14×14` 像素 |
| Spatial merge size | 2 | 每 `2×2` 个视觉 patch 合并为一个语言模型 image token |
| `min_pixels` | 112896 | `144 × 28²`，对应至少 144 image tokens |
| `max_pixels` | 1003520 | `1280 × 28²`，对应至多 1280 image tokens |
| 语言模型层数 | 18 | 用于估算 KV cache |
| KV heads / head dim | 2 / 128 | BF16 KV cache 约为 18 KiB/token |

配置中的 Vision `temporal_patch_size=2` 与当前 image processor 的
`temporal_patch_size=1` 不要混淆。静态图片处理实际使用后者，时间维度 `T=1`。

## 一张图片如何变成 image tokens

### 1. 动态缩放

processor 先将图片转换为 RGB，然后保持宽高比，将面积限制在：

```text
112896 <= resized_height × resized_width <= 1003520
```

缩放后的宽高都必须是 `patch_size × merge_size = 14 × 2 = 28` 的整数倍。
大于上限的手机原图会缩小；小于下限的小图会放大。因为需要进行 28 像素对齐，实际
宽高会有少量取整。

### 2. 切分视觉 patch

缩放后的图片被切成 `14×14` patch：

```text
grid_h = resized_height / 14
grid_w = resized_width / 14
raw_vision_patches = grid_h × grid_w
```

processor 将网格记录为：

```text
image_grid_thw = [1, grid_h, grid_w]
```

### 3. 合并到语言模型维度

Projector 把相邻 `2×2` 个视觉特征拼接并映射到语言模型 hidden size，因此最终：

```text
image_tokens
  = T × grid_h × grid_w / 2²
  = resized_height × resized_width / 28²
  = (resized_height / 28) × (resized_width / 28)
```

所以默认上下限恰好是：

```text
minimum image tokens = 112896 / 28² = 144
maximum image tokens = 1003520 / 28² = 1280
```

整体输入过程可以简化为：

```text
原始图片
  → 保持比例缩放，宽高对齐到 28
  → 14×14 Vision patches
  → Vision Encoder
  → 每 2×2 patch 合并
  → N 个 image embeddings
  → 替换文本序列中的 N 个 <|IMAGE_PLACEHOLDER|>
  → 与 OCR: prompt 一起进入自回归语言模型
```

## image tokens 在输入序列中的组织方式

当前 chat template 生成的逻辑序列为：

```text
<|begin_of_sentence|>
User:
<|IMAGE_START|>
<|IMAGE_PLACEHOLDER|> × N
<|IMAGE_END|>
OCR:\n
Assistant:\n
```

processor 根据 `image_grid_thw`，把单个图片占位符展开成 `N` 个 image token ID。
模型先为完整 token 序列建立文本 embedding，再把这些占位位置替换成 Vision Encoder
输出的 image embeddings。图像区域使用包含时间、高度和宽度三个轴的 position IDs；
静态图片的时间轴为 1。

因此 image tokens 与普通文本 token 位于同一上下文序列中，会占用
`max_model_len`，也会参与语言模型 attention 和 KV cache 预算。图片文件的 JPEG
压缩率只影响上传大小和解码成本，不直接影响 token 数。

## 本地实测

使用当前 ModelScope `master` 快照、官方 processor 和实际 chat template：

| 输入 | 原始尺寸 | `image_grid_thw` | image tokens | 完整 prompt tokens |
|---|---:|---:|---:|---:|
| `test_image_1_larkdoc.jpg` | 4000×3000 | `[1, 60, 82]` | 1230 | 1243 |
| `test_image_2_whiteboard.jpg` | 4000×3000 | `[1, 60, 82]` | 1230 | 1243 |

两张图片内容不同但宽高相同，因此 token 数相同。默认预处理关注缩放后的几何尺寸，
不会因为页面文字更多就分配更多 image tokens。

对同一张 `4000×3000` 图片降低 `max_pixels` 后的实测如下：

| image-token 上限 | `max_pixels` | 实际网格 | 实际 image tokens |
|---:|---:|---:|---:|
| 1280，默认 | 1003520 | `[1, 60, 82]` | 1230 |
| 1024 | 802816 | `[1, 54, 72]` | 972 |
| 768 | 602112 | `[1, 48, 64]` | 768 |
| 512 | 401408 | `[1, 38, 52]` | 494 |
| 256 | 200704 | `[1, 26, 36]` | 234 |

降低 `max_pixels` 可以近似线性减少 image tokens 和视觉编码计算量，但小字、公式、
表格线与复杂排版的识别质量也可能下降。不能仅凭启动显存选择该参数，必须用真实手机
照片评测。

## 如何设置 `max_model_len`

vLLM 对该参数的定义是单个请求的 **prompt 与 output 总长度**：

```text
required_context
  = image_tokens
  + chat template/text prompt tokens
  + maximum generated tokens
```

当前单图请求为：

```text
maximum image tokens       1280
current wrapper/prompt    +  13
client max_tokens         +4096
                           ----
worst-case budget          5389
```

因此当前阶段推荐：

```text
max_model_len = 8192
```

它能容纳默认最大分辨率图片和 4096 输出 token，并保留约 2800 tokens 余量。继续使用
16384 不会提高单张图片的视觉分辨率或 OCR 质量，只会允许更长输出、更多图片或更多
并行 KV cache 容量。

如果未来一次请求传入多张图片，估算公式变为：

```text
required_context ≈ sum(image_tokens_per_image) + wrapper + max_output_tokens
```

例如 8 张都达到默认上限时，视觉部分最多约 `8×1280=10240 tokens`；加 4096 输出后
需要接近 16K。但 PaddleOCR-VL 当前在 Capture2Doc 中只承担单页解析，多图内容融合由
后续编排层负责，不能仅因为 16K 数学上放得下就假定模型具备可靠的跨图理解能力。

## KV cache 与显存预算

当前语言模型有 18 层、2 个 KV heads、head dimension 128，KV cache 为 BF16。理论上：

```text
KV bytes/token
  = layers × KV_heads × head_dim × K/V × BF16_bytes
  = 18 × 2 × 128 × 2 × 2
  = 18432 bytes
  = 18 KiB/token
```

因此单个满长度请求大约需要：

| `max_model_len` | 单序列 KV cache 理论值 |
|---:|---:|
| 4096 | 72 MiB |
| 8192 | 144 MiB |
| 16384 | 288 MiB |

首次使用旧配置成功启动的 vLLM 日志显示：

- 模型权重占用约 1.82 GiB；
- 权重及 non-torch 固定占用约 1.98 GiB；
- peak activation 约 0.44 GiB；
- CUDA Graph 约 0.05 GiB；
- `gpu_memory_utilization=0.5` 为 KV cache 分配了约 5.57 GiB，可容纳约
  324496 tokens。

这远高于 `max_num_seqs=1` 的单图需要。当前默认改为
`kv_cache_memory_bytes=268435456`（256 MiB），它理论上可容纳约 14563 个 KV tokens，
高于单序列 `max_model_len=8192`。该参数只限制 KV cache，不会压缩 BF16 模型权重、
视觉编码器的中间激活或 CUDA Graph，所以不能将其理解为整个 Worker 的显存上限。

使用本轮默认参数重新进行 WSL 冒烟测试后，vLLM 日志记录了 0.25 GiB KV cache、
14560 个 GPU KV cache tokens，以及 8192-token 请求约 1.78 倍的理论最大并发。单张
测试图得到 1243 prompt tokens、517 completion tokens、851 个 OCR 字符，
`finish_reason=stop`；Worker 退出后显存回到启动前的 2049 MiB，且没有残留计算进程。

## 可调整参数

### 图像质量与视觉计算

| 参数 | 旧值 | 当前默认值 | 影响 |
|---|---:|---:|---|
| processor `max_pixels` | 1003520 | 1003520 | 可实验覆盖为 802816 / 602112，对应约 1024 / 768 image tokens |
| processor `min_pixels` | 112896 | 112896 | 防止小图被编码得过小，本轮不调整 |
| `limit_mm_per_prompt` | vLLM 默认较宽 | `{"image": 1}` | 明确单图 Worker 边界，拒绝意外多图 |

vLLM 0.28.0 可用 `mm_processor_kwargs` 覆盖 processor，例如把理论上限降到
768 image tokens：

```bash
--mm-processor-kwargs '{"max_pixels": 602112}'
```

官方 Transformers 示例对普通 OCR 使用 `1280×28²`，对 spotting 任务提高到
`2048×28²`。Capture2Doc 当前使用 `OCR:`，不应直接采用 spotting 的更大预算。

### 上下文、调度和并发

| 参数 | 旧值 | 当前默认值 | 说明 |
|---|---:|---:|---|
| `max_model_len` | 16384 | 8192 | 单请求 prompt + output 上限 |
| `max_num_batched_tokens` | 16384 | 4096 | 每次调度迭代的 token 上限；已启用 chunked prefill |
| `max_num_seqs` | 1 | 1 | 当前不需要批量并发 |
| OpenAI `max_tokens` | 4096 | 4096 | 真正限制最多生成多少 OCR token |
| `temperature` | 0 | 0 | 使用确定性 greedy decoding |
| prefix caching | 关闭 | 关闭 | 每张页面图不同，当前收益有限 |
| `mm_processor_cache_gb` | 0 | 0 | 单请求本地 Worker 无需额外 CPU processor cache |

`max_num_batched_tokens=4096` 小于 `max_model_len=8192` 并不禁止 8K 请求；chunked
prefill 会把较长 prompt 分块处理。它会降低单次调度和启动 profile 的预算，但仍需
通过真实图片验证延迟。

### 显存与执行模式

| 参数 | 旧值 | 当前默认值 | 说明 |
|---|---:|---:|---|
| `gpu_memory_utilization` | 0.5 | 不传递 | 仅作为 CLI 实验覆盖项，启用时关闭固定 KV cache |
| `kv_cache_memory_bytes` | 自动 | 268435456（256 MiB） | 固定 KV cache，跨不同 16GB 设备更易复现 |
| `dtype` | BF16 | BF16 | 4070 Ti Super 原生支持，不建议为省少量内存改 FP16 |
| `enforce_eager` | false | 仅作故障对照 | 可关闭 CUDA Graph，但当前只节省约 0.05 GiB，通常会损失性能 |

当前只选择固定 256 MiB 作为生产默认值，不再同时传递
`gpu_memory_utilization`。smoke CLI 仍允许使用 `--gpu-memory-utilization 0.2` 做实验；
指定后会关闭默认固定 KV cache。两个参数在 CLI 中互斥，避免依赖 vLLM 的忽略规则
形成含糊配置。

## 当前 WSL 问题与兼容处理

### UVA / pinned memory

vLLM 0.28.0 的 V2 Model Runner 需要 UVA buffer。vLLM 在兼容的 WSL2 内核上仍默认
关闭 pinned memory，因此第一次启动失败：

```text
RuntimeError: UVA is not available
```

当前 WSL 内核为 `6.6.87.2-microsoft-standard-WSL2`，设置下列环境变量后已经验证
`pin_memory=True`、`uva=True`，模型权重、KV cache 和 HTTP Server 均成功初始化：

```bash
VLLM_WSL2_ENABLE_PIN_MEMORY=1
```

`runtime.py` 现在会在检测到 WSL2 时只为 vLLM 子进程默认设置该变量，无需用户永久
写入 shell 配置，并保留用户显式设置的值。若 pinned memory 在真实推理中出现 WSL
内存问题，再把
`VLLM_USE_V2_MODEL_RUNNER=0` 作为兼容性对照，而不是首选路径。

### FlashInfer sampler / CUDA 13

当前 `flashinfer 0.6.16.post3` 在 CUDA 13.0、Ada `sm_89` 上 JIT 编译 sampling kernel
时，会因随包 CUB API 不兼容报出 `BlockAdjacentDifference::FlagHeads` 错误。由于当前
OCR 请求使用 greedy decoding，Worker 默认设置：

```bash
VLLM_USE_FLASHINFER_SAMPLER=0
```

这只让采样回退到 vLLM 的 PyTorch 实现，不改变模型权重、视觉编码或 KV cache 配置。
`runtime.py` 不会覆盖用户显式设置，待 FlashInfer/CUDA 组合修复后可重新进行性能对照。

### 本机 `127.0.0.1` 策略路由

Worker 日志已经出现 `Application startup complete`，但 smoke 脚本仍无法连接
`http://127.0.0.1:8118/health`。本机实测路由为：

```text
127.0.0.1 via 169.254.73.152 dev loopback0 table 127
```

它没有走标准 Linux `lo`。Windows 配置文件 `.wslconfig` 当前启用了
`networkingMode=mirrored`，`loopback0` 也是 Hyper-V/WSL 提供的 vmbus 接口。因此先把
问题归类为 **WSL mirrored networking 下的 loopback policy-routing 冲突**，不能在
缺少进一步证据时直接归因给 SD-WAN。这不是模型启动失败。

本机另有绑定在 `lo` 上的 `10.255.255.254/32` 地址，已经使用临时 HTTP Server 实测：

```text
bind 10.255.255.254:8123 → GET http://10.255.255.254:8123/ → HTTP 200
```

当前处理方式为：

1. Worker bind host 和客户端 connect host 已通过 smoke CLI 的 `--host` 参数统一覆盖；本机使用
   已经验证的
   `10.255.255.254`，其他机器仍默认使用 `127.0.0.1`；
2. 遇到连接失败时检查 `ip route get 127.0.0.1` 和 WSL networking mode；
3. 只有确认不会影响 mirrored networking、SSH 和 SD-WAN 后，才考虑为
   `127.0.0.0/8` 修改 policy-routing 豁免；
4. 不应为了绕过该问题直接把无鉴权的 OpenAI-compatible API 暴露到 `0.0.0.0`。

修改路由前需要确认 SD-WAN 的规则所有者和重启恢复机制，避免破坏现有 SSH 转发。

## 当前运行配置与后续验证

代码中的默认配置已经收敛为：

```text
max_pixels               = 1003520
max_tokens               = 4096
max_model_len            = 8192
max_num_batched_tokens   = 4096
max_num_seqs             = 1
kv_cache_memory_bytes    = 268435456
gpu_memory_utilization   = unset
limit_mm_per_prompt      = {"image": 1}
```

smoke CLI 支持覆盖这些资源参数，并在 `summary.json` 中记录有效配置、响应 `usage` 和
`finish_reason`。下一步使用真实手机照片验证默认配置的识别质量、峰值显存、首 token
延迟和是否发生输出截断；确认基准后，再单独比较 1024、768 image-token 档。token
inspection 脚本仍作为后续工具项，不属于本轮默认参数调整。

## 来源

- [PaddleOCR-VL-1.6 ModelScope 模型卡](https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL-1.6)
- [PaddleOCR-VL 官方 pipeline 文档](https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PaddleOCR-VL.html)
- [vLLM PaddleOCR-VL recipe](https://docs.vllm.ai/projects/recipes/en/latest/PaddlePaddle/PaddleOCR-VL.html)
- [vLLM Serve 参数](https://docs.vllm.ai/en/latest/cli/serve/)
- [vLLM 0.28.0 WSL pinned-memory 判断](https://github.com/vllm-project/vllm/blob/v0.28.0/vllm/platforms/cuda.py#L290-L308)

本文中的具体 token 数、KV cache 估算和 WSL 行为还来自已下载的 ModelScope 快照文件
`config.json`、`preprocessor_config.json`、`image_processing_paddleocr_vl.py`、
`processing_paddleocr_vl.py`、`modeling_paddleocr_vl.py`，以及本机 vLLM 0.28.0 启动日志。
