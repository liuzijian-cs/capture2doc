package io.github.liuzijiancs.capture2doc.data.document

import androidx.test.platform.app.InstrumentationRegistry
import androidx.compose.material3.Text
import androidx.compose.ui.test.junit4.createComposeRule
import io.github.liuzijiancs.capture2doc.core.document.C2dParser
import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import io.github.liuzijiancs.capture2doc.data.settings.ConnectionSettings
import io.github.liuzijiancs.capture2doc.data.task.TaskRepository
import io.github.liuzijiancs.capture2doc.data.task.DocumentService
import io.github.liuzijiancs.capture2doc.data.task.ServiceException
import io.github.liuzijiancs.capture2doc.data.task.TaskSynchronizer
import io.github.liuzijiancs.capture2doc.data.task.SyncOutcome
import io.github.liuzijiancs.capture2doc.ui.document.DocumentReaderSession
import io.github.liuzijiancs.capture2doc.ui.document.readerPayload
import io.github.liuzijiancs.capture2doc.ui.document.CopyMode
import io.github.liuzijiancs.capture2doc.ui.document.DocumentClipboard
import java.io.File
import java.nio.file.Files
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.emptyFlow
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.Before
import org.junit.Rule

class DocumentContentInstrumentedTest {
    // Keep the instrumented process foreground while testing large Parcels/Keystore. Some
    // devices freeze a headless instrumented process after the preceding UI test closes.
    @get:Rule val foreground = createComposeRule()
    @Before fun keepForeground() { foreground.setContent { Text("正在验证文档缓存与剪贴板") } }
    private val xml = "<document xmlns='urn:capture2doc:c2d:1' schema-version='0.1'><title>最终标题</title><p>中 &amp; 文</p></document>"
    private fun result() = FinalDocumentResult("remote", DocumentPhase.COMPLETED, "最终标题", 5, xml, sha256(xml.toByteArray()))
    private fun snapshot() = DocumentEvent(10, "document.snapshot", PreviewState("remote", listOf(PreviewBlock("a", 1, "<p>预览</p>")), 1).json())
    private fun patch() = DocumentEvent(11, "blocks.patch", JSONObject().put("documentId", "remote").put("baseRevision", 1).put("revision", 2)
        .put("afterBlockId", "a").put("removeBlockIds", JSONArray()).put("blocks", JSONArray().put(PreviewBlock("b", 1, "<p>后续</p>").json())))

