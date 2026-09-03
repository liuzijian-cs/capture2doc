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

测试只读取已经缓存的本地快照，不会隐式下载。脚本会启动 vLLM、等待健康检查、
发送 `OCR:` 请求，将原始响应、计时摘要和 Worker 日志保存到
`.cache/paddleocr-vl-smoke/`，最后关闭 Worker。

参考：[ModelScope 模型卡](https://modelscope.cn/models/PaddlePaddle/PaddleOCR-VL-1.6)、
[vLLM PaddleOCR-VL recipe](https://github.com/vllm-project/recipes/blob/main/PaddlePaddle/PaddleOCR-VL.md)。
