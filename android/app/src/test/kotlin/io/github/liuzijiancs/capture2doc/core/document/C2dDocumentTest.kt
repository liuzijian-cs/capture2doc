package io.github.liuzijiancs.capture2doc.core.document

import org.junit.Assert.*
import org.junit.Test

class C2dDocumentTest {
    private fun fixture() = requireNotNull(javaClass.classLoader!!.getResourceAsStream("all-tags.xml")).bufferedReader().use { it.readText() }
    @Test fun everyV01TagRendersAndCopiesWithoutUiDecorations() {
        val document = C2dParser.document(fixture())
        val html = C2dRenderer.html(document)
        val plain = C2dRenderer.plain(document)
        listOf("h1", "h2", "h3", "h4", "h5", "h6", "p", "blockquote", "ul", "ol", "li", "table", "thead", "tbody", "tr", "td", "th", "pre", "code", "hr", "strong", "em", "u", "s", "a", "br", "span").forEach { assertTrue("missing $it", html.contains("<$it")) }
        assertTrue(html.contains("rowspan=\"2\"")); assertTrue(html.contains("colspan=\"3\""))
        assertTrue(plain.startsWith("流式文档 · 中文 & 特殊字符"))
        assertTrue(plain.contains("  1. 嵌套编号")); assertTrue(plain.contains("甲\t乙"))
        assertTrue(plain.contains("    println(\"中文 & <内容>\")"))
        assertTrue(plain.contains("\\unsupportedMacro{x}"))
        assertFalse(html.contains("data-latex")); assertTrue(C2dRenderer.html(document, true).contains("data-latex"))
        assertTrue(html.contains("&lt;script&gt;")); assertFalse(html.contains("<script>"))
        assertFalse(plain.contains("等待"))
    }
    @Test fun sevenForegroundAndBackgroundMappingsAreIndependent() {
        val html = C2dRenderer.html(C2dParser.document(fixture()))
        listOf("#D83931", "#B96500", "#8A6A00", "#16803C", "#245BDB", "#7F3FBF", "#646A73",
            "#FDE2E2", "#FFE8CC", "#FFF3B0", "#DCF4E4", "#E1EBFF", "#EFE2FA", "#ECEEF0").forEach { assertTrue(it, html.contains(it)) }
    }
    @Test fun namespacedBlocksAndMixedTextArePreserved() {
        val node = C2dParser.block("<c:p xmlns:c='urn:capture2doc:c2d:1'>前<b xmlns='urn:capture2doc:c2d:1'>粗体</b>后</c:p>")
        assertEquals("<p>前<strong>粗体</strong>后</p>", C2dRenderer.html(node))
    }
    @Test fun unsafeLinksNeverBecomeExecutableHtml() {
        val node = C2dParser.block("<p><a href='javascript:alert(1)'>可读文字</a></p>")
        assertEquals("<p>可读文字</p>", C2dRenderer.html(node))
        assertFalse(C2dRenderer.safeLink("https://secret@example.com"))
        assertFalse(C2dRenderer.safeLink("file:///etc/passwd"))
    }
    @Test fun illegalXmlStructuresAreRejected() {
        listOf("<!DOCTYPE p [<!ENTITY x SYSTEM 'file:///etc/passwd'>]><p>&x;</p>", "<p xmlns='evil'>x</p>",
            "<p><!--comment-->x</p>", "<p><?pi x?>x</p>", "<p><script>x</script></p>", "<p><span>x</span></p>",
            "<p><span text-color='#ffffff'>x</span></p>", "<p><b onclick='x'>x</b></p>", "<p><em><b>x</b></em></p>",
            "<p><latex><b>x</b></latex></p>", "<pre><code><span text-color='red'>x</span></code></pre>",
            "<p>x", "<p>  </p>", "<p>\u200d</p>", "<p>x</p><p>y</p>").forEach { xml ->
            try { C2dParser.block(xml); fail("accepted: $xml") } catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun invalidTableGridsAndOutOfGroupSpansAreRejected() {
        listOf("<tr><td rowspan='2'>x</td></tr>", "<tr><td>x</td><td>y</td></tr><tr><td>z</td></tr>", "<tr/>").forEach { rows ->
            try { C2dParser.block("<table><tbody>$rows</tbody></table>"); fail("invalid grid accepted") } catch (_: IllegalArgumentException) { }
        }
        assertNotNull(C2dParser.block("<table><tbody><tr><td rowspan='2'>x</td></tr><tr/></tbody></table>"))
    }
    @Test fun unsupportedVersionIsRejected() {
        try { C2dParser.document(fixture().replace("schema-version=\"0.1\"", "schema-version=\"9.0\"")); fail("unknown version accepted") } catch (_: IllegalArgumentException) { }
    }
}
