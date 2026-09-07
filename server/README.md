# Capture2Doc

Capture2Doc 是一个将连续拍摄的文档照片自动整理为可编辑文档的工具。

用户按顺序拍摄同一份文档后，系统自动识别图片内容，拼接并合并重复内容，生成排版统一的连续文档。尽最大能力还原源文档的标题、段落、列表、表格、代码、公式及粗体、颜色、高亮等格式，可预览并复制到飞书、思源，或导出为 Markdown、纯文本。


Capture2Doc 的本地推理服务。当前提供 NVIDIA/WSL 上可独立验证的
PaddleOCR-VL-1.6 BF16 Worker 和 Qwen3.5-9B FP8 Worker。ModelScope 负责准备模型，
vLLM 只加载本地快照。本地 CLI V2 将 Paddle OCR、Qwen 多 block 草稿与 C2D-XML 校验串行连接，
主要输出文档 JSON 和完整逐块报告；每块最多五轮修复，耗尽后局部兜底并继续后续图片。支持不可变输入和检查点恢复，真实模型结果与质量限制见运行文档。
新增 FastAPI HTTP 业务 API、独立 GPU worker 与 SSE 逐块预览；Android 消费适配留待下一轮。暂不包含完整 PaddleOCR 页面解析或通用 Agent 服务。

当前冻结版本为 [pipeline-cli-v0.1.0](../docs/server_pipeline_baseline_v0_1.md)。整体流程、已知限制和下一步讨论依据集中在基线文档；配置与资源摘要见 [基线清单](baselines/pipeline_cli_v0_1.json)，真实质量证据见 [五轮报告](../docs/server_pipeline_evaluation_20260905.md)。

## HTTP 与 SSE 服务

使用 `uv sync --locked --extra cuda --extra service` 安装。通过 `capture2doc api --config <TOML>` 与
`capture2doc worker --config <TOML>` 分别启动，CLI 基线不依赖 HTTP 服务。每设备令牌、存储、systemd、
OpenResty 和断线续传见 [服务协议与运维](../docs/server_service.md)、[OpenAPI](../docs/server_openapi.json)
及 [验收记录](../docs/server_service_validation.md)。公网 GET 返回完整 C2D-XML JSON；SSE 提供可更新的块预览，
不改变模型整图候选响应或每图正式提交的边界。

## Mac 本地调试（Apple Silicon）

Mac 自动选择 MLX-VLM；Linux/NVIDIA 保持 vLLM。通过 `--qwen-model 4b` 选择
Qwen3.5-4B，默认仍为 `9b`。4B 使用官方未量化权重，沿用 BF16；Mac 上的
9B 同样使用 BF16、不量化，NVIDIA 的 9B 继续使用原 BF16 加载加 FP8 在线量化配置。
PaddleOCR-VL-1.6 在 Mac 使用 BF16。16 GB 统一内存机器应选择 4B。

在 `server/` 目录运行：

```bash
uv sync --locked --extra apple --extra service
uv run --locked --extra apple --extra service python scripts/prepare_paddleocr_vl.py
uv run --locked --extra apple --extra service python scripts/prepare_qwen35.py --qwen-model 4b
uv run --locked --extra apple --extra service python scripts/run_document.py \
  --qwen-model 4b --manifest /absolute/path/input.json \
  --output-dir .cache/documents/mac-debug
```

使用 HTTP 服务时，在私有 TOML 的 `[service]` 下设置 `qwen_model = "4b"`，然后分别启动
`uv run --locked --extra apple --extra service capture2doc api --config <TOML>` 和
`uv run --locked --extra apple --extra service capture2doc worker --config <TOML>`。
也可用 `C2D_QWEN_MODEL=4b` 覆盖 TOML，或为 `worker` 增加 `--qwen-model 4b`
（命令行优先于环境变量和 TOML）。模型准备与运行必须选择相同型号和缓存目录。

首次准备需要联网，默认缓存为 `~/models/modelscope`；运行阶段只使用本地快照。
原始缓存保留官方精度，不另存转换后的权重。MLX 保留模型实现要求的少量高精度参数，
例如 Qwen 的 `A_log`；主权重使用 BF16，不启用权重或 KV 量化。

