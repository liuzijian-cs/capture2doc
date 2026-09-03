# Capture2Doc Server

Capture2Doc 的本地推理服务。本阶段只实现 NVIDIA/WSL 上独立运行的
PaddleOCR-VL-1.6 Worker：ModelScope 负责准备模型，vLLM 只加载本地快照，
暂不包含完整 PaddleOCR pipeline、HTTP 业务 API、Agent 或 C2D-XML 映射。

## 环境准备

普通 `uv sync` 不会安装 CUDA 依赖。NVIDIA/WSL 推理机使用：

```bash
uv sync --extra cuda
```

模型缓存默认位于 `~/models/modelscope`，开发时无需额外配置环境变量。需要临时
切换缓存目录时可传入 `--cache-dir`；它的优先级高于 `MODELSCOPE_CACHE`。
缓存目录不应放入 Git 仓库。

## 准备模型

准备阶段允许 ModelScope 联网下载官方 BF16 权重，并打印最终快照路径：

```bash
uv run --extra cuda python scripts/prepare_paddleocr_vl.py
```

模型固定为 `PaddlePaddle/PaddleOCR-VL-1.6`，初始 revision 为 `master`。

## 单图冒烟测试

```bash
uv run --extra cuda python scripts/smoke_paddleocr_vl.py \
  --image /absolute/path/document.jpg
```

默认推理参数为 `max_pixels=1003520`（最多约 1280 image tokens）、
`max_tokens=4096`、`max_model_len=8192`、`max_num_batched_tokens=4096`、
`max_num_seqs=1` 和固定 `256 MiB` KV cache。固定 KV cache 模式不会同时传递
`gpu_memory_utilization`。

WSL2 会自动为 Worker 子进程启用 `VLLM_WSL2_ENABLE_PIN_MEMORY=1`，不会覆盖用户已经
设置的值。若 mirrored networking 导致 `127.0.0.1` 没有走标准 loopback，可显式指定
已经验证可达的本机地址：

```bash
uv run --extra cuda python scripts/smoke_paddleocr_vl.py \
  --image /absolute/path/document.jpg \
  --host 10.255.255.254
```

当前 CUDA 13 环境中的 FlashInfer sampler JIT 与随包提供的 CUB 不兼容，Worker 默认
设置 `VLLM_USE_FLASHINFER_SAMPLER=0`，回退到 vLLM 的 PyTorch sampler。该默认值同样
不会覆盖用户显式设置，后续依赖兼容后可以单独重新验证 FlashInfer。

所有主要资源参数都可临时覆盖。`--kv-cache-memory-bytes` 与
`--gpu-memory-utilization` 互斥；选择比例模式时会关闭默认固定 KV cache。例如：

```bash
uv run --extra cuda python scripts/smoke_paddleocr_vl.py \
  --image /absolute/path/document.jpg \
  --gpu-memory-utilization 0.2
```

测试只读取已经缓存的本地快照，不会隐式下载。脚本会启动 vLLM、等待健康检查、
发送 `OCR:` 请求，将原始响应、计时摘要和 Worker 日志保存到
`.cache/paddleocr-vl-smoke/`，最后关闭 Worker。`summary.json` 会同时记录实际生效的
推理参数、响应 token usage 和 `finish_reason`。

参考：[ModelScope 模型卡](https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL-1.6)、
[vLLM PaddleOCR-VL recipe](https://github.com/vllm-project/recipes/blob/main/PaddlePaddle/PaddleOCR-VL.md)。
