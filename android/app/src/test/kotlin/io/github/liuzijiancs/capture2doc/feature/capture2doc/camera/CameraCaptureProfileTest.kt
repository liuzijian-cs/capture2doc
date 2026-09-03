package io.github.liuzijiancs.capture2doc.feature.capture2doc.camera

import android.view.OrientationEventListener
import android.view.Surface
import androidx.camera.core.resolutionselector.ResolutionStrategy
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CameraCaptureProfileTest {
    @Test
    fun stableLowLatencyProfileUsesFiveMegapixelBound() {
        assertEquals(2560, CameraCaptureProfile.CAPTURE_WIDTH)
        assertEquals(1920, CameraCaptureProfile.CAPTURE_HEIGHT)
        assertEquals(
            ResolutionStrategy.FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER,
            CameraCaptureProfile.CAPTURE_FALLBACK_RULE,
        )
        assertEquals(90, CameraCaptureProfile.JPEG_QUALITY)
        assertEquals(3, CameraCaptureProfile.MAX_PENDING_CAPTURES)
    }

    @Test
    fun captureLimitCountsOnlyJobsThatHaveNotReachedDisk() {
        assertTrue(
            canAcceptCapture(
                listOf(CaptureStage.QUEUED, CaptureStage.SAVING),
            ),
        )
        assertFalse(
            canAcceptCapture(
                listOf(
                    CaptureStage.QUEUED,
                    CaptureStage.CAPTURING,
                    CaptureStage.SAVING,
                ),
            ),
        )
        assertTrue(
            canAcceptCapture(
                listOf(
                    CaptureStage.NORMALIZING,
                    CaptureStage.NORMALIZING,
                    CaptureStage.READY,
                    CaptureStage.FAILED,
                ),
            ),
        )
    }

    @Test
    fun physicalOrientationSnapsToAllSurfaceRotations() {
        assertEquals(Surface.ROTATION_0, snapOrientationToSurfaceRotation(0))
        assertEquals(Surface.ROTATION_270, snapOrientationToSurfaceRotation(90))
        assertEquals(Surface.ROTATION_180, snapOrientationToSurfaceRotation(180))
        assertEquals(Surface.ROTATION_90, snapOrientationToSurfaceRotation(270))
        assertNull(
            snapOrientationToSurfaceRotation(
                OrientationEventListener.ORIENTATION_UNKNOWN,
            ),
        )
    }

    @Test
    fun delayedCallbacksCannotRegressOrOverwriteTerminalStage() {
        assertEquals(
            CaptureStage.NORMALIZING,
            CaptureStage.NORMALIZING.advanceTo(CaptureStage.CAPTURING),
        )
        assertEquals(
            CaptureStage.READY,
            CaptureStage.READY.advanceTo(CaptureStage.FAILED),
        )
        assertEquals(
            CaptureStage.FAILED,
            CaptureStage.SAVING.advanceTo(CaptureStage.FAILED),
        )
    }
}
