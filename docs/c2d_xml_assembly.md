# C2D-XML 增量组装

> 状态：内存合并、上下文选择与离线文件导出已实现、已测试  
> 更新日期：2026-09-05

标签和校验规则见 [C2D-XML 标签体系](c2d_xml.md)。本模块在服务端本地按序
处理模型更新包；不加载模型、不调用外部服务、不判断文本是否忠实于图片。

## 在扫描 Pipeline 中的位置

目标业务流程先逐页上传并排入 OCR，再由用户确认最终页面集合、顺序与有效版本；只有最终输入被冻结且所需 OCR 结果齐备，未来调度层才按该顺序调用 VLM，并将响应交给本模块。OCR 回调乱序不能直接驱动 XML 追加，页面排序权也不属于本模块。完整流程见 [扫描 Pipeline](capture_pipeline.md)。

本模块的 `finalize()` 与未来会话级 `finalize` 不同：前者校验并返回 XML 字节，后者确认并冻结采集输入。后者尚未实现；本模块不会因此获得页面上传、会话冻结或进程恢复能力。生成最终文件由离线导出脚本负责，生成 Lark/Markdown/思源内容则仍需未来 Renderer。

## 模块与接口

模块：`capture2doc.formats.c2d_xml.assembler`，公共入口也从
`capture2doc.formats.c2d_xml` 导出。不建立标签 Dataclass，文档状态由内部 XML 树持有。

| 接口 | 行为 |
|---|---|
| `C2DAssembler(*, lang=None)` | 建立空 document，验证可选语言；非法参数抛 ValueError |
| `apply_update(xml: str \| bytes) -> ValidationResult` | 逐轮替换尾块、追加，成功才提交状态 |
| `context_blocks(*, count_tokens, token_budget=1536) -> tuple[bytes, ...]` | 返回阅读顺序下的完整历史 block |
| `finalize() -> bytes` | 重新校验并序列化 UTF-8 XML；不写文件、不冻结状态 |

`C2DAssemblyError` 继承 ValueError，携带 `issues` 元组。
没有 block 时 finalize 报 `EMPTY_DOCUMENT`；不能把结构合法的空文档当成生成成功。
重复 finalize 返回相同结果，之后仍可继续 apply_update。

## 尾块替换

首次更新的所有 block 都加入文档；已有历史时，用本轮第一个 block 的整个子树
替换旧尾块，其余 block 追加。允许尾块改变类型，例如把误识别的段落更正为标题。
较早节点的内容、顺序和内部空白保持。

每轮先安全解析并校验 c2d-update，在候选文档中执行替换，再检查完整 document。
未知属性、错误嵌套、非法表格、重复 title 或正文后的 title 均导致整轮失败；
此时文档和上下文不变，不保留部分新增内容。

更新包错误使用从零开始的 block_index；合并后全局错误提供 document XPath，
block_index 为 None。合并器复用现有校验器，不重复实现 XML 安全规则。

调用必须串行、每轮仅应用一次。该协议不具备重试幂等性；重复应用带新增块的更新
可能重复内容。未来调度层需负责轮次、版本检查与重试管理。
只读前缀不会被程序修改，但模型把旧内容复制到新尾块的语义重复无法由语法校验发现。

因此，未来页面上传的幂等重试不能直接复用于 XML 更新。会话重启、模型响应重放和跨进程组装恢复需要业务调度层管理轮次、有效版本与检查点；当前内存合并器和固定样例导出不构成这些能力的验证。

## 上下文 Token 策略

历史硬上限为 **1,536 token**，长尾块阈值为 **768 token**：

1. 有效预算取 min(1536, token_budget)。预算必须是非负整数；空历史直接返回空元组。
2. 先计数最后一个完整 block；超过预算时抛 CONTEXT_BUDGET_EXCEEDED，状态不变。
3. 尾块大于 768 token 时仅返回尾块；等于 768 时仍尝试多块。
4. 否则依次尝试最近三个、两个、一个连续 block，选第一个放得下的后缀。

每个 block 使用 UTF-8 XML，并携带独立解析所需的命名空间。去掉顶层 block 后的
兄弟间缩进，不清理 block 内的正文空白。多块按换行拼接后整体计数，
不会把各块 token 数简单相加；禁止截断 XML 或跳过中间 block。

