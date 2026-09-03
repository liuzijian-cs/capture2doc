# C2D-XML 标签体系

> 状态：v0.1 标签基线  
> 决策日期：2026-09-03

## 定位

C2D-XML 是 Capture2Doc 的规范中间表示。页面解析器和文档组装器先产生一份 C2D-XML，输出层再通过确定性转换生成以下目标格式：

- Lark 文档 XML；
- Markdown；
- 思源笔记 `.sy`。

C2D-XML 是唯一的内容基线，各输出格式都是它的派生产物。输出层不得调用模型补写内容，也不要求从目标格式无损反向恢复 C2D-XML。

当前优先保证 Lark 输出，Markdown 为通用回退格式，思源 `.sy` 由专用 Renderer 生成。标签设计以 Lark XML 为主要参考，同时限制在 Markdown 和思源能够直接表达或稳定降级的范围内。本阶段不考虑 DOC/DOCX 输出。

C2D-XML 只描述最终文档的逻辑内容，不保存原始页码、页面边界、坐标、采集来源或证据追溯。输入材料中的页眉、页脚和页码默认在组装阶段去除；跨图片的段落、列表和表格也应在进入 C2D-XML 前完成合并。

## 设计原则

1. **结构优先**：标题、段落、列表、引用、表格、公式和代码保留语义结构。
2. **Lark 优先**：在多种表达等价时，采用与 Lark XML 一致的标签名和属性名。
3. **稳定降级**：Markdown 或思源不支持的视觉效果必须能够被忽略，且不能丢失文字内容。
4. **有限样式**：只保留粗体、斜体、下划线、删除线、行内代码以及受限的文字颜色和背景色。
5. **确定性输出**：相同 C2D-XML 和相同 Renderer 配置必须生成相同结果。
6. **安全解析**：只接受符合本规范的 XML；禁止 DTD、外部实体和未声明标签。

## 文档外壳

C2D-XML 是一份具有单一根节点的完整 XML 文档：

```xml
<document
  xmlns="urn:capture2doc:c2d:1"
  schema-version="0.1"
  xml:lang="zh-CN">
  <title>文档标题</title>
  <h1>一级标题</h1>
  <p>正文内容。</p>
</document>
```

根节点规则：

- `<document>` 是唯一根节点；
- `schema-version` 必填，v0.1 固定为 `0.1`；
- `xml:lang` 可选，值使用 BCP 47 语言标签；
- `<title>` 最多出现一次，如存在必须是第一个内容节点；
- 根节点其余子节点按出现顺序组成最终阅读顺序；
- Lark Renderer 输出时移除 `<document>` 外壳和 C2D 命名空间，再转换其子节点。

不设置 `metadata`、`pages`、`sources` 或 `evidence` 等包装节点。非文档内容的运行信息保存在服务端任务数据中。

## 标签总表

### 块级标签

| 标签 | 说明 | 关键属性 |
|---|---|---|
| `<title>` | 文档标题，最多一个 | 无 |
| `<h1>` 至 `<h6>` | 六级标题 | 无 |
| `<p>` | 普通段落 | 无 |
| `<blockquote>` | 引用块，可包含段落、列表或嵌套引用 | 无 |
| `<ul>` | 无序列表 | 无 |
| `<ol>` | 有序列表 | 无 |
| `<li>` | 列表项 | 无 |
| `<table>` | 表格 | 无 |
| `<thead>` | 表头区域 | 无 |
| `<tbody>` | 表体区域 | 无 |
| `<tr>` | 表格行 | 无 |
| `<th>` | 表头单元格 | `rowspan`、`colspan` |
| `<td>` | 普通单元格 | `rowspan`、`colspan` |
| `<pre>` | 代码块，必须只包含一个 `<code>` | `lang`（可选） |
| `<hr/>` | 分隔线 | 无 |

### 行内标签和组件

| 标签 | 说明 | 关键属性 |
|---|---|---|
| `<b>` | 加粗 | 无 |
| `<em>` | 斜体 | 无 |
| `<u>` | 下划线 | 无 |
| `<del>` | 删除线 | 无 |
| `<code>` | 行内代码；位于 `<pre>` 内时表示代码块内容 | 无 |
| `<a>` | 普通链接 | `href`（必填） |
| `<br/>` | 段内换行 | 无 |
| `<span>` | 受限的文字颜色或背景色 | `text-color`、`background-color` |
| `<latex>` | 行内 LaTeX 公式 | 无 |

行内标签可用于 `<title>`、`<h1>` 至 `<h6>`、`<p>`、`<li>`、`<th>` 和 `<td>` 的文本内容中。`<latex>` 是叶子组件，内部只能出现经过 XML 转义的 LaTeX 文本。

## 富文本嵌套

行内样式采用与 Lark 一致的固定嵌套顺序，外层到内层依次为：

```text
<a> → <b> → <em> → <del> → <u> → <code> → <span> → 文本
```

关闭顺序必须严格反转。`<latex>` 和 `<br/>` 不进入该嵌套链。

