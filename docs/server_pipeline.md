# 手机图片到 C2D-XML：本地 CLI

> 状态：首轮 CLI 已实现；单张真实文档照片的 NVIDIA 双模型闭环、完成后恢复及 SIGTERM 中断续跑已验证，内容与样式质量仍需优化。
>
> 日期：2026-09-05。当前范围是普通手机文档照片，不承诺复杂版面质量。

## 本轮确定的业务边界

服务端以 `document_id` 组织图片，只有不可变 `image_id`，不增加逻辑 `page_id` 或同 ID 图片修订。当前 Android 已移除重拍；删除旧照片后新增照片使用新身份，旧图片及其 OCR 仍属于旧身份；最终列表只引用保留的图片。排序仅改变最终列表，不重新 OCR。所有身份、路径和诊断数据都不进入 C2D-XML。

Android 允许离线先拍摄和本地完成；后台关联真实文档 ID 后，在 1280 JPEG 派生图就绪时逐张上传，接收成功立即排 Paddle OCR；Qwen 占用 GPU 时仍可接收图片，但 OCR 等待。用户确认后冻结文档的 `ordered_image_ids`；所引用图片 OCR 齐备后，程序严格按该列表串行调用 Qwen。程序保证请求和提交顺序，模型是否忠实遵循图片内部阅读顺序仍要验收。

历史 Android 重拍曾复用 `ScanPage.pageId`，本次合并的客户端已移除该功能，每份新增照片有独立 pageId。客户端提议将 pageId 映射为服务端不可变 image_id，不增加逻辑页或同 ID 修订；HTTP 的路由、字段与状态适配仍须双方评审，见 [协议提案](android_task_protocol.md)。本 CLI 不是 HTTP 接收服务，Android 客户端已实现不代表网络链路已接通。

CLI 使用清单模拟已接收、已最终确认的输入：先导入图片、冻结列表，再完成所需 OCR 和 Qwen 组装。它不是持续监听的上传服务，不处理多个文档间的队列公平性。

## 运行

在 NVIDIA/WSL 的 `server/` 下，先按模型文档准备环境和两个本地模型快照。CLI 不下载模型，也不调用第三方推理 API。

创建 `input.json`（相对图片路径相对于清单目录）：

```json
{
  "document_id": "demo-001",
  "lang": "zh-CN",
  "images": [
    {"image_id": "image-a", "path": "photos/a.jpg"},
    {"image_id": "image-b", "path": "photos/b.jpg"}
  ],
  "ordered_image_ids": ["image-b", "image-a"]
}
```

最终列表必须非空、无重复，且全部引用清单内图片。`images` 的顺序不决定文档顺序；未被最终列表引用的图片不执行 OCR。ID 使用 1–128 位 ASCII 字母、数字、下划线、点或连字符，首字符为字母或数字。

```bash
uv run --extra cuda python scripts/run_document.py \
  --manifest /absolute/path/input.json \
  --output-dir .cache/documents/demo-001
```

当前推理机如需已验证的 WSL 地址，增加 `--host 10.255.255.254`；其他环境按实际可达地址指定。中断后不再依赖原始清单或清单外的照片：

```bash
uv run --extra cuda python scripts/run_document.py \
  --resume --output-dir .cache/documents/demo-001
```

若首次指定过 `--host` 或 `--cache-dir`，恢复时保持一致。达到重试上限后，先检查原始响应与诊断；显式增加 `--retry-failed` 才给未完成的 OCR/轮次追加最多三次尝试。它不会重新处理已完成 OCR 或已提交轮次。

原始上传字节保留并计算 SHA-256。服务端生成 EXIF 已旋正的 RGB PNG 作为两模型共同输入，不覆盖原图、不二次缩小像素尺寸。各模型 processor 仍使用自己的既定 max_pixels。恢复时检查原图及派生图身份，输入、顺序、提示词或模型配置改变时要求使用新目录。

## 参数与装卸

