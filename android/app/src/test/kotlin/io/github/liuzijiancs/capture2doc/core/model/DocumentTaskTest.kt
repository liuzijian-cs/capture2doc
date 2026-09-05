package io.github.liuzijiancs.capture2doc.core.model

import org.junit.Assert.*
import org.junit.Test

class DocumentTaskTest {
    private val draft = DocumentTask("task", "doc", "https://example.test", 1, pageIds = listOf("A", "B"))

    @Test fun uploadTakesPriorityOverOcrWithoutLosingDraftIdentity() {
        val task = draft.copy(uploadedPageIds = setOf("A"), phase = DocumentPhase.OCR)
        assertEquals(TaskDisplayStatus.UPLOADING, task.displayStatus)
        assertTrue(task.isDraft)
    }
    @Test fun allUploadedThenProcessing() {
        assertEquals(TaskDisplayStatus.PROCESSING,
            draft.copy(uploadedPageIds = setOf("A", "B"), phase = DocumentPhase.OCR).displayStatus)
    }
    @Test fun blockingFailureIsNotHiddenByUploading() {
        assertEquals(TaskDisplayStatus.FAILED, draft.copy(error = "HTTP 401").displayStatus)
        assertEquals(TaskDisplayStatus.FAILED, draft.copy(localError = "原图损坏").displayStatus)
    }
    @Test fun unknownWordCountDoesNotBecomeZeroOrIntermediateCount() {
        assertNull(draft.copy(wordCount = 700).visibleWordCount)
    }
    @Test fun onlyAcceptedCompletedDocumentShowsWordCount() {
        val task = draft.copy(submittedPageIds = listOf("A", "B"), uploadedPageIds = setOf("A", "B"),
            submissionAccepted = true, phase = DocumentPhase.COMPLETED, wordCount = 700)
        assertEquals(TaskDisplayStatus.COMPLETED, task.displayStatus)
        assertEquals(700L, task.visibleWordCount)
        assertFalse(task.isDraft)
    }
    @Test fun hiddenTaskKeepsExecutionFacts() {
        val hidden = draft.copy(hidden = true)
        assertFalse(hidden.isVisible)
        assertEquals(draft.displayStatus, hidden.displayStatus)
        assertEquals(draft.effectivePageIds, hidden.effectivePageIds)
    }
    @Test fun finalSnapshotExcludesDeletedPagesAndPreservesOrder() {
        val task = draft.copy(pageIds = listOf("C", "A"), submittedPageIds = listOf("C", "A"),
            uploadedPageIds = setOf("A", "B", "C"), phase = DocumentPhase.OCR,
            pagePhases = mapOf("B" to PageRemotePhase.FAILED))
        assertEquals(listOf("C", "A"), task.effectivePageIds)
        assertEquals(TaskDisplayStatus.PROCESSING, task.displayStatus)
    }
    @Test fun failedReferencedOcrPageIsBlocking() {
        assertEquals(TaskDisplayStatus.FAILED, draft.copy(pagePhases = mapOf("A" to PageRemotePhase.FAILED)).displayStatus)
    }
    @Test fun idleDraftDoesNotPretendToProcess() {
        assertEquals(TaskDisplayStatus.IDLE, draft.copy(uploadedPageIds = setOf("A", "B")).displayStatus)
        assertEquals(TaskDisplayStatus.NOT_CONNECTED, draft.copy(documentId = null, legacy = true).displayStatus)
    }
    @Test fun searchMatchesTitleOnlyAndSortsByCreationTime() {
        val newer = draft.copy(taskId = "new", createdAt = 10, title = "Android 笔记")
        val older = draft.copy(title = "android 草稿", plainText = "not searchable")
        val hidden = newer.copy(taskId = "hidden", hidden = true)
        assertEquals(listOf("new", "task"), visibleTasks(listOf(older, hidden, newer), " ANDROID ").map { it.taskId })
        assertTrue(visibleTasks(listOf(older), "not searchable").isEmpty())
    }
    @Test fun offlineDraftIsListedWithoutInventingServerId() {
        val task = draft.copy(documentId = null)
        assertTrue(task.isVisible)
        assertNull(task.documentId)
        assertEquals(TaskDisplayStatus.NOT_CONNECTED, task.displayStatus)
    }
    @Test fun offlineSubmissionPreservesSnapshotWithoutPretendingToUpload() {
        val task = draft.copy(documentId = null, submittedPageIds = listOf("B", "A"))
        assertFalse(task.isDraft)
        assertEquals(listOf("B", "A"), task.effectivePageIds)
        assertEquals(TaskDisplayStatus.NOT_CONNECTED, task.displayStatus)
        assertNull(task.visibleWordCount)
    }
    @Test fun connectionLossDoesNotPretendToProcessOrDestroyOcrFacts() {
        val task = draft.copy(connectionIssue = "offline", phase = DocumentPhase.OCR)
        assertEquals(TaskDisplayStatus.NOT_CONNECTED, task.displayStatus)
        assertEquals(DocumentPhase.OCR, task.phase)
        assertNull(task.error)
        assertEquals(TaskDisplayStatus.UPLOADING, task.copy(connectionIssue = null).displayStatus)
    }
    @Test fun cachedCompletedDocumentRemainsReadableOffline() {
        val task = draft.copy(submittedPageIds = listOf("A", "B"), uploadedPageIds = setOf("A", "B"),
            submissionAccepted = true, phase = DocumentPhase.COMPLETED, wordCount = 700,
            plainText = "缓存内容", connectionIssue = "offline")
        assertEquals(TaskDisplayStatus.NOT_CONNECTED, task.displayStatus)
        assertTrue(task.hasCompletedDocument)
        assertEquals(700L, task.visibleWordCount)
        assertEquals("缓存内容", task.plainText)
    }
    @Test fun disconnectedDoesNotMaskBrokenOriginal() {
        val task = draft.copy(documentId = null, localError = "原图损坏")
        assertTrue(task.isDisconnected)
        assertEquals(TaskDisplayStatus.FAILED, task.displayStatus)
    }
}
