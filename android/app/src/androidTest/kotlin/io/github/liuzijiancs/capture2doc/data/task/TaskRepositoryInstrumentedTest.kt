package io.github.liuzijiancs.capture2doc.data.task

import android.graphics.Bitmap
import androidx.test.platform.app.InstrumentationRegistry
import io.github.liuzijiancs.capture2doc.core.model.*
import java.io.File
import java.io.IOException
import java.nio.file.Files
import kotlinx.coroutines.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class TaskRepositoryInstrumentedTest {
    @Test fun jsonRoundTripIncludesSnapshotHiddenFlagAndServiceIdentity() {
        val task = DocumentTask("task", "remote", "https://service.test", 100, title = "标题",
            pageIds = listOf("C", "A"), submittedPageIds = listOf("C", "A"), uploadedPageIds = setOf("A"),
            phase = DocumentPhase.OCR, hidden = true, pagePhases = mapOf("A" to PageRemotePhase.COMPLETED))
        assertEquals(listOf(task), decodeTasks(JSONObject(encodeTasks(listOf(task)).toString())))
    }

    @Test fun offlineNewTasksHaveDistinctVisibleIdentitiesAndSurviveRestart() = fixture { root, scope, repository ->
        val first = repository.createLocalTask("")
        val second = repository.createLocalTask("")
        assertNotEquals(first.taskId, second.taskId)
        addPage(repository, first.taskId, "same-page")
        addPage(repository, second.taskId, "same-page")
        val recovered = TaskRepository(root, scope)
        recovered.initialize()
        assertEquals(2, recovered.tasks.value.size)
        recovered.tasks.value.forEach {
            assertTrue(it.isVisible)
            assertNull(it.documentId)
            assertEquals(listOf("same-page"), recovered.openRepository(it.taskId).draft.value!!.pages.map { page -> page.pageId })
        }
        assertNotEquals(repository.draftRepository(first.taskId).pageFiles("same-page").originalPath,
            repository.draftRepository(second.taskId).pageFiles("same-page").originalPath)
    }

    @Test fun offlineFinishRecoversAndLaterLinksBeforeUploadingFrozenOrder() = fixture { root, scope, repository ->
        val id = repository.createLocalTask("").taskId
        addPage(repository, id, "A")
        addPage(repository, id, "C")
        repository.draftRepository(id).movePage("C", 0)
        repository.submit(id)
        val recovered = TaskRepository(root, scope)
        recovered.initialize()
        assertNull(recovered.task(id).documentId)
        assertEquals(listOf("C", "A"), recovered.task(id).submittedPageIds)
        val service = FakeService()
        assertEquals(SyncOutcome.DONE, TaskSynchronizer(recovered, service).sync(id))
        assertTrue(service.calls.isEmpty())
        assertTrue(service.createIds.isEmpty())
        recovered.bindUnconfiguredService(id, "https://service.test")
        recovered.hide(setOf(id))
        assertEquals(SyncOutcome.DONE, TaskSynchronizer(recovered, service).sync(id))
        assertEquals(listOf(id), service.createIds)
        assertEquals("remote", recovered.task(id).documentId)
        assertEquals(listOf("upload:C", "upload:A", "finalize:C,A", "status"), service.calls)
        assertFalse(recovered.task(id).isVisible)
    }

    @Test fun failedCreateKeepsLocalPhotosAndRetriesSameIdentityAfterRestart() = fixture { root, scope, repository ->
        val id = repository.createLocalTask("https://service.test").taskId
        addPage(repository, id, "A")
        repository.submit(id)
        val service = FakeService().apply { failCreate = true }
        assertEquals(SyncOutcome.RETRY, TaskSynchronizer(repository, service).sync(id))
        assertNull(repository.task(id).documentId)
        assertNull(repository.task(id).error)
        assertEquals(TaskDisplayStatus.NOT_CONNECTED, repository.task(id).displayStatus)
        assertTrue(service.calls.isEmpty())
        val recovered = TaskRepository(root, scope)
        recovered.initialize()
        assertNotNull(recovered.task(id).connectionIssue)
        assertTrue(File(recovered.openRepository(id).pageFiles("A").originalPath).isFile)
        service.failCreate = false
        assertEquals(SyncOutcome.DONE, TaskSynchronizer(recovered, service).sync(id))
        assertEquals(listOf(id, id), service.createIds)
        assertNull(recovered.task(id).connectionIssue)
    }

    @Test fun uploadRetryDoesNotRecreateConfirmedDocument() = fixture { _, _, repository ->
        val id = repository.createLocalTask("https://service.test").taskId
        addPage(repository, id, "A")
        repository.submit(id)
        val service = FakeService().apply { failPage = "A" }
        val synchronizer = TaskSynchronizer(repository, service)
        assertEquals(SyncOutcome.RETRY, synchronizer.sync(id))
        assertEquals("remote", repository.task(id).documentId)
        service.failPage = null
        assertEquals(SyncOutcome.DONE, synchronizer.sync(id))
        assertEquals(listOf(id), service.createIds)
    }

    @Test fun endpointCanOnlyBeBoundOnce() = fixture { _, _, repository ->
        val id = repository.createLocalTask("").taskId
        repository.bindUnconfiguredService(id, "https://original.test")
        repository.bindUnconfiguredService(id, "https://different.test")
        assertEquals("https://original.test", repository.task(id).baseUrl)
    }

    @Test fun multipleDraftsHaveIsolatedFilesAndSurviveRecreation() = fixture { root, scope, repository ->
        val a = createTask(repository, "doc-a")
        val b = createTask(repository, "doc-b")
        addPage(repository, a, "same-page-id")
        addPage(repository, b, "same-page-id")
        val aFile = repository.draftRepository(a).pageFiles("same-page-id").originalPath
        val bFile = repository.draftRepository(b).pageFiles("same-page-id").originalPath
        assertNotEquals(aFile, bFile)
        repository.draftRepository(a).deletePage("same-page-id")
        assertTrue(File(bFile).isFile)
        val recovered = TaskRepository(root, scope)
        recovered.initialize()
        recovered.openRepository(a)
        recovered.openRepository(b)
        assertEquals(2, recovered.tasks.value.size)
        assertTrue(recovered.task(a).pageIds.isEmpty())
        assertEquals(listOf("same-page-id"), recovered.task(b).pageIds)
    }

    @Test fun hiddenTaskStillUploadsAndFinalizesInFrozenOrder() = fixture { root, scope, repository ->
        val id = createTask(repository, "remote")
        addPage(repository, id, "A")
        addPage(repository, id, "B")
        addPage(repository, id, "C")
        repository.draftRepository(id).deletePage("B")
        repository.draftRepository(id).movePage("C", 0)
        repository.refreshPages(id)
        repository.submit(id)
        assertEquals(listOf("C", "A"), repository.task(id).submittedPageIds)
        assertFalse(repository.task(id).submissionAccepted)
        repository.hide(setOf(id))
        val service = FakeService()
        assertEquals(SyncOutcome.DONE, TaskSynchronizer(repository, service).sync(id))
        assertEquals(listOf("upload:C", "upload:A", "finalize:C,A", "status"), service.calls)
        val recovered = TaskRepository(root, scope)
        recovered.initialize()
        assertFalse(recovered.task(id).isVisible)
        assertEquals(42L, recovered.task(id).visibleWordCount)
        assertTrue(File(repository.draftRepository(id).pageFiles("A").originalPath).isFile)
    }

    @Test fun retryOnlyUploadsUnconfirmedPagesAndKeepsImages() = fixture { _, _, repository ->
        val id = createTask(repository, "remote")
        addPage(repository, id, "A")
        addPage(repository, id, "B")
        repository.submit(id)
        val service = FakeService().apply { failPage = "B" }
        val synchronizer = TaskSynchronizer(repository, service)
        assertEquals(SyncOutcome.RETRY, synchronizer.sync(id))
        assertEquals(setOf("A"), repository.task(id).uploadedPageIds)
        assertNotNull(repository.task(id).connectionIssue)
        assertNull(repository.task(id).error)
        assertTrue(File(repository.draftRepository(id).pageFiles("B").originalPath).isFile)
        service.failPage = null
        assertEquals(SyncOutcome.DONE, synchronizer.sync(id))
        assertEquals(1, service.calls.count { it == "upload:A" })
        assertEquals(2, service.calls.count { it == "upload:B" })
    }

    @Test fun emptySubmissionRejectedButEmptyRemoteDraftPreserved() = fixture { root, scope, repository ->
        val id = createTask(repository, "remote")
        repository.openRepository(id)
        try { repository.submit(id); fail("empty submission accepted") } catch (_: IllegalArgumentException) { }
        val recovered = TaskRepository(root, scope)
        recovered.initialize()
        recovered.openRepository(id)
        assertTrue(recovered.task(id).isDraft)
        assertEquals("remote", recovered.task(id).documentId)
    }

    @Test fun legacyDraftIsRegisteredInPlaceAndNeverImportsProbeCaptures() = fixture { root, scope, _ ->
        val old = io.github.liuzijiancs.capture2doc.data.capture2doc.draft.ScanDraftRepository(root)
        old.createNewDraft()
        val page = old.reservePage("legacy-page", false)
        writeJpeg(File(page.originalPath))
        val probe = File(root, "captures/keep.jpg").apply { parentFile!!.mkdirs(); writeText("legacy probe") }
        val repository = TaskRepository(root, scope)
        repository.initialize()
        repository.openRepository("legacy")
        assertTrue(repository.task("legacy").isDraft)
        assertNull(repository.task("legacy").documentId)
        assertEquals(page.originalPath, repository.draftRepository("legacy").pageFiles("legacy-page").originalPath)
        assertEquals("legacy probe", probe.readText())
    }

    @Test fun corruptCatalogIsNotSilentlyOverwritten() = fixture { root, scope, repository ->
        createTask(repository, "remote")
        val manifest = File(root, "document_tasks/tasks.json")
        manifest.writeText("broken-json")
        try { TaskRepository(root, scope).initialize(); fail("corruption ignored") } catch (_: org.json.JSONException) { }
        assertEquals("broken-json", manifest.readText())
    }

    @Test fun interruptedOriginalRecoversWithoutReplayingShutter() = fixture { root, scope, repository ->
        val id = createTask(repository, "remote")
        val draft = repository.openRepository(id)
        val files = draft.reservePage("original-only", false)
        writeJpeg(File(files.originalPath))
        draft.reservePage("missing-original", false)
        val recovered = TaskRepository(root, scope)
        recovered.initialize()
        val pages = recovered.openRepository(id).draft.value!!.pages
        assertEquals(listOf("original-only", "missing-original"), pages.map { it.pageId })
        assertEquals(ScanPageState.READY, pages[0].state)
        assertEquals(ScanPageState.FAILED, pages[1].state)
    }

    private fun fixture(block: suspend (File, CoroutineScope, TaskRepository) -> Unit) = runBlocking {
        val cache = InstrumentationRegistry.getInstrumentation().targetContext.cacheDir
        val root = Files.createTempDirectory(cache.toPath(), "task-test-").toFile()
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try { block(root, scope, TaskRepository(root, scope)) }
        finally { scope.coroutineContext[Job]!!.cancelAndJoin(); root.deleteRecursively() }
    }

    private suspend fun createTask(repository: TaskRepository, remoteId: String): String {
        val task = repository.createLocalTask("https://service.test")
        repository.update(task.taskId) { it.copy(documentId = remoteId) }
        repository.openRepository(task.taskId)
        return task.taskId
    }

    private suspend fun addPage(repository: TaskRepository, taskId: String, pageId: String) {
        val draft = repository.openRepository(taskId)
        val files = draft.reservePage(pageId, false)
        writeJpeg(File(files.originalPath))
        draft.markNormalizing(pageId)
        draft.normalizePage(pageId)
        draft.markReady(pageId)
        repository.refreshPages(taskId)
    }

    private fun writeJpeg(file: File) {
        val bitmap = Bitmap.createBitmap(48, 64, Bitmap.Config.ARGB_8888)
        try { file.outputStream().use { assertTrue(bitmap.compress(Bitmap.CompressFormat.JPEG, 90, it)) } }
        finally { bitmap.recycle() }
    }

    private class FakeService : DocumentService {
        val calls = mutableListOf<String>()
        val createIds = mutableListOf<String>()
        var failCreate = false
        var failPage: String? = null
        var finalized = false
        override suspend fun create(baseUrl: String, clientTaskId: String): String {
            createIds += clientTaskId
            if (failCreate) throw IOException("offline")
            return "remote"
        }
        override suspend fun upload(baseUrl: String, documentId: String, pageId: String, image: File) {
            assertEquals("normalized_1280.jpg", image.name)
            calls += "upload:$pageId"
            if (pageId == failPage) throw IOException("offline")
        }
        override suspend fun finalize(baseUrl: String, documentId: String, clientTaskId: String, pageIds: List<String>) {
            calls += "finalize:${pageIds.joinToString(",")}"
            finalized = true
        }
        override suspend fun status(baseUrl: String, documentId: String): RemoteDocument {
            calls += "status"
            return RemoteDocument(if (finalized) DocumentPhase.COMPLETED else DocumentPhase.IDLE,
                if (finalized) "最终标题" else null, if (finalized) 42 else null,
                if (finalized) "文档内容" else null, null, emptyMap())
        }
    }
}
