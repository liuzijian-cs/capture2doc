# Qwen3.5-9B FP8 参数实测与推荐

> 状态：RTX 4070 Ti Super 16GB 单图冒烟测试已通过
> 测试日期：2026-09-04
> 环境：WSL2、vLLM 0.28.0、PyTorch 2.13.0+cu130
> 模型：`Qwen/Qwen3.5-9B@master`

## 当前最优推荐

在当前 16GB NVIDIA 单并发、单图工作负载下，推荐使用：

| 参数 | 推荐值 |
|---|---:|
| `dtype` | `bfloat16` |
| `quantization` | `fp8_per_channel` |
| `max_pixels` | 1310720 |
| 最大 image tokens | 约 1280 |
| `max_model_len` | 16384 |
| `max_tokens` | 8192 |
| `max_num_batched_tokens` | 4096 |
| `max_num_seqs` | 1 |
| `kv_cache_memory_bytes` | 671088640（640 MiB） |
| thinking | 关闭 |
| prefix caching | 关闭 |
| MM processor cache | 0 GiB |

对应测试命令：

```bash
uv run --extra cuda python scripts/smoke_qwen35.py \
  --image /absolute/path/document.jpg \
  --host 10.255.255.254 \
  --max-model-len 16384 \
  --kv-cache-memory-bytes 671088640
```

640 MiB KV cache 实测可容纳 17788 tokens，高于 16384 的最大上下文，vLLM
报告最大并发为 `1.09x`。这是当前硬件和单序列配置下已经验证的较紧凑值；代码默认值
目前仍是 1 GiB，使用推荐值时需要通过 CLI 显式覆盖。

## 测试输入与 Token

三轮测试使用同一张 `4000×3000` 文档图片和同一提示词。processor 将图片缩放并
对齐到 `1312×960`：

```text
image_grid_thw = [1, 60, 82]
image_tokens = 1 × 60 × 82 / 2² = 1230
模板及文字 tokens = 43
完整 prompt tokens = 1273
```

在推荐的 16K context 和 8K 输出预留下，prompt 上限是 8192 tokens。这张图片及
当前提示词使用 1273 tokens，还能增加约 6919 个输入 tokens，因此能够覆盖计划中的
约 4K OCR 文本输入。

## 三轮结果

| 配置 | KV 容量 | 最大并发 | 加载时间 | 推理时间 | 输出 tokens |
|---|---:|---:|---:|---:|---:|
| 16K + 1 GiB KV | 29023 | `1.77x` | 136.097 s | 24.522 s | 834 |
| 12K + 512 MiB KV | 14108 | `1.15x` | 83.076 s | 16.455 s | 846 |
| **16K + 640 MiB KV** | **17788** | **`1.09x`** | **43.024 s** | **17.682 s** | **834** |

加载时间随测试轮次下降，主要受 vLLM、Torch 编译缓存和系统缓存影响，不能直接归因于
context 或 KV cache 变小。推理时间也只代表当前单张图片，不是正式性能 Benchmark。

### 显存记录

| 配置 | 启动前 | 模型加载后 | 推理峰值 | 请求结束 | Worker 退出后 |
|---|---:|---:|---:|---:|---:|
| 16K + 1 GiB KV | 2474 MiB | 14963 MiB | 15903 MiB | 14963 MiB | 1357 MiB |
| 12K + 512 MiB KV | 1612 MiB | 14407 MiB | 15955 MiB | 14601 MiB | 1379 MiB |
| **16K + 640 MiB KV** | **1299 MiB** | **14369 MiB** | **15933 MiB** | **14625 MiB** | **1276 MiB** |

WSL/Windows 的基础显存会动态变化，因此不同轮次的启动前数值不能作为固定基线。三轮
推理峰值均在 `15903–15955 MiB`，差异只有 52 MiB，说明峰值主要来自模型权重、视觉
编码、临时张量、CUDA workspace 和 allocator；缩小 KV cache 主要降低模型加载后的
常驻显存，并未明显降低推理峰值。

推荐配置的峰值为 15933 MiB，在 16376 MiB 总显存上只剩约 443 MiB。它适合
Qwen3.5-9B 独立运行，不适合与 PaddleOCR-VL 同时常驻。当前服务端应继续采用顺序
加载、推理和卸载模型的策略。

## 配置取舍

### 为什么保留 16K

12K context 配合 8K 最大输出时，完整 prompt 最多只能使用 4096 tokens。单张图片
已经使用 1230 image tokens，再加入约 4K OCR 文本和模板后会超出预算，因此不能满足
Capture2Doc 的目标输入。

