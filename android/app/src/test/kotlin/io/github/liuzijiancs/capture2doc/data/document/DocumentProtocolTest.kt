package io.github.liuzijiancs.capture2doc.data.document

import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class DocumentProtocolTest {
    private fun block(id: String, version: Long = 1) = PreviewBlock(id, version, "<p>$id 第 $version 版</p>")
    private fun initial() = PreviewState("doc", listOf(block("a"), block("b"), block("c")), 1, 10)
    private fun patch(after: String?, remove: List<String>, blocks: List<PreviewBlock>, base: Long = 1, id: Long = 11) = DocumentEvent(id, "blocks.patch",
        JSONObject().put("documentId", "doc").put("baseRevision", base).put("revision", base + 1)
            .put("afterBlockId", after ?: JSONObject.NULL).put("removeBlockIds", JSONArray(remove))
            .put("blocks", JSONArray(blocks.map { it.json() })))
    @Test fun insertsInMiddleWithoutChangingExistingBlocks() {
        val old = initial()
        val next = PreviewReducer.apply(old, patch("a", emptyList(), listOf(block("x"))))
        assertEquals(listOf("a", "x", "b", "c"), next.blocks.map { it.blockId })
        assertSame(old.blocks[0], next.blocks[0])
        assertEquals(2L, next.revision)
        assertEquals(11L, next.cursor)
    }
    @Test fun splitMergeReplaceAndWithdrawRespectContiguousRanges() {
        var state = initial()
        state = PreviewReducer.apply(state, patch("a", listOf("b"), listOf(block("b", 2), block("x"))))
        assertEquals(listOf("a", "b", "x", "c"), state.blocks.map { it.blockId })
        state = PreviewReducer.apply(state, patch("a", listOf("b", "x"), listOf(block("merged")), 2, 12))
        state = PreviewReducer.apply(state, patch("merged", listOf("c"), listOf(block("c", 2)), 3, 13))
        state = PreviewReducer.apply(state, patch("merged", listOf("c"), emptyList(), 4, 14))
        assertEquals(listOf("a", "merged"), state.blocks.map { it.blockId })
    }
    @Test fun duplicateEventIsNotAppliedTwice() {
        val event = patch(null, emptyList(), listOf(block("x")))
        val next = PreviewReducer.apply(initial(), event)
        assertEquals(next, PreviewReducer.apply(next, event))
    }
    @Test fun revisionGapsMissingAnchorAndNoncontiguousRemovalReset() {
        listOf(patch("a", listOf("b", "a"), emptyList()), patch("unknown", emptyList(), emptyList()),
            patch(null, emptyList(), emptyList(), base = 0), patch(null, emptyList(), emptyList(), id = 12),
            patch("a", listOf("b"), listOf(block("a")))).forEach { event ->
            try { PreviewReducer.apply(initial(), event); fail("inconsistent patch accepted") } catch (_: PreviewResetRequired) { }
        }
    }
    @Test fun snapshotReplacesCacheAndAtomicallyRoundTripsCursor() {
        val snapshot = PreviewState("doc", listOf(block("fresh")), 7, 35, DocumentPhase.ASSEMBLING, 2, 1, 3)
        val restored = PreviewReducer.apply(initial().copy(cursor = null), DocumentEvent(35, "document.snapshot", snapshot.json()))
        assertEquals(snapshot, restored)
        assertEquals(restored, PreviewReducer.restore(JSONObject(restored.json().toString()), "doc"))
    }
    @Test fun terminalCannotRegressFromOldProgressOrSnapshot() {
        val completed = PreviewReducer.apply(initial(), DocumentEvent(11, "document.completed", JSONObject().put("documentId", "doc").put("status", "COMPLETED")))
        assertEquals(completed, PreviewReducer.apply(completed, DocumentEvent(12, "document.snapshot", initial().json())))
        assertEquals(completed, PreviewReducer.apply(completed, DocumentEvent(12, "document.progress", JSONObject().put("documentId", "doc"))))
    }
    @Test fun failurePreservesPreview() {
        val failed = PreviewReducer.apply(initial(), DocumentEvent(11, "document.failed", JSONObject().put("documentId", "doc").put("status", "FAILED").put("message", "未完成")))
        assertEquals(initial().blocks, failed.blocks)
        assertEquals(DocumentPhase.FAILED, failed.status)
        assertEquals("未完成", failed.message)
    }
    @Test fun emptyFinalIsValidButHasNoCopyableDocument() {
        assertNull(FinalDocumentResult("doc", DocumentPhase.COMPLETED, "文档", 0, null, null).validatedDocument())
    }
    @Test fun hashIsCheckedBeforeXmlParsingAndWithoutReserialization() {
        val xml = "<document xmlns='urn:capture2doc:c2d:1' schema-version='0.1'>\r\n<p>中 &amp; 文</p></document>"
        val result = FinalDocumentResult("doc", DocumentPhase.COMPLETED, "标题", 3, xml, sha256(xml.toByteArray()))
        assertNotNull(result.validatedDocument())
        assertNotNull(FinalDocumentResult.parse(JSONObject(result.json().toString()), "doc").validatedDocument())
        try { result.copy(c2dXml = xml.replace("\r\n", "\n")).validatedDocument(); fail("altered bytes accepted") }
        catch (e: IllegalArgumentException) { assertTrue(e.message!!.contains("摘要")) }
    }
}
