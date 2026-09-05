package io.github.liuzijiancs.capture2doc.data.task

import io.github.liuzijiancs.capture2doc.core.model.*
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class TaskCodecTest {
    private val task = DocumentTask("task", "remote", "https://server.test", 12,
        pageIds = listOf("C", "A"), submittedPageIds = listOf("C", "A"), uploadedPageIds = setOf("A"),
        phase = DocumentPhase.OCR, hidden = true, pagePhases = mapOf("A" to PageRemotePhase.COMPLETED))

    @Test fun roundTripPreservesOrderHiddenAndEndpoint() {
        assertEquals(listOf(task), decodeTasks(JSONObject(encodeTasks(listOf(task)).toString())))
    }
    @Test fun nullSubmissionRemainsEditableDraft() {
        assertTrue(decodeTasks(encodeTasks(listOf(task.copy(submittedPageIds = null)))).single().isDraft)
    }
    @Test fun offlineSnapshotAndConnectionIssueSurviveRoundTrip() {
        val offline = task.copy(documentId = null, baseUrl = "", connectionIssue = "连接超时", hidden = false)
        val decoded = decodeTasks(JSONObject(encodeTasks(listOf(offline)).toString())).single()
        assertEquals(offline, decoded)
        assertTrue(decoded.isVisible)
        assertEquals(TaskDisplayStatus.NOT_CONNECTED, decoded.displayStatus)
    }
    @Test fun olderManifestWithoutConnectionFieldStillLoads() {
        val json = encodeTasks(listOf(task))
        json.getJSONArray("tasks").getJSONObject(0).remove("connectionIssue")
        assertEquals(listOf(task), decodeTasks(json))
    }
    @Test fun rejectsUnknownVersion() {
        assertThrows(IllegalArgumentException::class.java) { decodeTasks(encodeTasks(listOf(task)).put("schemaVersion", 99)) }
    }
    @Test fun rejectsDuplicateTasks() {
        assertThrows(IllegalArgumentException::class.java) { decodeTasks(encodeTasks(listOf(task, task))) }
    }
    @Test fun rejectsUnsafeLocalIds() {
        assertThrows(IllegalArgumentException::class.java) { decodeTasks(encodeTasks(listOf(task.copy(taskId = "../elsewhere")))) }
    }
    @Test fun rejectsDuplicateFinalPages() {
        assertThrows(IllegalArgumentException::class.java) { decodeTasks(encodeTasks(listOf(task.copy(submittedPageIds = listOf("A", "A"))))) }
    }
}
