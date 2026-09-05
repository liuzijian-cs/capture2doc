package io.github.liuzijiancs.capture2doc.feature.capture2doc.camera

import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class CameraCandidateStateTest {
    @Test
    fun finishAcceptsReadyAndNormalizingPagesWithSafeOriginals() {
        assertTrue(
            canFinishCandidates(
                listOf(
                    candidate("ready", ScanPageState.READY, hasSafeOriginal = true),
                    candidate(
                        "normalizing",
                        ScanPageState.NORMALIZING,
                        hasSafeOriginal = true,
                    ),
                    candidate("derived-failed", ScanPageState.FAILED, hasSafeOriginal = true),
                ),
            ),
        )
    }

    @Test
    fun finishRejectsEmptyUnsafeCapturingAndRetakeStates() {
        assertFalse(canFinishCandidates(emptyList()))
        assertFalse(
            canFinishCandidates(
                listOf(candidate("unsafe", ScanPageState.READY, hasSafeOriginal = false)),
            ),
        )
        assertFalse(
            canFinishCandidates(
                listOf(candidate("capturing", ScanPageState.CAPTURING, hasSafeOriginal = false)),
            ),
        )
        assertFalse(
            canFinishCandidates(
                listOf(candidate("failed", ScanPageState.FAILED, hasSafeOriginal = false)),
            ),
        )
        assertFalse(
            canFinishCandidates(
                listOf(
                    candidate("retake", ScanPageState.RETAKE_REQUIRED, hasSafeOriginal = false),
                ),
            ),
        )
    }

    @Test
    fun finishIsDisabledDuringDraftMutation() {
        assertFalse(
            canFinishCandidates(
                candidates = listOf(
                    candidate("ready", ScanPageState.READY, hasSafeOriginal = true),
                ),
                mutationInProgress = true,
            ),
        )
    }

    @Test
    fun cameraActionsStayDisabledUntilDraftRecoveryCompletes() {
        val recovering = CameraProbeUiState(
            gate = CameraGateState.Ready,
            draftReady = false,
            candidates = listOf(
                candidate("ready", ScanPageState.READY, hasSafeOriginal = true),
            ),
        )

        assertFalse(recovering.canCapture)
        assertFalse(recovering.canFinish)
        assertTrue(recovering.copy(draftReady = true).canCapture)
        assertTrue(recovering.copy(draftReady = true).canFinish)
    }

    @Test
    fun submissionGateBlocksShutterAndDuplicateFinish() {
        val state = CameraProbeUiState(
            gate = CameraGateState.Ready,
            draftReady = true,
            candidates = listOf(candidate("page", ScanPageState.READY, hasSafeOriginal = true)),
        )
        assertTrue(state.canCapture)
        assertTrue(state.canFinish)
        assertFalse(state.copy(workflowBlocked = true).canCapture)
        assertFalse(state.copy(workflowBlocked = true).canFinish)
    }

    private fun candidate(
        pageId: String,
        state: ScanPageState,
        hasSafeOriginal: Boolean,
    ) = CameraCandidateUi(
        pageId = pageId,
        pageNumber = 1,
        originalPath = "/not-present/$pageId/original.jpg",
        normalizedPath = "/not-present/$pageId/normalized_1280.jpg",
        state = state,
        hasSafeOriginal = hasSafeOriginal,
    )
}