### 为什么选择 640 MiB KV cache

- 1 GiB 可容纳 29023 tokens，对单个 16K 序列明显过量。
- 640 MiB 可容纳 17788 tokens，已经通过 16K Worker 启动和单图推理。
- 512 MiB 只容纳 14108 tokens，低于 16K 最大上下文；本次只能搭配 12K context
  使用，不能替代推荐配置。
- 640 MiB 只保留约 `1.09x` 容量余量，不应提高 `max_num_seqs`，也不应未经测试继续
  增大 context。

## 当前结论与后续验证

- `fp8_per_channel` 已由 vLLM 选择 Cutlass FP8 kernel，模型能够返回非空图片响应。
- thinking 已关闭，三轮 `reasoning_tokens` 均为 0。
- Worker 退出后显存正常回落，没有发现残留 vLLM 进程造成的显存占用。
- 当前结果证明配置可以运行，不代表已经完成 OCR 准确率、C2D-XML 合法率或长输出
  稳定性评测。
- 后续需要使用真实的约 4K 文本输入、接近 8K 输出、多种文档图片和重复运行测试
  640 MiB 配置，并验证 PaddleOCR-VL 与 Qwen 的顺序切换。

## 长上下文与长输出压力测试

> 状态：测试工具已实现，尚未在推理机执行；本节不包含预设的实测结论。

压力测试中的 4K 和 8K 都指 Qwen tokenizer 计算后的文本 payload tokens，不是字符
数。脚本先用本地 processor 生成并复核精确长度的合成文本，再将图片、固定指令和
payload 一起通过官方 chat template 计算完整 prompt。

完整测试命令：

```bash
uv run --extra cuda python scripts/stress_qwen35.py \
  --image tests/test_image_1_larkdoc.jpg \
  --host 10.255.255.254
```

测试矩阵如下：

| 用例 | payload | context | KV cache | 强制输出阶段 | 预期 |
|---|---:|---:|---:|---:|---|
| `4k-default` | 4096 | 16384 | 640 MiB | 2048、4096、8192 | 三阶段均应成功 |
| `8k-rejection` | 8192 | 16384 | 640 MiB | 预留 8192 | 加载模型前被预检拒绝 |
| `8k-boundary` | 8192 | 17728 | 640 MiB | 2048、4096、8192 | 非生产极限实验，允许 OOM |

单图加 4K 文本和 8K 输出预计约为：

```text
1230 image + 4096 payload + 固定模板/指令 + 8192 output < 16384
```

单图加 8K 文本和 8K 输出无法放入 16K。边界用例把 context 临时提高到 17728，完整
prompt 必须不超过 `17728 - 8192 = 9536 tokens`，并且 Worker 启动日志报告的 KV
容量必须至少为 17728 tokens，否则跳过推理。该配置只探索此前 17788-token KV 容量
附近的边界，不替代顶部的 16K 推荐配置。

为了实际覆盖 decode 阶段，而不是只设置 `max_tokens`，压力请求使用
`ignore_eos=true`，依次强制生成 2K、4K 和 8K tokens。它可能产生无意义内容，因此
只能用于显存、吞吐和稳定性测试。每一级成功后才继续下一级，并立即保存 response、
实际 usage、耗时、tokens/s 和阶段显存；后续 OOM 或 Worker 退出不会丢失此前结果。

结果保存在以下目录，不提交 Git：

```text
server/.cache/qwen35-stress/<timestamp>/
├── suite-summary.json
├── generated-inputs/
├── 4k-default/
├── 8k-rejection/
├── 8k-boundary/
└── vllm.log
```

可以通过 `--case 4k-default`、`--case 8k-rejection` 或
`--case 8k-boundary` 单独重跑某项。边界用例成功只能证明当前图片和当前环境下可运行；
OOM、容量不足或 Worker 崩溃也会作为有效边界数据记录，不能据此自动扩大生产 context。
完整矩阵只有在 `4k-default` 三阶段全部通过后才运行 `8k-boundary`；显式指定边界用例
时允许独立执行。

原始测试产物位于推理机的以下本地目录，不提交 Git：

```text
server/.cache/qwen35-smoke/20260904T040617Z  # 16K + 1 GiB
server/.cache/qwen35-smoke/20260904T044311Z  # 12K + 512 MiB
server/.cache/qwen35-smoke/20260904T045400Z  # 16K + 640 MiB
```
