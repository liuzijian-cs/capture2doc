# Android 富文本剪贴板协议

> 状态：桌面端协议已实测，Android 输出基线已决定，目标 App 真机粘贴仍待验证
>
> 调研日期：2026-09-05

## 目标与边界

Android App 最终取得完整 C2D-XML 后，除导出文件外，还需要支持直接复制并粘贴到飞书、思源或普通文本输入框。剪贴板输出必须由确定性 Renderer 生成，不调用模型，也不把页面、OCR、上传或来源追溯信息加入最终内容。

剪贴板传输格式与文件导出格式是两个边界：

- Lark 文档 XML 用于目标文件或发布链路，不是跨应用剪贴板格式；
- 思源 `.sy` 是思源文件格式，不是跨应用剪贴板格式；
- Android 跨应用富文本使用标准 HTML，并同时提供纯文本回退；
- 平台私有剪贴板数据仅用于理解目标软件行为，不作为 Capture2Doc 输出契约。

本页记录的是一次 macOS Chromium/Electron 剪贴板实测与思源开源实现核对。它足以确定公共输出方向，但不能代替 Android 飞书和思源当前版本的真机粘贴验收。

## 调研方法

用户分别在飞书文档和思源中复制包含多种结构与样式的内容。通过 macOS `NSPasteboard` 只读检查每个 pasteboard item 的类型、字节数和关键结构，没有把文档正文、文档 URL、块 ID 或私有 JSON 保存到仓库。

以下大小只描述本次样本，不是协议上限，也不能用于估算任意文档的固定膨胀比例。

## 飞书剪贴板

### 实测类型

| macOS pasteboard 类型 | 本次大小 | 内容 |
|---|---:|---|
| `public.html` | 822,315 B | 可跨应用使用的 HTML，以及飞书附加的私有记录节点 |
| `public.utf8-plain-text` | 40,208 B | 纯文本回退 |
| `org.chromium.web-custom-data` | 810,188 B | Chromium 自定义数据，其中包含 `docx/record` |
| `org.chromium.source-url` | 53 B | 复制来源 URL |

### 公共 HTML 层

飞书的 `public.html` 使用了可识别的标准 HTML 结构，包括：

- 标题：`h1`、`h2`、`h3`；
- 段落容器：`div`；
- 粗体和斜体：`strong`、`em`；
- 链接：`a href="..."`；
- 列表：`ul`、`ol`、`li`；
- 引用：`blockquote`；
- 表格：`table`、`thead`、`tbody`、`tr`、`th`、`td`；
- 代码：`pre`、`code`；
- 颜色和高亮：内联 `color`、`background-color`。

该层还带有 `data-lark-*`、旧记录 ID、编辑器 class、布局尺寸等飞书内部信息。Capture2Doc 只需要生成干净的语义 HTML，不复制这些属性。

### 私有层

本次 `org.chromium.web-custom-data` 的自定义键为 `docx/record`，值是飞书 Block JSON。HTML 尾部还存在等价的隐藏节点，结构形如：

```html
<span
  class="lark-record-clipboard"
  data-lark-record-format="docx/record"
  data-lark-record-data="...">
</span>
```

私有记录包含当前文档和选区上下文，例如根块、父块、块集合、记录映射、选择范围和粘贴随机标识。它具有以下限制：

- 与来源文档身份绑定，不能由独立 C2D 文档合理构造；
- 未找到飞书提供的公开、稳定剪贴板规范；
- Web、桌面端和 Android 端的封装方式可能不同；
- 私有结构可能随飞书版本变化；
- 复制这些字段会泄漏不属于最终文档内容的内部信息。

因此 Android App 不生成 `docx/record`、`data-lark-record-data` 或任何飞书记录 ID。

## 思源剪贴板

### 实测类型

