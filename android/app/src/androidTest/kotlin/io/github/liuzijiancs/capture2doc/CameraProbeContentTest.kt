package io.github.liuzijiancs.capture2doc

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraGateState
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeContent
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeTags
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeUiState
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CaptureJob
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CaptureStage
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.FocusIndicatorState
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.FocusStatus
import io.github.liuzijiancs.capture2doc.ui.theme.Capture2DocTheme
import org.junit.Rule
import org.junit.Test
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue

class CameraProbeContentTest {
    @get:Rule
    val composeRule = createComposeRule()

    private var state by mutableStateOf(CameraProbeUiState())

    @Test
    fun rendersEveryCameraGateState() {
        setProbeContent(cameraSessionAvailable = true)

        assertState(CameraProbeTags.PERMISSION)
        updateState(CameraProbeUiState(gate = CameraGateState.RequestingPermission))
        assertState(CameraProbeTags.REQUESTING_PERMISSION)
        updateState(CameraProbeUiState(gate = CameraGateState.PermissionDenied))
        assertState(CameraProbeTags.PERMISSION_DENIED)
        updateState(CameraProbeUiState(gate = CameraGateState.StartingCamera))
        assertState(CameraProbeTags.STARTING)
        updateState(CameraProbeUiState(gate = CameraGateState.Ready))
        assertState(CameraProbeTags.READY)
        updateState(CameraProbeUiState(gate = CameraGateState.Error("测试错误")))
        assertState(CameraProbeTags.ERROR)
        updateState(CameraProbeUiState(gate = CameraGateState.CameraUnavailable))
        assertState(CameraProbeTags.UNAVAILABLE)
    }

    @Test
    fun capturePendingKeepsPreviewAndShutterVisible() {
        state = CameraProbeUiState(
            gate = CameraGateState.Ready,
            captureJobs = listOf(testJob("one", CaptureStage.SAVING)),
        )
        setProbeContent(cameraSessionAvailable = true)

        composeRule.onNodeWithTag(CameraProbeTags.READY).assertIsDisplayed()
        composeRule.onNodeWithTag(CameraProbeTags.THUMBNAILS).assertIsDisplayed()
        composeRule.onNodeWithTag(CameraProbeTags.SHUTTER)
            .assertIsDisplayed()
            .assertIsEnabled()
    }

    @Test
    fun portraitViewportIsThreeByFourAndDoesNotOverlapControls() {
        state = CameraProbeUiState(gate = CameraGateState.Ready)
        setProbeContent(cameraSessionAvailable = true)

        val viewportBounds = composeRule.onNodeWithTag(CameraProbeTags.VIEWPORT)
            .getUnclippedBoundsInRoot()
        val controlsBounds = composeRule.onNodeWithTag(CameraProbeTags.CONTROLS)
            .getUnclippedBoundsInRoot()

        val viewportWidth = viewportBounds.right - viewportBounds.left
        val viewportHeight = viewportBounds.bottom - viewportBounds.top
        assertEquals(3f / 4f, viewportWidth / viewportHeight, 0.01f)
        assertTrue(viewportBounds.bottom <= controlsBounds.top)
    }

    @Test
    fun landscapeViewportIsFourByThreeAndDoesNotOverlapControls() {
        state = CameraProbeUiState(gate = CameraGateState.Ready)
        setProbeContent(
            cameraSessionAvailable = true,
            landscapeLayoutOverride = true,
        )

        val viewportBounds = composeRule.onNodeWithTag(CameraProbeTags.VIEWPORT)
            .getUnclippedBoundsInRoot()
        val controlsBounds = composeRule.onNodeWithTag(CameraProbeTags.CONTROLS)
            .getUnclippedBoundsInRoot()
        val viewportWidth = viewportBounds.right - viewportBounds.left
        val viewportHeight = viewportBounds.bottom - viewportBounds.top

        assertEquals(4f / 3f, viewportWidth / viewportHeight, 0.01f)
        assertTrue(viewportBounds.right <= controlsBounds.left)
    }

    @Test
    fun thirdPendingCaptureDisablesShutterWithoutCoveringPreview() {
        state = CameraProbeUiState(
            gate = CameraGateState.Ready,
            captureJobs = listOf(
                testJob("one", CaptureStage.CAPTURING),
                testJob("two", CaptureStage.SAVING),
                testJob("three", CaptureStage.QUEUED),
            ),
        )
        setProbeContent(cameraSessionAvailable = true)

        composeRule.onNodeWithTag(CameraProbeTags.READY).assertIsDisplayed()
        composeRule.onNodeWithTag(CameraProbeTags.QUEUE_FULL).assertIsDisplayed()
        composeRule.onNodeWithTag(CameraProbeTags.SHUTTER).assertIsNotEnabled()
    }

    @Test
    fun rendersFocusIndicatorAndFailureResults() {
        state = CameraProbeUiState(
            gate = CameraGateState.Ready,
            focusIndicator = FocusIndicatorState(
                requestId = 1,
                x = 100f,
                y = 100f,
                status = FocusStatus.FOCUSING,
            ),
        )
        setProbeContent(cameraSessionAvailable = true)
        composeRule.onNodeWithTag(CameraProbeTags.FOCUS).assertIsDisplayed()

        updateState(
            CameraProbeUiState(
                gate = CameraGateState.Ready,
                captureJobs = listOf(
                    testJob("failed", CaptureStage.FAILED).copy(
                        errorMessage = "保存失败",
                    ),
                ),
                finishRequested = true,
                showResults = true,
            ),
        )
        composeRule.onNodeWithTag(CameraProbeTags.RESULTS).assertIsDisplayed()
    }

    private fun setProbeContent(
        cameraSessionAvailable: Boolean,
        landscapeLayoutOverride: Boolean? = null,
    ) {
        composeRule.setContent {
            Capture2DocTheme {
                CameraProbeContent(
                    uiState = state,
                    cameraSessionAvailable = cameraSessionAvailable,
                    onBack = {},
                    onRequestPermission = {},
                    onOpenSettings = {},
                    onCapture = { true },
                    onFinish = {},
                    onContinue = {},
                    onRetryCamera = {},
                    onRetryJob = {},
                    onRemoveJob = {},
                    onFocus = { _, _ -> },
                    onClearFocus = {},
                    cameraPreview = {},
                    landscapeLayoutOverride = landscapeLayoutOverride,
                )
            }
        }
    }

    private fun updateState(newState: CameraProbeUiState) {
        composeRule.runOnIdle { state = newState }
    }

    private fun assertState(tag: String) {
        composeRule.onNodeWithTag(tag).assertIsDisplayed()
    }

    private fun testJob(captureId: String, stage: CaptureStage) = CaptureJob(
        captureId = captureId,
        captureDirectoryPath = "/not-present/$captureId",
        originalPath = "/not-present/$captureId/original.jpg",
        normalizedPath = "/not-present/$captureId/normalized_1280.jpg",
        stage = stage,
        requestedAtElapsedMillis = 0,
    )
}