示例：

```xml
<p>
  <a href="https://example.com"><b><span text-color="blue">带链接的蓝色粗体</span></b></a>
</p>
```

## 颜色

C2D-XML 只支持以下七种显式颜色：

```text
red, orange, yellow, green, blue, purple, gray
```

规则：

- `text-color` 和 `background-color` 使用同一组颜色枚举；
- 未设置属性表示使用目标格式的默认颜色；
- 两个属性可以同时出现；
- 不支持 `rgb()`、`rgba()`、十六进制颜色、CSS 变量或任意 `style`；
- 颜色只应用于 `<span>`，不用于表格单元格、引用块、按钮或其他容器。

示例：

```xml
<p>
  普通文字，
  <span text-color="red">红色文字</span>，
  <span background-color="yellow">黄色背景</span>，
  <span text-color="blue" background-color="gray">蓝色文字和灰色背景</span>。
</p>
```

Lark Renderer 直接使用上述属性。思源 Renderer 将七种名称映射为固定的目标 CSS 颜色；Markdown Renderer 使用受限的内联 HTML `<span style="...">`。不支持 HTML 样式的 Markdown 阅读器可以忽略颜色，但必须保留文字。

## 引用

C2D-XML 使用标准 `<blockquote>` 表达 Markdown 中以 `>` 开头的引用块：

```xml
<blockquote>
  <p>引用的第一段。</p>
  <p>引用的第二段。</p>
</blockquote>
```

Markdown Renderer 输出为：

```markdown
> 引用的第一段。
>
> 引用的第二段。
```

嵌套引用通过嵌套 `<blockquote>` 表达。飞书的 `<cite type="user">` 和 `<cite type="doc">` 是平台引用组件，不属于普通引用，因此不进入 C2D-XML。

## 列表

- `<ul>` 和 `<ol>` 只能直接包含 `<li>`；
- 新增列表项必须位于 `<ul>` 或 `<ol>` 内；
- 嵌套列表放在父级 `<li>` 内；
- 列表顺序由节点位置决定，C2D-XML 不使用 Lark 的 `seq="auto"`；Lark Renderer 在输出有序列表时按需补充该属性。

```xml
<ol>
  <li>第一项</li>
  <li>
    第二项
    <ul>
      <li>子项</li>
    </ul>
  </li>
</ol>
```

## 表格

表格采用标准 HTML 树结构：

- `<table>` 可包含一个 `<thead>` 和一个 `<tbody>`；
- `<thead>` 使用 `<th>`，`<tbody>` 使用 `<td>`；
- 没有表头的表格可以只包含 `<tbody>`；
- `rowspan` 和 `colspan` 必须是大于等于 `1` 的整数；
- 合并单元格只输出起始单元格，被覆盖的单元格不得重复出现；
- 不保存列宽、单元格背景色或垂直对齐等视觉属性。

```xml
<table>
  <thead>
    <tr><th>名称</th><th>数量</th></tr>
  </thead>
  <tbody>
    <tr><td rowspan="2">项目 A</td><td>10</td></tr>
    <tr><td>20</td></tr>
  </tbody>
</table>
```

Markdown Renderer 对简单矩形表格生成管道表格；包含 `rowspan` 或 `colspan` 时生成 HTML table，以避免丢失合并关系。

## 代码

行内代码使用 `<code>`：

```xml
<p>执行 <code>git status</code> 查看状态。</p>
```

代码块使用 `<pre><code>`。三个反引号只是 Markdown Renderer 的输出语法，不进入 C2D-XML：

```xml
<pre lang="python"><code>if a &lt; b:
    print("hello")</code></pre>
```

`lang` 可选；不支持代码块标题或 `caption`。`<pre>` 内的换行和空白必须原样保留。

## 公式

公式统一使用 Lark 的 `<latex>`，不另设 `<formula>`：

```xml
<p>标准更新公式为 <latex>h_l = h_{l-1} + f_{l-1}(h_{l-1})</latex>。</p>
```

首版只定义行内公式。需要独占一段显示时，使用只包含公式的段落：

```xml
<p><latex>E = mc^2</latex></p>
```

Markdown Renderer 将其转换为 `$...$`；思源 Renderer 将其转换为 `inline-math` 文本标记；Lark Renderer 保留 `<latex>`。

## 链接

链接统一使用 `<a href="...">`：

```xml
<p>查看 <a href="https://example.com">参考资料</a>。</p>
```

Markdown Renderer 输出为 `[参考资料](https://example.com)`。C2D-XML 不区分普通链接、预览卡片和书签，也不包含 `<bookmark>` 或 `<a type="url-preview">`。

## 输出映射

