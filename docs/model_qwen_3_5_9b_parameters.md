# Qwen3.5-9B FP8 参数实测与推荐

> 状态：RTX 4070 Ti Super 16GB 单图冒烟和长上下文压力测试已通过
> 测试日期：2026-09-04
> 环境：WSL2、vLLM 0.28.0、PyTorch 2.13.0+cu130
> 模型：`Qwen/Qwen3.5-9B@master`

本页保留 2026-09-04 的独立 Worker 容量实验数据。2026-09-05 的 [扫描 Pipeline](capture_pipeline.md) 调整了 OCR 与最终组装的触发关系，没有重跑或改变本页实验：页面可提前入 OCR 队列，VLM 仍须等最终输入及有效结果齐备；本页不证明双模型并发、业务任务恢复或 Android 端到端联通。

## 当前生产推荐

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

这组参数不是硬件能运行的最大 context，而是为输入变化保留余量的生产推荐。正常请求
允许模型输出 EOS，不应使用压力测试中的 `ignore_eos=true` 强制生成到上限。

## 已验证容量上限

同一张测试图片已经在以下边界配置下完成强制 8192-token 输出：

| 参数 | 已验证值 |
|---|---:|
| `max_model_len` | 17728 |
| 完整 prompt | 9472 tokens |
| 实际 output | 8192 tokens |
| 实际总长度 | 17664 tokens |
| context 剩余 | 64 tokens |
| `kv_cache_memory_bytes` | 671088640（640 MiB） |
| Worker 报告 KV 容量 | 18207 tokens |
| 输出末段 KV 使用率 | 100% |

该配置证明 16GB 显卡能在当前输入上完成 `9472 + 8192`，但 64-token context 余量无法
容纳真实任务提示词、工具定义或输入波动。它只作为已验证容量上限，不作为服务端默认值。

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
- 后续需要使用真实 OCR 文本和 C2D-XML 输出评估质量，并重复验证 PaddleOCR-VL 与
  Qwen 的顺序切换和进程清理。

## 长上下文与长输出压力测试

> 状态：2026-09-04 已在推理机完成
> 产物：`server/.cache/qwen35-stress/20260904T100005Z`

压力测试中的 4K 和 8K 都指 Qwen tokenizer 计算后的文本 payload tokens，不是字符
数。脚本先用本地 processor 生成并复核精确长度的合成文本，再将图片、固定指令和
payload 一起通过官方 chat template 计算完整 prompt。

完整测试命令：

```bash
uv run --extra cuda python scripts/stress_qwen35.py \
  --image tests/test_image_1_larkdoc.jpg \
  --host 10.255.255.254
```

输入 token 实测如下：

| Payload | Image tokens | 非图像 tokens | 完整 prompt | 16K 下预留 8K 输出 |
|---:|---:|---:|---:|---|
| 4096 | 1230 | 4146 | 5376 | 可以 |
| 8192 | 1230 | 8242 | 9472 | 不可以，预检正确拒绝 |

强制输出结果如下，所有完成项的 `finish_reason` 都是 `length`：

| 用例 | Context | Prompt | Output | 推理时间 | 速度 | 结果 |
|---|---:|---:|---:|---:|---:|---|
| `4k-default` | 16384 | 5376 | 2048 | 160.573 s | 12.754 tok/s | 通过 |
| `4k-default` | 16384 | 5376 | 4096 | 317.497 s | 12.901 tok/s | 通过 |
| `4k-default` | 16384 | 5376 | 8192 | 631.769 s | 12.967 tok/s | 通过 |
| `8k-rejection` | 16384 | 9472 | 预留 8192 | - | - | 预检拒绝，符合预期 |
| `8k-boundary` | 17728 | 9472 | 2048 | 161.929 s | 12.648 tok/s | 通过 |
| `8k-boundary` | 17728 | 9472 | 4096 | 319.611 s | 12.816 tok/s | 通过 |
| `8k-boundary` | 17728 | 9472 | 8192 | 633.904 s | 12.923 tok/s | 通过 |

完整矩阵耗时约 39 分 14 秒，没有发生 OOM。两次 Worker 的资源记录为：

| 用例 | 加载时间 | KV 容量 | 启动前 | 模型加载后 | 全程峰值 | Worker 退出后 |
|---|---:|---:|---:|---:|---:|---:|
| `4k-default` | 61.032 s | 17788 | 1284 MiB | 14288 MiB | 15921 MiB | 1068 MiB |
| `8k-boundary` | 66.031 s | 18207 | 1068 MiB | 14256 MiB | 15904 MiB | 1068 MiB |

测试完成后的独立 `nvidia-smi` 观察为 859 MiB、GPU 利用率 0%，没有残留测试、vLLM
或 EngineCore 进程。退出日志出现一次 vLLM 强制清理 EngineCore 和一个
`leaked semaphore` warning；本次资源已经回收，但后续仍需通过重复启停测试判断它是否
只是退出路径噪声。

为了实际覆盖 decode 阶段，而不是只设置 `max_tokens`，压力请求使用
`ignore_eos=true`，依次强制生成 2K、4K 和 8K tokens。它可能产生无意义内容，因此
只能用于显存、吞吐和稳定性测试，不能据此评价模型回答或 C2D-XML 质量。

生产请求应继续执行 token 预检，并为模板或工具定义保留至少 512 tokens：

```text
allowed_output = min(8192, max_model_len - prompt_tokens - 512)
```

当前 5376-token prompt 仍可保留 8192 输出；当前 9472-token prompt 在 16K 下最多约
6400 tokens，建议取整为 6144。若 8K 输入还必须保留 8K 输出，应拆分输入或分阶段
合成，而不是自动切换到 17728 边界配置。

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
`--case 8k-boundary` 单独重跑某项。完整矩阵只有在 `4k-default` 三阶段全部通过后才
运行 `8k-boundary`；显式指定边界用例时允许独立执行。

原始测试产物位于推理机的以下本地目录，不提交 Git：

```text
server/.cache/qwen35-smoke/20260904T040617Z  # 16K + 1 GiB
server/.cache/qwen35-smoke/20260904T044311Z  # 12K + 512 MiB
server/.cache/qwen35-smoke/20260904T045400Z  # 16K + 640 MiB
server/.cache/qwen35-stress/20260904T100005Z # 长上下文与强制长输出矩阵
```