| macOS pasteboard 类型 | 本次大小 | 内容 |
|---|---:|---|
| `public.html` | 25,389 B | HTML、内嵌的思源私有注释和外部回退层 |
| `public.utf8-plain-text` | 2,468 B | 标准 Markdown 回退 |
| `org.chromium.web-custom-data` | 29,900 B | Chromium 自定义数据，其中包含 `text/siyuan` |
| `org.chromium.internal.source-rfh-token` | 24 B | Chromium 内部来源标识 |
| `org.chromium.source-url` | 56 B | 本地编辑器来源 URL |

### 私有 `text/siyuan`

`text/siyuan` 的值是思源 BlockDOM。它包含 `NodeHeading`、`NodeParagraph`、`NodeTable` 等节点类型，以及节点 ID、更新时间、编辑器 class 和行内标记。

思源同时把相同 BlockDOM 做 Base64 编码，放进 `public.html` 开头的注释作为 Web 剪贴板回退：

```html
<!--data-siyuan='BASE64_BLOCK_DOM'-->
```

思源源码也明确实现了这一行为：

- 粘贴时读取 `text/html`、`text/plain` 和 `text/siyuan`；
- Web 剪贴板可从 `data-siyuan` 注释恢复内部 BlockDOM；
- 内部块复制生成标准 Markdown、HTML 和 `text/siyuan` 三种表示。

实现依据：