| 参数 | Paddle | Qwen |
|---|---:|---:|
| 输入图片数/请求 | 1 | 1 |
| max_num_seqs | 1 | 1 |
| max_model_len | 8192 | 16384 |
| 最大输出 | 4096 | 8192 |
| KV cache | 256 MiB | 640 MiB |
| max_num_batched_tokens | 4096 | 4096 |
| max_pixels | 1003520 | 1310720 |
| 量化 | BF16 | fp8_per_channel 在线量化 |
| thinking | 不适用 | 关闭 |

不改独立 smoke 的既有默认值。CLI 显式采用 Qwen 实测推荐的 640 MiB；不切换到 17728 极限上下文，不设置 0.95 比例，不创建第二份模型副本。请求允许正常 EOS，SDK 隐式重试关闭，长输出请求超时为 1200 秒。

单轮运行先加载 Paddle，顺序完成需要的 OCR 后停止并等待清理；再加载 Qwen，处理全部未完成窗口与纠错，停止后才发布最终文件。每次停止检查所属进程组及显存回落；30 秒内未回到启动前全局显存加 128 MiB 的范围就明确失败，不启动下一个模型。这一容差是资源门禁配置，不是对 WSL 背景显存波动的保证。

文档目录锁防止同一文档双写，共享 GPU 文件锁防止协作 CLI 同时加载模型。所有进程必须使用相同 `--gpu-lock`；默认位于系统临时目录。该锁不约束手动启动的 vLLM、其他用户或其他应用。Ctrl-C/SIGTERM 走清理路径；SIGKILL、系统断电无法保证 Python 清理执行，恢复前需确认没有残留 Worker。

阶段记录保存所属进程组和清理结果。恢复会检查尚未确认清理的旧阶段：进程组仍存在则拒绝继续，不自动向可能复用的旧 PID 发信号；进程组退出后重新检查显存。进程启动与记录落盘之间仍存在极短的异常窗口，因此该记录不替代操作系统级进程托管。

## 生成、检查点与恢复

每张图片可以对应多个顺序 OCR 文本窗口，一轮可以生成多个完整顶层 block。输入游标用内部字符区间记录；预算始终按真实 Qwen processor/tokenizer 计算，不用字符数代替 token 数。

每轮先计算完整 system、图片、OCR、历史和重试信息的输入 token 数，保留 512 token 余量：

```text
allowed_output = min(8192, 16384 - actual_prompt_tokens - 512)
```

常规历史沿用最多三块、768/1536 token 规则。尾块超过 1536 时，只要真实输入及完整回传输出预算允许，就单独携带完整尾块。预算不足时依次移除失败响应副本、只读历史，随后缩小当前 OCR 窗口；不截断或摘要尾块。完整尾块自身无法放入窗口时停止并保留检查点；首版没有任意长度 block 或表格行级 patch。

每轮先在候选组装器中执行更新包与完整文档校验，再检查内容。通过后，把轮次序号、image_id、输入区间、源文本哈希、旧文档哈希、完整更新包及其哈希写入同一个原子检查点。恢复从空组装器按顺序重放已提交日志，每个提交只重放一次；未提交的响应文件只作证据，不自动应用。

推理可能因中断而重新执行，但文档提交不会因此重复。不能把此语义理解为“模型请求恰好执行一次”。全部轮次完成后重新校验并原子写出 XML；已有不同内容的最终文件不覆盖。

