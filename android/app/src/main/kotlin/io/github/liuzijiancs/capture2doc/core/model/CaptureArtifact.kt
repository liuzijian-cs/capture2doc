package io.github.liuzijiancs.capture2doc.core.model

data class CaptureTimings(
    val requestToCaptureStartedMillis: Long?,
    val requestToPostviewMillis: Long?,
    val requestToImageSavedMillis: Long,
    val normalizationMillis: Long,
)

data class CaptureArtifact(
    val captureId: String,
    val originalPath: String,
    val normalizedPath: String,
    val originalWidth: Int,
    val originalHeight: Int,
    val originalBytes: Long,
    val normalizedWidth: Int,
    val normalizedHeight: Int,
    val normalizedBytes: Long,
    val sourceRotationDegrees: Int,
    val timings: CaptureTimings,
)