    @Test fun previewCursorRevisionAndBlocksRecoverWithoutRewritingCatalog() = fixture { root, tasks, id ->
        val contents = DocumentContentRepository(root, tasks)
        contents.load(id, "remote")
        val catalog = File(root, "document_tasks/tasks.json").readText()
        val generation = contents.begin(id)
        contents.accept(id, generation, snapshot())
        contents.accept(id, generation, patch())
        contents.accept(id, generation, patch())
        assertEquals(catalog, File(root, "document_tasks/tasks.json").readText())
        val recovered = DocumentContentRepository(root, tasks)
        recovered.load(id, "remote")
        assertEquals(contents.observe(id).value.preview, recovered.observe(id).value.preview)
        assertEquals(11L, recovered.observe(id).value.preview!!.cursor)
        assertEquals(2, recovered.observe(id).value.preview!!.blocks.size)
    }
    @Test fun oldSessionCallbackCannotOverwriteNewSession() = fixture { root, tasks, id ->
        val contents = DocumentContentRepository(root, tasks)
        val old = contents.begin(id)
        contents.accept(id, old, snapshot())
        val fresh = contents.begin(id)
        contents.accept(id, old, patch())
        assertEquals(1L, contents.observe(id).value.preview!!.revision)
        contents.end(id, old)
        contents.accept(id, fresh, patch())
        assertEquals(2L, contents.observe(id).value.preview!!.revision)
    }
    @Test fun finalResultRestoresSummaryAndRemainsReadableOffline() = fixture { root, tasks, id ->
        val contents = DocumentContentRepository(root, tasks)
        contents.storeResult(id, result())
        tasks.update(id) { it.copy(title = "旧标题", wordCount = null, phase = DocumentPhase.OCR) }
        val recovered = DocumentContentRepository(root, tasks)
        recovered.load(id, "remote")
        assertEquals("最终标题", tasks.task(id).title)
        assertEquals(5L, tasks.task(id).wordCount)
        assertEquals(DocumentPhase.COMPLETED, tasks.task(id).phase)
        assertNotNull(recovered.observe(id).value.document)
        assertEquals(xml, recovered.observe(id).value.result!!.c2dXml)
    }
    @Test fun coldWorkerRestoresFinalCacheWithoutNetworkRequest() = fixture { root, tasks, id ->
        DocumentContentRepository(root, tasks).storeResult(id, result())
        val contents = DocumentContentRepository(root, tasks)
        val service = ReaderService(result()) { _, _ -> error("completed cache must not stream") }
        assertEquals(SyncOutcome.DONE, TaskSynchronizer(tasks, service, contents).sync(id))
        assertEquals(0, service.gets)
        assertNotNull(contents.observe(id).value.document)
    }
    @Test fun badDigestDoesNotReplacePreviewOrVerifiedResult() = fixture { root, tasks, id ->
        val contents = DocumentContentRepository(root, tasks)
        contents.accept(id, contents.begin(id), snapshot())
        try { contents.storeResult(id, result().copy(sha256 = "0".repeat(64))); fail("bad hash accepted") } catch (_: IllegalArgumentException) { }
        assertNull(contents.observe(id).value.result)
        assertEquals("a", contents.observe(id).value.preview!!.blocks.single().blockId)
        contents.storeResult(id, result())
        val saved = File(root, "document_tasks/$id/result.json").readText()
        try { contents.storeResult(id, result().copy(c2dXml = "broken")); fail("bad result accepted") } catch (_: IllegalArgumentException) { }
        assertEquals(saved, File(root, "document_tasks/$id/result.json").readText())
    }
    @Test fun lostOrCorruptPreviewStartsWithoutResumeCursor() = fixture { root, tasks, id ->
        File(root, "document_tasks/$id/preview.json").apply { parentFile!!.mkdirs(); writeText("broken") }
        val contents = DocumentContentRepository(root, tasks)
        contents.load(id, "remote")
        assertNull(contents.observe(id).value.preview)
        contents.accept(id, contents.begin(id), snapshot())
        assertEquals(10L, contents.observe(id).value.preview!!.cursor)
    }
    @Test fun terminalSnapshotAnd204BothFetchAndVerifyFinalGet() = fixture { root, tasks, id ->
        listOf(true, false).forEach { terminalSnapshot ->
            // Use independent task directories, as a terminal result is immutable.
            val taskId = if (terminalSnapshot) id else tasks.createLocalTask("https://service.test").taskId.also { new -> tasks.update(new) { it.copy(documentId = "remote") } }
            val contents = DocumentContentRepository(root, tasks)
            val service = ReaderService(result()) { _, _ -> if (terminalSnapshot) flowOf(DocumentEvent(10, "document.snapshot", PreviewState("remote", status = DocumentPhase.COMPLETED).json())) else emptyFlow() }
            DocumentReaderSession(service, contents, tasks, taskId).follow(tasks.task(taskId))
            assertEquals(1, service.gets)
            assertNotNull(contents.observe(taskId).value.document)
        }
    }
    @Test fun cursor409ResetsThenTerminalFetchUsesNormalGet() = fixture { root, tasks, id ->
        val contents = DocumentContentRepository(root, tasks)
        contents.accept(id, contents.begin(id), snapshot())
        val cursors = mutableListOf<Long?>()
        val service = ReaderService(result()) { cursor, attempt ->
            cursors += cursor
            if (attempt == 1) flow { throw ServiceException("stale cursor", false, 409) }
            else flowOf(DocumentEvent(12, "document.snapshot", PreviewState("remote", status = DocumentPhase.COMPLETED).json()))
        }
        withTimeout(5_000) { DocumentReaderSession(service, contents, tasks, id).follow(tasks.task(id)) }
        assertEquals(listOf(10L, null), cursors)
        assertEquals(1, service.gets)
    }
    @Test fun terminalBadHashKeepsPreviewAndEmptyFinalHidesIt() = fixture { root, tasks, id ->
        val contents = DocumentContentRepository(root, tasks)
        val terminal = PreviewState("remote", snapshot().data.getJSONArray("blocks").let { listOf(PreviewBlock.parse(it.getJSONObject(0))) }, status = DocumentPhase.COMPLETED)
        val service = ReaderService(result().copy(sha256 = "0".repeat(64))) { _, _ -> flowOf(DocumentEvent(11, "document.snapshot", terminal.json())) }
        val session = DocumentReaderSession(service, contents, tasks, id)
        session.follow(tasks.task(id))
        assertNull(contents.observe(id).value.result)
        assertEquals(1, contents.observe(id).value.preview!!.blocks.size)
        assertTrue(session.connection.value.message!!.contains("摘要"))
        contents.storeResult(id, FinalDocumentResult("remote", DocumentPhase.COMPLETED, "文档", 0, null, null))
        val payload = JSONObject(readerPayload(contents.observe(id).value, null, false, false))
        assertEquals(0, payload.getJSONArray("blocks").length())
        assertFalse(payload.getBoolean("ready"))
    }
    private class ReaderService(val result: FinalDocumentResult, val stream: (Long?, Int) -> Flow<DocumentEvent>) : DocumentService {
        var gets = 0
        var streams = 0
        override fun events(baseUrl: String, documentId: String, cursor: Long?) = stream(cursor, ++streams)
        override suspend fun status(baseUrl: String, documentId: String): FinalDocumentResult { gets++; return result }
        override suspend fun create(baseUrl: String, clientTaskId: String): String = error("reader must not create")
        override suspend fun upload(baseUrl: String, documentId: String, pageId: String, image: File): Unit = error("reader must not upload")
        override suspend fun finalize(baseUrl: String, documentId: String, clientTaskId: String, pageIds: List<String>): Unit = error("reader must not finalize")
    }
    @Test fun keystoreStorageIsEncryptedAndTokensRemainBoundToAddress() = fixture { root, _, _ ->
        val settings = ConnectionSettings(root)
        settings.initialize()
        settings.save("https://first.example/", "test-token-one")
        settings.save("https://second.example", "test-token-two")
        val raw = File(root, "connection-settings.enc").readText()
        assertFalse(raw.contains("test-token")); assertFalse(raw.contains("first.example"))
        val recovered = ConnectionSettings(root)
        recovered.initialize()
        assertEquals("https://second.example", recovered.state.value.baseUrl)
        assertEquals("test-token-one", recovered.token("https://first.example"))
        assertEquals("test-token-two", recovered.token("https://second.example/"))
    }
    @Test fun htmlClipboardIncludesFallbackAndActualParcelBudget() {
        val fixture = InstrumentationRegistry.getInstrumentation().context.assets.open("all-tags.xml").bufferedReader().use { it.readText() }
        val document = C2dParser.document(fixture)
        val siyuan = DocumentClipboard.clip(document, CopyMode.SIYUAN)
        val feishu = DocumentClipboard.clip(document, CopyMode.FEISHU)
        assertEquals(siyuan.getItemAt(0).htmlText, feishu.getItemAt(0).htmlText)
        assertTrue(siyuan.description.hasMimeType("text/html"))
        assertTrue(siyuan.description.hasMimeType("text/plain"))
        assertTrue(siyuan.getItemAt(0).text.startsWith("流式文档"))
        assertTrue(DocumentClipboard.serializedBytes(siyuan) < DocumentClipboard.MAX_BYTES)
        val huge = C2dParser.document("<document xmlns='urn:capture2doc:c2d:1' schema-version='0.1'><p>${"中".repeat(300_000)}</p></document>")
        val oversized = DocumentClipboard.clip(huge, CopyMode.SIYUAN)
        assertTrue(DocumentClipboard.serializedBytes(oversized) > DocumentClipboard.MAX_BYTES)
        try { DocumentClipboard.write(InstrumentationRegistry.getInstrumentation().targetContext, oversized); fail("oversize accepted") }
        catch (_: IllegalArgumentException) { }
    }
    private fun fixture(block: suspend (File, TaskRepository, String) -> Unit) = runBlocking {
        val root = Files.createTempDirectory(InstrumentationRegistry.getInstrumentation().targetContext.cacheDir.toPath(), "document-content-test-").toFile()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            val tasks = TaskRepository(root, scope)
            val id = tasks.createLocalTask("https://service.test").taskId
            tasks.update(id) { it.copy(documentId = "remote") }
            block(root, tasks, id)
        } finally { scope.coroutineContext[Job]!!.cancelAndJoin(); root.deleteRecursively() }
    }
}
