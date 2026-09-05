package io.github.liuzijiancs.capture2doc.core.model

internal enum class DocumentPhase { IDLE, OCR, ASSEMBLING, VALIDATING, COMPLETED, FAILED }
internal enum class PageRemotePhase { QUEUED, PROCESSING, COMPLETED, FAILED }
internal enum class TaskDisplayStatus(val label: String) {
    IDLE(""), NOT_CONNECTED("未连接"), UPLOADING("上传中"), PROCESSING("处理中"), COMPLETED("已完成"), FAILED("处理失败"),
}

internal data class DocumentTask(
    val taskId: String,
    val documentId: String? = null,
    val baseUrl: String,
    val createdAt: Long,
    val title: String = "未命名文档",
    val pageIds: List<String> = emptyList(),
    val submittedPageIds: List<String>? = null,
    val uploadedPageIds: Set<String> = emptySet(),
    val submissionAccepted: Boolean = false,
    val phase: DocumentPhase = DocumentPhase.IDLE,
    val pagePhases: Map<String, PageRemotePhase> = emptyMap(),
    val wordCount: Long? = null,
    val plainText: String? = null,
    val error: String? = null,
    val localError: String? = null,
    val connectionIssue: String? = null,
    val retryable: Boolean = true,
    val hidden: Boolean = false,
    val legacy: Boolean = false,
) {
    val isDraft: Boolean get() = submittedPageIds == null
    val isVisible: Boolean get() = !hidden
    val isDisconnected: Boolean get() = documentId == null || connectionIssue != null
    val effectivePageIds: List<String> get() = submittedPageIds ?: pageIds
    val hasCompletedDocument: Boolean get() = !isDraft && submissionAccepted &&
        phase == DocumentPhase.COMPLETED && effectivePageIds.all { it in uploadedPageIds }
    val displayStatus: TaskDisplayStatus get() = when {
        error != null || localError != null || phase == DocumentPhase.FAILED ||
            effectivePageIds.any { pagePhases[it] == PageRemotePhase.FAILED } -> TaskDisplayStatus.FAILED
        isDisconnected -> TaskDisplayStatus.NOT_CONNECTED
        effectivePageIds.any { it !in uploadedPageIds } -> TaskDisplayStatus.UPLOADING
        !isDraft && submissionAccepted && phase == DocumentPhase.COMPLETED -> TaskDisplayStatus.COMPLETED
        phase in setOf(DocumentPhase.OCR, DocumentPhase.ASSEMBLING, DocumentPhase.VALIDATING) ||
            !isDraft -> TaskDisplayStatus.PROCESSING
        else -> TaskDisplayStatus.IDLE
    }
    val visibleWordCount: Long? get() = wordCount.takeIf { hasCompletedDocument }
}

internal fun visibleTasks(tasks: List<DocumentTask>, query: String): List<DocumentTask> =
    tasks.filter { it.isVisible && it.title.contains(query.trim(), ignoreCase = true) }
        .sortedWith(compareByDescending<DocumentTask> { it.createdAt }.thenBy { it.taskId })