调度沿用 **Paddle OCR → 退出 Paddle 子进程 → Qwen → 退出 Qwen 子进程**。
MLX 模型权重和 Metal 状态只存在于独立子进程，退出确认失败会阻止加载下一模型。
`*.mlx.log` 和 `*.metrics.json` 记录加载、卸载、实际权重类型以及 MLX 进程内存峰值；
这不是整机统一内存占用。Mac 不使用 `nvidia-smi` 或 NVIDIA 的固定 KV cache 配额。
Qwen 保持 16K 上下文、8K 最大输出、关闭 thinking 和 JSON Schema 输出约束，
预填充按 256 token 分块。实际输入 token 数必须与预检一致，否则停止该请求。

Mac 调试使用新的输出目录／独立服务数据目录；模型、精度或后端变化时，现有检查点的
配置一致性校验仍会生效。现有 `smoke_*` 和 `stress_*` 脚本仍是 NVIDIA 诊断入口；
Mac 可用上述完整文档 CLI，或 `inspect_qwen35_tokens.py --qwen-model 4b --image <图片>`
只检查输入 token。

2026-09-07 在 M4／16 GB 上完成一张 800×400、三行英文测试图的完整 CLI 验证：
OCR 和导出文本与输入一致，生成 3 块、0 次修复、无需人工复核；两个模型均确认退出。
Qwen 实际主权重为 BF16，另保留 3072 字节 FP32 参数。MLX 进程峰值：Paddle 2.34 GiB、
Qwen 9.27 GiB；Qwen 加载约 20.55 秒、生成约 61.10 秒（3302 输入 token）。
445 项自动化回归通过。该结果仅覆盖这张简单测试图，未验证长上下文、多图质量或整机内存上限。

## 文档转换 CLI

以 `document_id` 组织图片，使用不可变 `image_id` 和最终 `ordered_image_ids`，
不增加服务端 page_id。入口、清单、恢复命令和完整边界见
[手机图片到 C2D-XML](../docs/server_pipeline.md)。

```bash
uv run --extra cuda python scripts/run_document.py \
  --manifest /absolute/path/input.json \
  --output-dir .cache/documents/demo-001
```

该 CLI 保持单并发，Paddle 使用原默认配置，Qwen 显式采用 16K context、
8K 最大输出、640 MiB KV cache 和关闭 thinking。完整 system prompt 以
`src/capture2doc/prompts/c2d_blocks_v2.txt` 与 `c2d_contract_v2.txt` 管理，
随包发布并记录运行哈希；旧 `c2d_system.txt` 仅用于 V1 恢复。扩展样式和重叠生成实验默认关闭，基础结构/样式规则仍提供。独立 Qwen Worker 的默认 1 GiB KV 不代表本 CLI 的 640 MiB 配置。

## 环境准备

普通 `uv sync` 不会安装 CUDA 依赖。NVIDIA/WSL 推理机使用：

```bash
uv sync --extra cuda
```

模型缓存默认位于 `~/models/modelscope`，开发时无需额外配置环境变量。需要临时切换
缓存目录时可传入 `--cache-dir`；它的优先级高于 `MODELSCOPE_CACHE`。缓存目录不应
放入 Git 仓库。

## PaddleOCR-VL-1.6

准备官方 BF16 权重：

```bash
uv run --extra cuda python scripts/prepare_paddleocr_vl.py
```

执行单图 OCR 冒烟测试：

```bash
uv run --extra cuda python scripts/smoke_paddleocr_vl.py \
  --image /absolute/path/document.jpg
```

默认参数为 `max_pixels=1003520`（最多约 1280 image tokens）、
`max_tokens=4096`、`max_model_len=8192`、`max_num_batched_tokens=4096`、
`max_num_seqs=1` 和固定 256 MiB KV cache。结果保存在
`.cache/paddleocr-vl-smoke/`。

## Qwen3.5-9B FP8

准备 `Qwen/Qwen3.5-9B@master` 官方 BF16 快照：

```bash
uv run --extra cuda python scripts/prepare_qwen35.py
```

磁盘缓存仍为 BF16；vLLM 加载时通过 `fp8_per_channel` 在线量化。不使用
BitsAndBytes 或第三方量化权重。

先检查同一张图片的 chat template 和 token，不加载模型权重：