- [思源粘贴入口 `paste.ts`](https://github.com/siyuan-note/siyuan/blob/master/app/src/protyle/util/paste.ts)
- [思源 Web 剪贴板编码 `clipboardData.ts`](https://github.com/siyuan-note/siyuan/blob/master/app/src/protyle/util/clipboardData.ts)
- [思源块复制载荷 `blockDOMClipboard.ts`](https://github.com/siyuan-note/siyuan/blob/master/app/src/protyle/util/blockDOMClipboard.ts)
- [思源外部富文本转换 `richClipboard.ts`](https://github.com/siyuan-note/siyuan/blob/master/app/src/protyle/util/richClipboard.ts)

Capture2Doc 不生成 `text/siyuan`、`data-siyuan` 注释、思源节点 ID 或更新时间。思源内部格式虽然开源，但仍是目标 App 的实现格式，不是 Android 跨应用公共协议。

### HTML 和 Markdown 回退

移除本次 `data-siyuan` 注释后，公共 HTML 约为 2,920 个字符，包含标题、段落、表格、粗体标记、行内公式描述和颜色样式。

实测 HTML 中仍出现思源内部表达，例如：

```html
<span data-type="strong">...</span>
<span
  data-type="inline-math"
  data-subtype="math"
  data-content="h_l = ...">
</span>
<span style="background-color: var(--b3-card-info-background); ...">...</span>
```

这些 CSS 变量只在思源主题中有定义，不能作为通用 HTML 颜色。Capture2Doc 应改用 `strong`、标准 HTML 元素和固定 CSS 色值。

本次 `public.utf8-plain-text` 是标准 Markdown，包含标题、粗体、行内公式和管道表格。思源在没有 HTML 时会调用 Markdown 到 BlockDOM 的转换，因此“复制到思源”可以优先使用 Markdown 作为首版稳定入口。

## Android 公共协议

### 富文本载荷

Android 从 API 16 起提供 `ClipData.newHtmlText(label, text, htmlText)`。其中：

- `htmlText` 是实际 HTML；
- `text` 是接收端不处理 HTML 时使用的必填纯文本回退；
- `ClipDescription` 的类型是 `text/html`。

官方说明：

- [Android Copy and paste](https://developer.android.com/develop/ui/views/touch-and-input/copy-paste)
- [`ClipData.newHtmlText`](https://developer.android.com/reference/android/content/ClipData#newHtmlText(java.lang.CharSequence,%20java.lang.CharSequence,%20java.lang.String))
- [`ClipData.Item`](https://developer.android.com/reference/kotlin/android/content/ClipData.Item)

建议使用独立返回值，避免 UI、XML 解析和系统剪贴板调用耦合：

```kotlin
data class C2dClipboardPayload(
    val plainText: String,
    val htmlText: String,
)
```

写入富文本：

```kotlin
val clip = ClipData.newHtmlText(
    "Capture2Doc",
    payload.plainText,
    payload.htmlText,
)
clipboardManager.setPrimaryClip(clip)
```

写入纯文本：

```kotlin
val clip = ClipData.newPlainText("Capture2Doc", plainText)
clipboardManager.setPrimaryClip(clip)
```

### 产品入口建议

| 操作 | Android 载荷 | 目标 |
|---|---|---|
| 复制纯文本 | `text/plain` | 普通文本框、聊天或不需要样式的场景 |
| 复制富文本 / 复制到飞书 | `text/html` + 纯文本回退 | 飞书及其他富文本编辑器 |
| 复制 Markdown / 复制到思源 | Markdown `text/plain` | 让思源调用 Markdown 解析器生成原生块 |

“复制到思源”是否改为通用 HTML，取决于真机验收中颜色、公式和复杂表格的结果。首版不写入思源私有 BlockDOM。

### C2D 到公共 HTML 映射

| C2D-XML | 剪贴板 HTML | 说明 |
|---|---|---|
| `title` | `h1` | 剪贴板不能设置目标文档元数据，只保留可见标题 |
| `h1` 至 `h6` | 同名标签 | 保留标题层级 |
| `p` | `p` | 不使用编辑器专有段落 class |
| `blockquote` | `blockquote` | 保留普通引用语义 |
| `ul`、`ol`、`li` | 同名标签 | 保留嵌套关系 |
| `table`、`thead`、`tbody`、`tr`、`th`、`td` | 同名标签 | 保留 `rowspan`、`colspan` |
| `pre`、`code` | 同名标签 | `lang` 映射为受限 `class="language-*"` |
| `hr`、`br` | 同名标签 | 使用标准空元素 |
| `b` | `strong` | 使用语义 HTML |
| `em` | `em` | 使用语义 HTML |
| `u` | `u` | 目标 App 可按自身规则降级 |
| `del` | `s` | 使用广泛支持的删除线标签 |
| 行内 `code` | `code` | 与代码块上下文区分 |
| `a href` | `a href` | 首版只允许经过校验的 `http`、`https` URL |
| `span` 文字色/背景色 | `span style="color:...;background-color:..."` | 七种 C2D 颜色确定性映射为固定 CSS 色值 |
| `latex` | 可见 LaTeX 文本 | 通用 HTML 暂不承诺生成目标 App 原生公式 |

Renderer 不输出任意 CSS、脚本、事件属性、目标编辑器节点 ID 或未知 `data-*` 属性。

## 公式与颜色

### 公式

一个通用 HTML 片段目前不能保证同时生成飞书和思源的原生公式：

- 思源目标优先使用 Markdown `$...$`，由思源解析为公式；
- 飞书公共 HTML 首版必须保证 LaTeX 内容可见，但不宣称会自动转成原生公式；
- 若必须生成飞书原生公式，应另行验证飞书 Android 的 HTML 导入行为，或使用飞书文档发布/API 链路；
- 不通过伪造 `docx/record` 实现公式。

### 颜色

C2D 的七种语义颜色应在 Clipboard Renderer 中映射为固定 CSS 值。不得直接透传：

- 思源 `var(--b3-...)`；
- 飞书本次样本中的临时 RGBA 值；
- 任意模型生成的 `style`；
- Android UI 主题色。

最终 CSS 色值需要用同一份真机样例验证文字色和背景色在飞书、思源中的实际保留情况。

## 大小与安全限制

Android 官方说明 `ClipData.Item` 不接受超过 800 KB 的 HTML 文本。实现必须按 UTF-8 字节数预检并保留安全余量；超过阈值时提示用户：

- 导出或分享文件；
- 分段复制；
- 改用纯文本或 Markdown。

本次飞书样本的 `public.html` 已达到 822,315 B，主要原因是 HTML 和私有 Block JSON 重复携带。Capture2Doc 只生成干净 HTML，通常会显著更小，但不能因此省略上限检查。

其他安全规则：

- 所有文本和属性值按 HTML 上下文转义；
- URL 先解析并校验 scheme，再写入 `href`；
- 颜色只从固定映射表生成；
- 不支持 `script`、`style` 块、事件属性、iframe 或任意嵌入对象；
- 剪贴板失败不能被报告为导出成功；
- 剪贴板内容可能被其他前台应用读取，不在其中加入服务端 ID、路径、认证信息或诊断数据。

## Android 真机验证

### 为什么不能只靠 ADB shell

现代 Android 对后台剪贴板读取有限制，`adb shell` 也没有可依赖的跨版本富文本读取协议。验证时应让 Capture2Doc debug 进程处于前台，通过 `ClipboardManager` 读取：

- `ClipDescription` 中的 MIME 类型；
- `ClipData.Item.text`；
- `ClipData.Item.htmlText`；
- 文本和 HTML 的 UTF-8 字节数。

再通过 instrumentation 或受控日志把摘要回传给 ADB。诊断输出不得打印整份用户文档，默认只报告类型、大小、哈希和经过裁剪的测试夹具。

### 最小测试夹具

使用一份固定 C2D-XML，覆盖：

- `h1`、`h2` 和普通段落；
- 粗体、斜体、下划线、删除线、行内代码；
- 七种文字色和七种背景色；
- 一个 `https` 链接；
- 无序列表、有序列表和嵌套列表；
- 引用块、分隔线；
- 简单表格与合并单元格表格；
- 代码块；
- 行内 LaTeX。

分别执行以下闭环：

1. Capture2Doc 生成剪贴板载荷；
2. 用户粘贴到飞书 Android 文档；
3. 用户粘贴到思源 Android 文档；
4. 对照结构、样式、链接和公式结果；
5. 从两个目标 App 反向复制，Capture2Doc debug 探针只读检查 MIME、`text` 和 `htmlText`；
6. 记录目标 App 版本、Android 版本和每项结果。

### 验收矩阵

| 能力 | 飞书 Android | 思源 Android | 当前状态 |
|---|---|---|---|
| 标题与段落 | 待验证 | 待验证 | 桌面复制层可表达 |
| 粗体、斜体、下划线、删除线 | 待验证 | 待验证 | 标准 HTML 可表达 |
| 文字色和背景色 | 待验证 | 待验证 | 需冻结 CSS 映射 |
| `https` 链接 | 待验证 | 待验证 | 标准 HTML 可表达 |
| 列表和嵌套列表 | 待验证 | 待验证 | 标准 HTML/Markdown 可表达 |
| 引用 | 待验证 | 待验证 | 标准 HTML/Markdown 可表达 |
| 表格和合并单元格 | 待验证 | 待验证 | HTML 可表达，目标解析待验收 |
| 行内与块级代码 | 待验证 | 待验证 | 标准 HTML/Markdown 可表达 |
| 原生公式 | 待验证 | 待验证 | 思源优先 Markdown；飞书无公共保证 |
| 超限提示与降级 | 待实现 | 待实现 | Android HTML 上限已确认 |

## 当前决定

- 新增独立 Clipboard Renderer，输入为完整、有效的 C2D-XML；
- 公共富文本契约为干净 HTML 加纯文本回退；
- 纯文本复制使用 `ClipData.newPlainText`；
- 富文本复制使用 `ClipData.newHtmlText`；
- 思源首版候选入口为 Markdown `text/plain`；
- 不生成飞书 `docx/record` 或思源 `text/siyuan`；
- 不把 Lark XML 或思源 `.sy` 直接放进系统剪贴板冒充富文本；
- 颜色、公式、复杂表格和大小余量在 Android 真机闭环后冻结。
