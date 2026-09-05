package io.github.liuzijiancs.capture2doc.core.document

import java.io.StringReader
import javax.xml.parsers.SAXParserFactory
import org.xml.sax.Attributes
import org.xml.sax.InputSource
import org.xml.sax.SAXException
import org.xml.sax.helpers.DefaultHandler

internal sealed interface C2dNode
internal data class C2dText(val value: String) : C2dNode
internal data class C2dElement(val tag: String, val attributes: Map<String, String>, val children: List<C2dNode>) : C2dNode
internal data class C2dDocument(val blocks: List<C2dElement>)

/** Namespace-aware, bounded parsing. Rendering never consumes arbitrary HTML. */
internal object C2dParser {
    const val NS = "urn:capture2doc:c2d:1"
    val colors = setOf("red", "orange", "yellow", "green", "blue", "purple", "gray")
    private val inline = listOf("a", "b", "em", "del", "u", "code", "span")
    private val rich = inline.toSet() + setOf("latex", "br")
    private val blocks = setOf("title", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "blockquote", "table", "pre", "hr")
    private data class Builder(val tag: String, val attrs: Map<String, String>, val children: MutableList<C2dNode> = mutableListOf())

    fun document(xml: String): C2dDocument {
        val root = parse(xml)
        require(root.tag == "document") { "需要完整 C2D 文档" }
        validate(root)
        val elements = root.children.filterIsInstance<C2dElement>()
        require(elements.isNotEmpty()) { "文档没有可用内容" }
        return C2dDocument(elements)
    }

    fun block(xml: String): C2dElement {
        val root = parse("<c2d-update xmlns=\"$NS\" schema-version=\"0.1\">$xml</c2d-update>")
        validate(root)
        return root.children.filterIsInstance<C2dElement>().single()
    }

    private fun parse(xml: String): C2dElement {
        require(xml.length <= 16 * 1024 * 1024) { "文档内容过大" }
        require(!Regex("<!\\s*(DOCTYPE|ENTITY)", RegexOption.IGNORE_CASE).containsMatchIn(xml)) { "不允许 XML 实体或 DTD" }
        require(!xml.contains("<!--")) { "不允许 XML 注释" }
        val stack = ArrayDeque<Builder>()
        var root: C2dElement? = null
        var count = 0
        val handler = object : DefaultHandler() {
            override fun resolveEntity(publicId: String?, systemId: String?): InputSource = throw SAXException("External entity forbidden")
            override fun startElement(uri: String, localName: String, qName: String, attributes: Attributes) {
                require(uri == NS && stack.size < 96 && ++count <= 200_000) { "XML 命名空间或结构无效" }
                val attrs = buildMap {
                    for (i in 0 until attributes.length) {
                        val ns = attributes.getURI(i)
                        val name = attributes.getLocalName(i)
                        require('\u200d' !in attributes.getValue(i)) { "不允许零宽连接符" }
                        require(ns.isEmpty() || (ns == "http://www.w3.org/XML/1998/namespace" && name == "lang")) { "不支持的 XML 属性" }
                        put(if (ns.isEmpty()) name else "xml:$name", attributes.getValue(i))
                    }
                }
                stack.addLast(Builder(localName, attrs))
            }
            override fun characters(ch: CharArray, start: Int, length: Int) {
                if (stack.isNotEmpty()) {
                    val children = stack.last().children
                    val text = String(ch, start, length)
                    require('\u200d' !in text) { "不允许零宽连接符" }
                    val previous = children.lastOrNull()
                    if (previous is C2dText) children[children.lastIndex] = C2dText(previous.value + text)
                    else children.add(C2dText(text))
                }
            }
            override fun processingInstruction(target: String?, data: String?) { throw SAXException("Processing instruction forbidden") }
            override fun endElement(uri: String, localName: String, qName: String) {
                val b = stack.removeLast()
                val element = C2dElement(b.tag, b.attrs, b.children.toList())
                if (stack.isEmpty()) root = element else stack.last().children.add(element)
            }
        }
        val factory = SAXParserFactory.newInstance().apply { isNamespaceAware = true }
        val reader = factory.newSAXParser().xmlReader
        // Providers differ on Android/JVM; the lexical gate and resolver remain mandatory.
        listOf("http://xml.org/sax/features/external-general-entities", "http://xml.org/sax/features/external-parameter-entities",
            "http://apache.org/xml/features/nonvalidating/load-external-dtd").forEach { feature ->
            runCatching { reader.setFeature(feature, false) }
        }
        reader.contentHandler = handler
        reader.entityResolver = handler
        reader.errorHandler = handler
        try { reader.parse(InputSource(StringReader(xml))) }
        catch (_: SAXException) { throw IllegalArgumentException("XML 内容无法解析") }
        return requireNotNull(root) { "XML 内容为空" }
    }