```bash
uv run --extra cuda python scripts/inspect_qwen35_tokens.py \
  --image /absolute/path/document.jpg
```

再启动独立 Worker 并发送单图请求：

```bash
uv run --extra cuda python scripts/smoke_qwen35.py \
  --image /absolute/path/document.jpg
```

Qwen 默认使用 `max_pixels=1310720`（最多约 1280 image tokens）、
`max_tokens=8192`、`max_model_len=16384`、`max_num_batched_tokens=4096`、
`max_num_seqs=1` 和固定 1 GiB KV cache。thinking 默认关闭。请求前会保留完整 8192
输出空间；prompt 超过 8192 tokens 时直接失败，不自动截断。

smoke 输出位于 `.cache/qwen35-smoke/`，包含原始响应、token 摘要、渲染模板、
vLLM 日志，以及启动前、模型加载后、推理峰值和退出后的 GPU 全局显存。

### 长上下文与长输出压力测试

使用已经准备好的模型和当前测试图片运行完整压力矩阵：

```bash
uv run --extra cuda python scripts/stress_qwen35.py \
  --image /absolute/path/document.jpg \
  --host 10.255.255.254
```

脚本生成 tokenizer 精确计数的 4K/8K 合成文本。2026-09-04 的完整矩阵实测结果为：

- 16K context 下，完整 5376-token prompt 实际生成 2K、4K 和 8K tokens，全部通过；
- 16K context 下，9472-token prompt 在预留 8K 输出时被正确拒绝；
- 17728 context 下，9472-token prompt 实际生成 2K、4K 和 8K tokens，全部通过。

最后一项的实际总长度为 17664，只剩 64 tokens，输出末段 KV 使用率达到 100%，因此
仅是已验证容量上限。生产仍推荐 16K context、8K 最大输出和 640 MiB KV cache，不应
自动切换到 17728。

长输出请求通过 `ignore_eos=true` 强制生成到上限，只用于容量和稳定性测试，不用于
评价输出质量；正常业务请求必须允许模型输出 EOS。完整矩阵本次耗时约 39 分钟，结果
会在每个阶段结束后立即写入 `.cache/qwen35-stress/`；边界失败不会覆盖已经完成的阶段。
可以用 `--case 4k-default`、`--case 8k-rejection` 或 `--case 8k-boundary` 单独重跑。
完整矩阵中若 `4k-default` 未通过，脚本会跳过更激进的 `8k-boundary`；显式选择边界
用例时则允许独立执行。

生产请求建议为模板和工具定义保留至少 512 tokens，并动态限制输出：

```text
allowed_output = min(8192, max_model_len - prompt_tokens - 512)
```

## WSL 兼容和资源覆盖

WSL2 会自动为 Worker 子进程启用 `VLLM_WSL2_ENABLE_PIN_MEMORY=1`，不会覆盖用户已经
设置的值。若 mirrored networking 导致 `127.0.0.1` 没有走标准 loopback，可显式
使用当前机器已验证可达的地址：

```bash
uv run --extra cuda python scripts/smoke_qwen35.py \
  --image /absolute/path/document.jpg \
  --host 10.255.255.254
```

Paddle smoke 同样支持 `--host 10.255.255.254`。

当前 CUDA 13 环境中的 FlashInfer sampler JIT 与随包提供的 CUB 不兼容，Worker 默认
设置 `VLLM_USE_FLASHINFER_SAMPLER=0`，回退到 vLLM 的 PyTorch sampler。该值不会
覆盖用户显式配置。

两个 smoke 脚本都允许覆盖主要资源参数。`--kv-cache-memory-bytes` 与
`--gpu-memory-utilization` 互斥；选择比例模式时会关闭默认固定 KV cache。prepare
允许联网，inspect 和 smoke 只读取已经缓存的本地快照，不会隐式下载。

详细说明见 [PaddleOCR-VL-1.6 输入 Token 与调优](../docs/model_paddle_ocr_v_1_6.md)
、[Qwen3.5-9B FP8 输入 Token 与独立 Worker](../docs/model_qwen_3_5_9b.md)和
[Qwen3.5-9B FP8 参数实测与推荐](../docs/model_qwen_3_5_9b_parameters.md)。