| 情况 | 本轮行为 |
|---|---|
| Paddle `finish_reason=length` | 保留原始响应，标记 OCR_TRUNCATED 并停止；以单图 4K 内为首轮输入假设，不自动重复同一容量不足请求 |
| 空响应、异常停止、请求错误 | 保存可取得的响应/错误，有限重试，不把非空或 HTTP 成功视为完成 |
| Qwen 输出截断 | 即使已闭合 XML 也不提交；缩小 OCR 输入窗口并重新生成，不补闭合标签 |
| XML/Schema/业务规则错误 | 旧文档不变，携带错误和预算允许的上一响应重试，初始最多三次尝试 |
| 尾块丢字 | 保守拒绝；首轮允许结构调整、续接，但不允许删除或改写已有尾块文字，代码另保留精确空白 |
| 内容明显不匹配 | 单独对齐当前 OCR 窗口与去掉完整旧尾块后的新增输出；覆盖率或支持率低于 0.80 时拒绝并纠错，长历史不能掩盖新内容遗漏 |
| 内容中等差异 | 任一比例低于 0.95 时最终标记 needs_review；这两个阈值是未校准的启发式起点，不是质量成绩 |

字符对齐可发现部分遗漏、重复或无依据输出，但去页眉、公式写法、OCR 错字修正也可能触发差异；它无法发现所有语义错误，更无法检测两个模型共同漏掉的图片内容。状态 complete 只表示流程与既定检查通过，始终记录 `semantic_fidelity_verified=false`。真实照片需人工对照，不能声称 100% 内容忠实。

退出码：0 为流程完成；1 为错误；2 为参数错误；3 为 XML 已生成但存在内容复核标记；130 为中断。

## 提示词管理

完整模型侧 C2D 契约位于 `server/src/capture2doc/prompts/c2d_system.txt`，覆盖标签、属性、嵌套、七色、表格网格、转义、安全边界、尾块更新、纠错和合法样例。它是面向模型的完整生成规则，不复制 Renderer 平台映射说明或整个 XSD。规范依据仍是 `c2d_xml.md` 与随包 XSD。

使用 UTF-8 TXT + Git 审查/版本历史，通过 importlib.resources 加载，随 wheel 分发，不依赖工作目录。运行保存 prompt_id、完整 system 文本和 SHA-256，恢复拒绝混用提示词。动态 OCR/历史通过 JSON 序列化放进 user message，不在 system 文本中使用字符串模板替换；预检和请求共用消息构建器。

