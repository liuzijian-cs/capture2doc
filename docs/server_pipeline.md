# 手机图片到文档 JSON：本地 CLI V2

> 2026-09-05：当前版本冻结为 [pipeline-cli-v0.1.0 首版基线](server_pipeline_baseline_v0_1.md)。本文是运行与协议参考，整体流程见基线文档，真实八图结果见 [五轮实测报告](server_pipeline_evaluation_20260905.md)。本文末尾的单图数据仅为旧 V1 历史证据；语法通过不代表内容或样式质量达标。

## 输入和运行

以 `document_id` 归属不可变的 `image_id`，没有新增服务端 `page_id`。当前 Android 已移除重拍；删除后重新拍摄会创建新的图片身份。用户最终确认的 `ordered_image_ids` 决定请求及提交顺序；一张图片是一批输入，一批可生成多个语义 block。Android 已实现离线任务首页、HTTP 适配器和 WorkManager；其 pageId/pageIds 拟映射不可变 image_id/ordered_image_ids，见 [跨端协议提案](android_task_protocol.md)。服务端 HTTP 接收和跨文档实时队列仍未接入，CLI 不代表端到端上传已联通。

在 NVIDIA/WSL 的 `server/` 目录执行。两个模型必须已准备成本地快照；CLI 不下载模型。

```json
{
  "document_id": "demo-001",
  "lang": "zh-CN",
  "images": [
    {"image_id": "image-1", "path": "photos/1.jpg"},
    {"image_id": "image-2", "path": "photos/2.jpg"}
  ],
  "ordered_image_ids": ["image-1", "image-2"]
}
```

图片路径相对于清单目录。最终列表非空、唯一且引用已提供图片；未被引用图片不执行 OCR。ID 长度 1–128，首字符是 ASCII 字母或数字，其余允许字母、数字、`_`、`.`、`-`。

```bash
uv run --extra cuda python scripts/run_document.py \
  --manifest /absolute/path/input.json \
  --output-dir .cache/documents/demo-001 \
  --host 10.255.255.254

uv run --extra cuda python scripts/run_document.py \
  --resume --output-dir .cache/documents/demo-001 \
  --host 10.255.255.254
```

`10.255.255.254` 是当前 WSL 已验证地址，其他部署按实际地址配置。恢复必须保持原模型、提示词、主机参数、输入身份和顺序。新目录默认检查点 `schema_version=2`；旧 V1 检查点继续调用原 V1 runner，保留其历史行为。V2 不允许 `--retry-failed` 重置预算。

保留旧失败基线，在**新目录**复用成功 OCR：

```bash
uv run --extra cuda python scripts/run_document.py \
  --manifest /absolute/path/input.json \
  --output-dir .cache/documents/demo-v2 \
  --reuse-ocr-from .cache/documents/demo-v1/result \
  --host 10.255.255.254
```

导入检查原图 SHA-256、旋正 RGB PNG 身份、Paddle 设置、快照元数据指纹和原始响应内容；只导入正常 `stop` 的 OCR，记录原检查点哈希和耗时。Qwen/提示词可与旧实验不同。不会改写旧目录或复制其修复预算。原始字节保留，两个模型共用 EXIF 已旋正、未二次缩小的 RGB PNG。

退出码：0 完成；3 完成但待复核（含兜底/缺失）；1 基础设施或配置错误；2 参数错误；130 中断。`processing_status=completed` 表示所有图片已处理，不表示每块都由 VLM 成功生成。

## JSON 与 block 契约

主要产物为 `document.json`：顶层 `doc`，其中 `blocks[]` 的顺序决定文档顺序，输出 `block_id` 从 0 编号。内部 ID 和版本用于修复，不能用显示编号定位运行中的可变对象。

