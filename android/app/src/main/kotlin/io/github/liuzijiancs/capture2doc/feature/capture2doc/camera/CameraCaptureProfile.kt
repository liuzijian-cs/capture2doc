package io.github.liuzijiancs.capture2doc.feature.capture2doc.camera

import android.util.Size
import android.view.OrientationEventListener
import androidx.camera.core.ImageCapture
import androidx.camera.core.resolutionselector.AspectRatioStrategy
import androidx.camera.core.resolutionselector.ResolutionSelector
import androidx.camera.core.resolutionselector.ResolutionStrategy

internal object CameraCaptureProfile {
    const val CAPTURE_WIDTH = 2560
    const val CAPTURE_HEIGHT = 1920
    const val CAPTURE_FALLBACK_RULE =
        ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER
    const val JPEG_QUALITY = 90
    const val MAX_PENDING_CAPTURES = 3

    val targetCaptureSize = Size(CAPTURE_WIDTH, CAPTURE_HEIGHT)
    private val targetPostviewSize = Size(640, 480)

    fun previewResolutionSelector(): ResolutionSelector = ResolutionSelector.Builder()
        .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
        .build()

    fun captureResolutionSelector(): ResolutionSelector = ResolutionSelector.Builder()
        .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
        .setResolutionStrategy(
            ResolutionStrategy(
                targetCaptureSize,
                CAPTURE_FALLBACK_RULE,
            ),
        )
        .build()

    fun postviewResolutionSelector(): ResolutionSelector = ResolutionSelector.Builder()
        .setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
        .setResolutionStrategy(
            ResolutionStrategy(
                targetPostviewSize,
                ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER,
            ),
        )
        .build()
}

internal enum class CaptureStage {
    QUEUED,
    CAPTURING,
    SAVING,
    NORMALIZING,
    READY,
    FAILED,
}

internal val CaptureStage.countsAgainstCaptureLimit: Boolean
    get() = this == CaptureStage.QUEUED ||
        this == CaptureStage.CAPTURING ||
        this == CaptureStage.SAVING

internal val CaptureStage.isTerminal: Boolean
    get() = this == CaptureStage.READY || this == CaptureStage.FAILED

internal fun CaptureStage.advanceTo(next: CaptureStage): CaptureStage = when {
    isTerminal -> this
    next == CaptureStage.FAILED -> CaptureStage.FAILED
    next.ordinal > ordinal -> next
    else -> this
}

internal fun canAcceptCapture(
    stages: Iterable<CaptureStage>,
    maxPendingCaptures: Int = CameraCaptureProfile.MAX_PENDING_CAPTURES,
): Boolean = stages.count { it.countsAgainstCaptureLimit } < maxPendingCaptures

internal fun snapOrientationToSurfaceRotation(orientationDegrees: Int): Int? =
    if (orientationDegrees == OrientationEventListener.ORIENTATION_UNKNOWN) {
        null
    } else {
        ImageCapture.snapToSurfaceRotation(orientationDegrees)
    }