| C2D-XML | Lark | Markdown | 思源 `.sy` |
|---|---|---|---|
| `<title>` | `<title>` | 首个 `#` 标题 | `NodeDocument.Properties.title` |
| `<h1>` 至 `<h6>` | 同名标签 | `#` 至 `######` | `NodeHeading` + `HeadingLevel` |
| `<p>` | `<p>` | 普通段落 | `NodeParagraph` |
| `<blockquote>` | `<blockquote>` | `>` 引用 | 引用块节点 |
| `<ul>/<ol>/<li>` | 同名标签，必要时补 `seq` | Markdown 列表 | 列表节点 |
| `<pre><code>` | 同名标签 | fenced code block | 代码块节点 |
| `<b>` | `<b>` | `**文字**` | 粗体文本标记 |
| `<em>` | `<em>` | `*文字*` | 斜体文本标记 |
| `<u>` | `<u>` | 原始 HTML `<u>` | 下划线文本标记 |
| `<del>` | `<del>` | `~~文字~~` | 删除线文本标记 |
| 行内 `<code>` | `<code>` | 反引号行内代码 | 行内代码文本标记 |
| `<latex>` | `<latex>` | `$...$` | `NodeTextMark`，类型 `inline-math` |
| `<a>` | `<a>` | `[文字](URL)` | 链接文本标记 |
| `<span>` 颜色 | Lark 命名色属性 | 受限 HTML `style` | 固定 CSS 颜色映射 |
| `<br/>` | `<br/>` | Markdown 硬换行 | 段内换行节点 |
| `<hr/>` | `<hr/>` | `---` | 分隔线节点 |
| `<table>` | HTML 结构表格 | 管道表格或 HTML table | `NodeTable` 树 |

思源 Renderer 负责生成 `.sy` 所需的节点 ID、文档属性和更新时间等目标格式字段；这些字段不进入 C2D-XML。

## 明确排除的标签和属性

v0.1 不包含以下能力：

- 图片与视图：`img`、`figure`、`caption`；
- 页面或布局：`grid`、`column`、绝对位置、分页和 `align`；
- 飞书资源：`whiteboard`、`sheet`、`source`、`bookmark`；
- 飞书交互组件：`button`、`time`、`task`、`chat_card`、`sub-page-list`；
- 飞书提及组件：`cite type="user"`、`cite type="doc"`；
- 其他富块：`callout`、`checkbox`；
- 任意 CSS、任意颜色、表格美化和代码块标题。

若以后增加能力，必须先定义 C2D 语义以及 Lark、Markdown、思源三种输出的确定性降级规则，不能直接把某个平台的私有节点加入核心。

## 转义与规范化

标签本身不能转义，标签内部的文本和属性值按 XML 规则转义：

| 字符 | 文本中的写法 | 属性值中的写法 |
|---|---|---|
| `<` | `&lt;` | `&lt;` |
| `>` | `&gt;` | `&gt;` |
| `&` | `&amp;` | `&amp;` |
| `"` | `"` | `&quot;` |
| `'` | `'` | `&apos;`（使用单引号包围属性时） |

错误：

```xml
&lt;p&gt;内容&lt;/p&gt;
```

正确：

```xml
<p>A &amp; B 的对比：1 &lt; 2</p>
```

其他规范化规则：

- 普通文本中的明确换行使用 `<br/>`；
- `<pre>` 内保留原始换行和空白；
- 删除纯空白段落以及 U+200D 等编辑器占位字符；
- 不把 OCR 产生的视觉换行自动转换为 `<br/>`；
- 解析器必须禁用 DTD 和外部实体；
- 未声明标签、属性或颜色值必须导致校验失败，不能静默透传。

## 完整示例

```xml
<document
  xmlns="urn:capture2doc:c2d:1"
  schema-version="0.1"
  xml:lang="zh-CN">
  <title>Attention Residuals</title>

  <h1>快速摘要</h1>
  <h2>背景</h2>

  <p>
    现代大型语言模型普遍使用
    <b>残差连接与 PreNorm</b>，
    <span background-color="yellow">不同层的贡献并不总是相同</span>。
  </p>

  <blockquote>
    <p>结构信息优先于视觉排版信息。</p>
  </blockquote>

  <p><latex>h_l = h_{l-1} + f_{l-1}(h_{l-1})</latex></p>

  <ul>
    <li>保留文档语义结构</li>
    <li>通过输出层转换目标格式</li>
  </ul>

  <table>
    <thead>
      <tr><th>目标</th><th>表示</th></tr>
    </thead>
    <tbody>
      <tr><td>Lark</td><td>Lark XML</td></tr>
      <tr><td>Markdown</td><td>Markdown 与受限 HTML</td></tr>
      <tr><td>思源</td><td>结构化 `.sy` 节点</td></tr>
    </tbody>
  </table>

  <pre lang="python"><code>if document.is_valid():
    render(document)</code></pre>

  <p>更多信息见 <a href="https://example.com">参考资料</a>。</p>
  <hr/>
</document>
```

## 后续工作

标签体系冻结后，下一步需要：

1. 编写 v0.1 XSD；
2. 为 Lark、Markdown 和思源分别建立逐标签 Renderer 测试；
3. 建立规范化输出和 XML 安全解析测试；
4. 使用同一批样例验证三种输出的内容一致性和允许的样式降级。