    private fun validate(node: C2dElement, parent: String? = null) {
        val tag = node.tag
        val elements = node.children.filterIsInstance<C2dElement>()
        val attrs = when (tag) {
            "document" -> setOf("schema-version", "xml:lang")
            "c2d-update" -> setOf("schema-version")
            "span" -> setOf("text-color", "background-color")
            "a" -> setOf("href")
            "pre" -> setOf("lang")
            "td", "th" -> setOf("rowspan", "colspan")
            else -> emptySet()
        }
        require(node.attributes.keys.all { it in attrs }) { "不支持的 $tag 属性" }
        val allowed = when (tag) {
            "document", "c2d-update" -> blocks
            "title", "p", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th" -> rich
            "ul", "ol" -> setOf("li")
            "li" -> rich + setOf("ul", "ol")
            "blockquote" -> setOf("p", "ul", "ol", "blockquote")
            "table" -> setOf("thead", "tbody")
            "thead", "tbody" -> setOf("tr")
            "tr" -> setOf(if (parent == "thead") "th" else "td")
            "pre" -> setOf("code")
            "latex", "br", "hr" -> emptySet()
            in inline -> if (tag == "code" && parent == "pre") emptySet() else inline.drop(inline.indexOf(tag) + 1).toSet()
            else -> throw IllegalArgumentException("不支持的 C2D 标签：$tag")
        }
        require(elements.all { it.tag in allowed }) { "$tag 内的结构无效" }
        val structural = setOf("document", "c2d-update", "ul", "ol", "blockquote", "table", "thead", "tbody", "tr", "pre", "hr", "br")
        if (tag in structural) require(node.children.filterIsInstance<C2dText>().all { it.value.isBlank() }) { "$tag 内含无效文本" }
        if (tag in setOf("document", "c2d-update")) require(node.attributes["schema-version"] == "0.1") { "不支持的 C2D 版本" }
        if (tag == "document") require(elements.count { it.tag == "title" } <= 1 && elements.drop(1).none { it.tag == "title" }) { "文档标题顺序无效" }
        if (tag in setOf("ul", "ol", "blockquote", "thead", "tbody")) require(elements.isNotEmpty()) { "$tag 不能为空" }
        if (tag == "pre") require(elements.size == 1) { "代码块需要一个 code 节点" }
        if (tag == "table") require(elements.map { it.tag } in listOf(listOf("tbody"), listOf("thead", "tbody"))) { "表格结构无效" }
        if (tag == "a") require(!node.attributes["href"].isNullOrBlank()) { "链接缺少地址" }
        if (tag == "span") require(node.attributes.isNotEmpty() && node.attributes.values.all { it in colors }) { "不支持的颜色" }
        if (tag in setOf("td", "th")) require(node.attributes.values.all { (it.toIntOrNull() ?: 0) in 1..10_000 }) { "合并单元格无效" }
        node.attributes["xml:lang"]?.let { require(Regex("[A-Za-z]{1,8}(-[A-Za-z0-9]{1,8})*").matches(it)) { "语言属性无效" } }
        if (tag == "p") require(hasText(node)) { "段落不能为空" }
        elements.forEach { validate(it, tag) }
        if (tag == "table") validateTable(elements)
    }

    private fun hasText(node: C2dNode): Boolean = when (node) {
        is C2dText -> node.value.isNotBlank()
        is C2dElement -> node.children.any(::hasText)
    }

    private fun validateTable(groups: List<C2dElement>) {
        var tableWidth: Int? = null
        groups.forEach { group ->
            val rows = group.children.filterIsInstance<C2dElement>()
            // One interval per active cell, not rowspan × colspan allocation.
            data class Occupied(val start: Int, val end: Int, val until: Int)
            val active = mutableListOf<Occupied>()
            rows.forEachIndexed { rowIndex, row ->
                active.removeAll { it.until <= rowIndex }
                var cursor = 0
                row.children.filterIsInstance<C2dElement>().forEach { cell ->
                    while (true) {
                        val occupied = active.firstOrNull { cursor in it.start until it.end } ?: break
                        cursor = occupied.end
                    }
                    val end = cursor + (cell.attributes["colspan"]?.toInt() ?: 1)
                    val until = rowIndex + (cell.attributes["rowspan"]?.toInt() ?: 1)
                    require(end <= 100_000 && until <= rows.size && active.none { cursor < it.end && end > it.start }) { "表格跨度重叠或超出区域" }
                    active += Occupied(cursor, end, until)
                    cursor = end
                }
                var width = 0
                active.sortedBy { it.start }.forEach { require(it.start == width) { "表格存在空洞" }; width = it.end }
                require(width > 0 && (tableWidth == null || tableWidth == width)) { "表格行宽不一致" }
                tableWidth = width
            }
        }
    }
}