TXT 与 Markdown 都可用于提示词，扩展名不是工业质量保证。本项目选择 TXT 是因为内容直接作为 system 文本，不需要模板执行器；版本绑定、可复现请求、示例校验和真实样本评测才是维护重点。参考 [Promptfoo 文件提示词](https://www.promptfoo.dev/docs/configuration/prompts/) 与 [Langfuse 版本管理概念](https://langfuse.com/docs/prompt-management/data-model)，本轮不引入这些服务依赖。

修改 v0.1 规范时，应同步模型契约、示例与校验测试；提示词的 XML 样例会用真实校验器检查。提示词变更后的真实 tokenizer 长度和生成质量必须在推理机重新验证。

## 产物与验证边界

```text
output-dir/
  state.json                 # 图片、OCR、轮次日志、配置、状态和错误
  images/                    # 不可变原始图片
  prepared/                  # 两模型共用的旋正 RGB PNG
  ocr/                       # 各次 OCR 原始响应
  rounds/                    # 请求、响应、完整模板及校验/内容报告
  runs/                      # system 快照、vLLM 日志、加载/卸载/显存统计
  document.c2d.xml            # 全部轮次通过后生成
```

本地自动化使用可控模型边界，实际执行身份冻结、模型阶段编排、真实 XML 校验、原子落盘和日志恢复。覆盖乱序清单、删除图片、截断、有限纠错、中断、重复提交保护、配置漂移、超长尾块、内容诊断和锁互斥。

这些自动化测试不加载 GPU 模型。真实模型验收独立记录如下，不继承旧 smoke/压力测试的通过结论；多张真实照片跨轮续接、模型实际触发的纠错和截断处理仍需扩充样本验证。

本轮实际本地检查：服务端全量 **187 项测试通过**，相关 Python 文件 Ruff 检查通过；真实 CLI 子进程已验证无 CUDA 情况下恢复完成文档及 needs_review 退出码。另用 Pillow 构造 EXIF 旋转 JPEG，验证原图不变、两模型共用 RGB 像素、旋正尺寸及派生 PNG 可重复；已构建 wheel 并确认完整 TXT 提示词和 XSD 随包分发。这些结果不包含真实 OCR/Qwen 推理。

## 2026-09-05 WSL 真实模型验证

通过 `ssh wsl` 在 `~/workspace/project/capture2doc` 拉取 `main` 提交 `d881227`，在 `server/` 执行 `uv sync --locked --extra cuda`。环境为 RTX 4070 Ti SUPER（16376 MiB）、PyTorch 2.13.0+cu130、Transformers 5.16.1、vLLM 0.28.0、Pillow 12.3.0。远端服务端全量 **187 项测试通过**。

使用远端已有的 `tests/test_image_1_larkdoc.jpg`（4000×3000，手机拍摄屏幕上的文档），以 `--host 10.255.255.254` 执行单图清单。照片及运行产物不提交 Git。首轮结果如下，耗时和显存均为这一次运行的观测值，不是基准均值或服务承诺。

| 阶段 | 加载 | 请求 | 卸载 | prompt / output tokens | GPU 全局峰值 | 卸载后显存 |
|---|---:|---:|---:|---:|---:|---:|
| Paddle | 34.04 s | 2.78 s | 0.64 s | 1243 / 517 | 3846 MiB | 1054 MiB |
| Qwen | 63.04 s | 10.87 s | 0.56 s | 3711 / 550 | 15890 MiB | 892 MiB |

Paddle 正常 `stop` 后才卸载；进程组与显存检查通过后启动 Qwen。Qwen 输入含完整 TXT system、OCR、图片和动态 JSON，真实预检与服务返回均为 3711 prompt tokens，其中图片 1230 tokens；保留 8192 最大输出，实际正常 `stop`，reasoning tokens 为 0。第一轮即提交全部 851 个 OCR 字符，最终 XML 通过 XSD 与业务校验，CLI 退出 0。两模型清理均记录 `cleanup_verified=true`。Qwen 日志仍有 vLLM 的 semaphore 清理警告，但本次所属进程组已退出、显存已回落。

再次运行相同目录的 `--resume`，约 0.10 s 完成，退出 0；XML SHA-256、已提交轮次及模型运行次数均未变化，没有重新加载模型。

另建独立文档，在首个 Qwen 请求开始后约 1 s 向 CLI 注入 SIGTERM。CLI 退出 130，保存 `interrupted` 状态，已成功 OCR 保留，提交轮次为 0；所属 Qwen 进程组退出，显存回到 892 MiB。随后 `--resume` 退出 0 并生成合法 XML：总计 1 次 OCR、2 次 Qwen 尝试、1 次提交；恢复阶段只加载 Qwen，不重新加载 Paddle，OCR 记录保持一致。两次运行的全部模型阶段均通过清理门禁，最终显存仍为 892 MiB。故障注入及恢复合计约 148.80 s。这验证的是 SIGTERM 清理与未提交轮次续跑，不涵盖断电或 SIGKILL 的操作系统级回收。

字符对齐覆盖率为 0.997555、输出支持率为 1.0，状态 `complete`；这只说明与 OCR 的文本一致性。人工对照照片发现：标题、列表和正文段落形成了结构，但工具栏和头像被 OCR 误识别出的符号仍在 XML 中，粗体、高亮及链接语义未恢复。当前不把该样本视为排版质量达标，仍保留 `semantic_fidelity_verified=false`。

远端证据目录（相对于 `server/`）：`.cache/documents/wsl-pipeline-20260905/result/`，包含最终 `document.c2d.xml`、`state.json`、两模型原始响应、逐轮 token 预检及加载/卸载指标。

中断恢复证据位于 `.cache/documents/wsl-recovery-20260905/`：`interrupted.log`、`resumed.log`、`verification.json` 和 `result/` 中的两次运行检查点。验证摘要明确记录退出码、OCR 复用、尝试/提交数量、XML 校验及各阶段显存。