```json
{
  "doc": {
    "document_id": "demo-001",
    "processing_status": "completed",
    "needs_review": true,
    "semantic_fidelity_verified": false,
    "xml_status": "complete",
    "blocks": [
      {
        "block_id": 0,
        "status": "fallback",
        "vlm_validation": "failed",
        "final_validation": "passed",
        "text": "OCR 原文",
        "xml": "<p xmlns=\"urn:capture2doc:c2d:1\">OCR 原文</p>",
        "fallback_source": "ocr",
        "errors": [],
        "repair_attempts": 5
      }
    ]
  }
}
```

实际失败记录包含错误码、目标块、可取得的行列/XPath、实际与允许结构、修复说明、经过真实校验器验证的正确示例。原失败记录不会被兜底成功抹去；已修好的约束另存 `guards`，当前错误存 `current_errors`。上例为字段示意，实际兜底必有失败记录。

| status | 含义 | xml/text | final_validation |
|---|---|---|---|
| ok | 模型候选经局部及组合检查通过 | 完整内容 | passed |
| fallback | 程序采用 OCR 或独立 VLM 文字生成段落 | 完整降级内容 | passed |
| unresolved | 无法可靠取得该位置的文字 | null | failed |

XML 字符串使用 JSON 库无损序列化。兜底段落用 XML 库构建，特殊字符自动转义，换行使用 `br`；不会靠剥除损坏 XML 标签取得文字。状态、错误、来源和图片身份均不进入公开 C2D-XML。

有缺失记录时只输出 `document.partial.c2d.xml`，JSON 保留缺失位置；没有任何可用块则不生成空的非法 XML，`xml_status=unavailable`。处理中导出的 XML 也明确标为 partial。没有缺失时输出 `document.c2d.xml`；fallback 仍要求人工复核。

## 主上下文与工具协议

图片决定有效正文、阅读顺序和样式，OCR 只作参考。不按主题删段或总结；保留有效标题、正文、编号、注释、页眉页脚、代码、公式及表格，只去掉明确在文档之外的桌面、工具栏和物理环境文字。粗体和受限颜色依据图片恢复。

主上下文含完整 C2D 契约、已校验结构与样式 few-shot、完整当前图、OCR/来源片段、最近 1–3 个完整历史块及可选旧尾块。模型分别返回每块 `xml`、独立 `text` 和 `ocr_refs`。OCR 片段按原始换行分配 `image_id:ocr:行号` 及字符区间，仅定位来源，不决定 block 粒度。