internal object C2dRenderer {
    private val foreground = listOf("#D83931", "#B96500", "#8A6A00", "#16803C", "#245BDB", "#7F3FBF", "#646A73")
    private val background = listOf("#FDE2E2", "#FFE8CC", "#FFF3B0", "#DCF4E4", "#E1EBFF", "#EFE2FA", "#ECEEF0")
    private val colorNames = listOf("red", "orange", "yellow", "green", "blue", "purple", "gray")
    fun escape(value: String): String = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&#39;")
    fun safeLink(value: String): Boolean = runCatching {
        val uri = java.net.URI(value)
        uri.scheme?.lowercase() in setOf("http", "https") && !uri.host.isNullOrBlank() && uri.userInfo == null
    }.getOrDefault(false)

    fun html(document: C2dDocument, forPreview: Boolean = false): String = document.blocks.joinToString("\n") { html(it, forPreview) }
    fun html(node: C2dNode, forPreview: Boolean = false): String {
        if (node is C2dText) return escape(node.value)
        node as C2dElement
        val children = node.children.joinToString("") { html(it, forPreview) }
        if (node.tag == "latex") return if (forPreview) "<span class=\"math\" data-latex=\"${escape(text(node))}\"></span>" else "<span>${escape(text(node))}</span>"
        if (node.tag == "a" && !safeLink(node.attributes.getValue("href"))) return children
        val tag = when (node.tag) { "title" -> "h1"; "b" -> "strong"; "del" -> "s"; else -> node.tag }
        val attrs = buildString {
            node.attributes.forEach { (key, value) -> when (key) {
                "href", "rowspan", "colspan" -> append(" $key=\"${escape(value)}\"")
                "lang" -> if (Regex("[A-Za-z0-9_+.-]{1,40}").matches(value)) append(" class=\"language-${escape(value)}\"")
            } }
            if (tag == "span") {
                val styles = node.attributes.mapNotNull { (key, value) ->
                    val index = colorNames.indexOf(value)
                    when (key) { "text-color" -> "color:${foreground[index]}"; "background-color" -> "background-color:${background[index]}"; else -> null }
                }
                if (styles.isNotEmpty()) append(" style=\"${styles.joinToString(";")}\"")
            }
        }
        val content = if (tag in setOf("hr", "br")) "<$tag>" else "<$tag$attrs>$children</$tag>"
        return if (forPreview && tag in setOf("table", "pre")) "<div class=\"wide\">$content</div>" else content
    }

    private fun text(node: C2dNode): String = when (node) {
        is C2dText -> node.value
        is C2dElement -> when (node.tag) {
            "br" -> "\n"
            else -> node.children.joinToString("") { text(it) }
        }
    }
    fun plain(document: C2dDocument): String = document.blocks.joinToString("\n\n") { plainBlock(it) }
    private fun plainBlock(node: C2dElement, depth: Int = 0): String = when (node.tag) {
        "ul", "ol" -> node.children.filterIsInstance<C2dElement>().mapIndexed { index, li ->
            val prefix = "  ".repeat(depth) + if (node.tag == "ol") "${index + 1}. " else "• "
            prefix + buildString {
                var afterList = false
                li.children.forEach { child ->
                    if (child is C2dElement && child.tag in setOf("ul", "ol")) {
                        append("\n"); append(plainBlock(child, depth + 1)); afterList = true
                    } else {
                        val value = text(child)
                        if (afterList && value.isNotBlank()) { append("\n"); append("  ".repeat(depth + 1)); afterList = false }
                        append(value)
                    }
                }
            }.trim()
        }.joinToString("\n")
        "blockquote" -> node.children.filterIsInstance<C2dElement>().joinToString("\n\n") { plainBlock(it, depth) }
        "table" -> node.children.filterIsInstance<C2dElement>().flatMap { it.children.filterIsInstance<C2dElement>() }.joinToString("\n") { row -> row.children.filterIsInstance<C2dElement>().joinToString("\t") { text(it).trim() } }
        "hr" -> "────────"
        "pre" -> text(node.children.filterIsInstance<C2dElement>().single())
        else -> text(node).trim()
    }
}