`count_tokens(text: str) -> int` 由调用方注入，必须是确定性的非负整数计数器。
实际接入使用目标模型的 tokenizer，对传入文本计数，不给每个 block 添加独立
chat template，不用字符数近似。返回内容按以下方式拼接，与计数内容一致：

```python
from capture2doc.formats.c2d_xml import C2DAssembler

assembler = C2DAssembler(lang="zh-CN")
# result = assembler.apply_update(update_xml)
# if not result.valid: 处理 result.issues 后停止本轮
# tokenizer 由模型调用层加载；合并器不下载或加载模型
def count_tokens(text):
    return len(tokenizer.encode(text, add_special_tokens=False))

blocks = assembler.context_blocks(count_tokens=count_tokens, token_budget=1536)
history_xml = b"\n".join(blocks).decode("utf-8")
```

调用层还需为图片、OCR、提示词、模板和输出保留空间，并传入更小的实际历史预算。
当前 16K 模型配置预留 8K 输出，因此全部输入最多 8K；历史上限不是整个 prompt
上限。发送请求前仍须通过真实 processor 计算完整 prompt。
本轮使用可控计数器验证选择策略，尚未完成真实 tokenizer 与 VLM 质量评测。
完整大表格超过历史预算时明确失败，行级 patch 和超长块处理留待后续设计。

## 离线文件导出

从仓库根目录进入 server，使用已经准备的四轮更新样例：

```bash
cd server
mkdir -p .cache/c2d-assembly
uv run python scripts/assemble_c2d_xml.py \
  --updates tests/fixtures/c2d_xml/assembly/01_paragraph.xml \
            tests/fixtures/c2d_xml/assembly/02_list.xml \
            tests/fixtures/c2d_xml/assembly/03_table.xml \
            tests/fixtures/c2d_xml/assembly/04_finish.xml \
  --output .cache/c2d-assembly/document.c2d.xml \
  --lang zh-CN
```

样例依次续写段落、列表和表格；预期文档为同目录的 expected.c2d.xml。
此命令从空文档开始，严格按照 --updates 参数顺序处理，不根据文件名排序，
不加载旧文档、不去重、不自动重试。--updates 和 --output 必填，--lang 可选。
重复运行示例需换一个尚不存在的输出路径。

任何一轮失败都会停止处理，stderr 输出 JSON，包含源文件路径、从 1 开始的轮次
和 issues（校验失败）或 error（文件操作失败），并且不生成最终文件。
全部更新通过后执行 finalize，写入目标同目录临时文件，flush/fsync 后通过
不覆盖的硬链接发布，最后清理临时文件。已有文件、目录及断开的符号链接都拒绝覆盖，
发布前出现的并发目标也不会被替换。目标父目录必须已经存在。
底层文件系统若不支持硬链接则返回错误，不降级为可能覆盖的写入方式。

成功退出码 0，校验/文件操作失败为 1，参数错误为 2。
命令不调用 context_blocks，因此离线导出无需 tokenizer，较大 block 可以被合并；
为模型准备上下文时才检查 token 上限。模型窗口预算不属于文件格式限制。

## 序列化与验证

finalize 使用 UTF-8、XML 声明，关闭 pretty-print，保留 XML 解析后正文、
行内节点的 text/tail 及代码换行空白。不保证原始实体写法、命名空间前缀、
引号和输入字节编码完全相同；XML 标准的换行与属性规范化仍然适用。

已验证首次生成、连续尾块替换、列表与表格整体替换、错误回退、
标题全局约束、安全解析、语言参数、重复 finalize、混合文本与代码空白，
以及 768/769、1536/1537、较小预算、空历史和注入计数器行为。
离线命令另覆盖真实子进程导出与预期文件一致性、参数顺序、失败不落盘、
已有目标保护、发布竞态、写入失败清理、文件缺失和退出码。
当前 C2D 相关模块共 100 项测试通过：

```bash
cd server
uv run pytest tests/test_c2d_xml.py tests/test_c2d_assembler.py tests/test_assemble_c2d_xml.py -q
```

本轮服务端全量测试 155 项通过，相关 Python 文件的 Ruff 检查和 diff 空白检查通过。
四轮样例也已通过本地命令实际生成 document.c2d.xml；这是固定样例验证，不是模型实测。

真实 VLM 请求、完整 prompt 预算校验和模型续写质量尚未接入验证；
下一阶段可用本轮闭环承接模型响应。