本轮实现应用层 JSON action 工具协议，复用已验证的 Qwen 普通 chat 模板，不引入原生 function-calling parser 或额外模型实例。请求使用 `response_format=json_schema` 约束外层 action/blocks/字段类型，XML 字符串内部仍交给独立校验器及修复队列。接口依据 [vLLM Structured Outputs](https://docs.vllm.ai/en/stable/features/structured_outputs/)，检查点绑定 schema 哈希：

- `submit`：主生成提交新增 blocks 及可选完整尾块；修复只能替换请求指定的目标组。
- `read_blocks`：按历史显示编号读取最多 3 个完整已提交块。
- `search_blocks`：短关键词检索，返回最多 3 个完整匹配块。

每个生成/修复任务最多 3 次只读工具调用，工具请求和结果均持久化；随后必须提交结果。历史工具不能修改任何块。主生成只可改变旧尾块和新增内容；修复携带目标版本/尝试 ID，只能更改当前任务范围。

跨图重叠声明为默认关闭的实验能力：第 4 轮真实输出复制历史并截断，尚未满足默认启用标准。默认主生成保留最近历史与只读工具，不发送主动检索窗口或重叠声明协议。实验开启时，另检索一个较早图片的连续完整历史窗口，并纳入实际 token 预检。模型仍须返回当前图片的全部可见块；只有声明 `overlap_claim`，且服务端确认历史引用确已发送、版本及内容未变、当前前缀与连续历史的文字及有意义结构匹配时，才省略已覆盖的前缀。相似度只用于检索，不直接授权删除。数字、代码空白、链接目标、列表与表格结构冲突时保留全文。短标题或少量裁切文字不足以证明重叠；尾块续接与前缀去重互斥。

被省略的完整候选仍保存在草稿，JSON 的 `doc.stitching` 记录证据及待复核状态；公开 XML 不增加图片或历史标识。该验证证明文本覆盖，不等同于独立确认两张图来自同一源区域，因此成功去重也标记待复核。整图均已覆盖时允许原子提交零新增块，继续下一张。

修复额外提供目标完整 XML、独立文字、相关邻居、当前错误与已修好约束；不会重新生成其他正确块。允许在目标范围内拆分和合并，服务端用连续数组替换保持顺序。旧尾块必须在第一个替换块中保留全部旧文字，代码空白也须保留；否则回退原尾块。

每次请求用实际 Qwen processor/tokenizer 计算 system、图片、JSON（含工具结果）的完整 token 数：

```text
allowed_output = min(8192, 16384 - actual_prompt_tokens - 512)
```

没有沿用旧的 1536 token 尾块硬上限。预算不足先减少可选历史至一块，不截断尾块、目标 XML、OCR 或图片；仍无法容纳至少 512 输出 tokens 时让该任务进入局部兜底。此实现不承诺无限上下文或无限输出；`finish_reason=length` 的响应即使包含闭合 XML/JSON 也不视为完成。

## 草稿、修复与局部兜底

每张图先生成独立草稿，逐块 XML/XSD/语义检查，再用最终 document 规则检查组合（例如 title 只能在文档开头）。正确块保留；独立失败块分别排队，共同约束涉及的连续块组成关联任务。一个 Qwen 串行取任务，不并发推理。

初次生成后最多 5 轮，只处理仍失败的任务。每个初始块/关联组最多 5 次修复；拆分后的子块共享祖先预算，合并继承预算，中断请求也消耗该次预算。多个独立失败块的总调用数可超过 5。结果携带版本与尝试 ID，拒绝过期、重复和越界结果；同一已收到响应可从检查点继续应用，不必再次调用模型。

对已观测的窄结构错误——无属性、无额外正文的 `blockquote` 只包住一个 `pre`——程序可生成并校验一个文字、代码空白和 OCR 引用不变的修复选项。存在安全选项时，模型只返回 `apply_repair_option` 或 `decline_repair_option`，不能顺带重写邻居；应用前重新验证已发送选项、目标版本和内容摘要，再经过原有修复事务。拒绝或中断仍消耗原预算。无可验证选项时继续普通 `submit` 修复，不能把这项改进解释为所有结构错误都已自动解决。

耗尽后由程序选择：

1. 引用存在于当前图 OCR、且未被其他候选或历史块共享的 OCR 原文；按服务端来源顺序拼接。
2. 该块独立保存的 VLM 纯文本，标记 `fallback_source=vlm_text`。
3. `unresolved` 缺失记录，不编造正文。

**OCR 关联的限制：** `ocr_refs` 由 Qwen 根据带 ID 的原始文本行提出；服务端只核验引用存在与占用冲突，尚无独立语义/坐标对齐来证明引用指向正确内容。原始行不是 block，粗粒度一行可能包含多个块；多个候选共享一行时均不能用它执行 OCR 兜底。错误但唯一的引用仍可能通过，因此不能把来源检查称作语义对应已验证。分段示例和真实 Block 23 的来源解释见 [基线的 OCR 章节](server_pipeline_baseline_v0_1.md#7-ocr-如何对应-block以及兜底的真实保证)。

所有兜底重新校验并待复核。不会把整张 OCR 放入多个失败块；只有整个候选列表没有生成时，允许整图 OCR 降级一次。旧尾块失败则恢复原尾块，只降级当前图片的新文字；无法分离时留下缺失位置。

该图全部任务进入终态后，候选草稿作为证据保存，正式 blocks、图片游标及提交日志写入同一个原子 `state.json`。后续图片继续处理。导出 JSON/XML/报告是该检查点的可重建投影，导出中断可以恢复；不覆盖被外部修改的结果文件。

OCR 截断或空响应明确记录 `complete=false`，保留原始响应，图片仍可供 Qwen 识别；不把非空文本当成完整 OCR。OCR 与输出字符对齐仅作诊断，差异较大可标记待复核，**不会再触发硬性覆盖率修复**。无法自动检测两模型共同漏字，也不能以 XML 校验保证语义保真。

存储损坏、原图身份冲突、模型/提示词漂移和 GPU 清理失败仍停止运行。检查点内容有完整性哈希，原图、OCR 原始响应和已提交草稿也校验身份/哈希。

## 参数、模型装卸与提示词

| 参数 | Paddle | Qwen |
|---|---:|---:|
| max_num_seqs / 图片数每请求 | 1 / 1 | 1 / 1 |
| context / 最大输出 | 8192 / 4096 | 16384 / 8192 |
| KV cache | 256 MiB | 640 MiB |
| max_num_batched_tokens | 4096 | 4096 |
| max_pixels | 1003520 | 1310720 |
| 精度 | BF16 | fp8_per_channel 在线量化 |
| thinking | 不适用 | 关闭 |

保持既有参数；不改 0.95 比例，不启动两个模型副本。Paddle 先完成待处理 OCR，卸载并检查所属进程组退出、显存回到基线加 128 MiB 内，再加载 Qwen。Qwen 完成所有图片与修复后卸载。清理超时阻止后续加载；不自动杀死可能复用 PID 的旧进程组。SDK 隐式重试关闭，请求超时 1200 秒。

文档锁防止同目录双写，GPU 文件锁约束协作 CLI；不约束手动启动的 Worker。SIGTERM/Ctrl-C 可走清理路径；SIGKILL/断电后的资源回收仍需操作系统托管。

UTF-8 TXT + Git 管理提示词，通过 `importlib.resources` 随包加载。V2 使用 `c2d_blocks_v2.txt`（任务及工具协议）和 `c2d_contract_v2.txt`（完整格式规则）；`blocks.py` 保存已校验 few-shot，并随动态请求提供。运行绑定提示词、样例和模型配置哈希，保存完整请求及原始响应。`c2d_system.txt` 仅供 V1 恢复。

`c2d_style_examples_v2.json` 保存可校验的扩展样式示例；第 3 轮实测出现转录回退，默认不追加这份扩展资源，保留显式实验入口。完整契约及动态请求中的基础结构、样式示例仍然提供。候选中的空 `href` 会报 `LINK_TARGET_EMPTY` 进入局部修复；未知目标应去掉链接外壳，保留文字及有依据的样式，不编造 URL。

## 产物与自动化

```text
output-dir/
  state.json                 # V2 权威原子检查点，含当前草稿、预算和提交顺序
  document.json              # 主要结果，doc.blocks[]
  document.c2d.xml            # 完成且无缺失的位置时生成
  document.partial.c2d.xml    # 有缺失或处理中的可用 XML，和上项互斥
  blocks.md                  # Block 0 开始，完整文字/XML/状态/次数/来源/错误
  summary.json               # 状态计数、逐图诊断、运行与草稿证据索引
  images/ prepared/ ocr/     # 输入、两模型共用图片、原始 OCR
  requests/ responses/       # 完整 Qwen 输入、预算、原始响应及耗时
  drafts/                    # 每张图已终态的草稿、原候选、全部尝试
  runs/                      # 提示词快照、vLLM 日志、显存及装卸指标
```

本地测试以可控模型边界执行真实 XML 校验、存储和调度；覆盖丰富 XML/中文/转义无损 JSON、多块隔离、五次预算、拆分合并、OCR 冲突、纯文本及缺失兜底、尾块恢复、过期/重复响应、中断和导出恢复、上下文耗尽、只读历史、GPU 清理门禁及旧 OCR 身份校验。这些测试不代替真实模型和视觉质量验收。

## 历史 V1 单图验证

下文为旧 V1 运行的原始结果，参数和指标不能视为 V2 的测试结果。旧八图失败基线也保留，V2 使用独立目录。

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
