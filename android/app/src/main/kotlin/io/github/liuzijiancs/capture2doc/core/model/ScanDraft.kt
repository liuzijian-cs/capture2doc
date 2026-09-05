package io.github.liuzijiancs.capture2doc.core.model

internal const val SCAN_DRAFT_SCHEMA_VERSION = 1
internal const val SCAN_PAGE_INVALID_ORIGINAL_MESSAGE = "原图缺失或损坏，请删除后重新拍摄"

internal enum class ScanPageState {
    CAPTURING,
    NORMALIZING,
    READY,
    FAILED,
    RETAKE_REQUIRED,
}

internal data class ScanPage(
    val pageId: String,
    val originalRelativePath: String,
    val normalizedRelativePath: String,
    val state: ScanPageState,
    val errorMessage: String? = null,
)

internal data class ScanDraft(
    val schemaVersion: Int = SCAN_DRAFT_SCHEMA_VERSION,
    val draftId: String,
    val createdAtEpochMillis: Long,
    val updatedAtEpochMillis: Long,
    val pages: List<ScanPage>,
)

internal data class ScanPageFiles(
    val pageId: String,
    val directoryPath: String,
    val originalPath: String,
    val normalizedPath: String,
)
