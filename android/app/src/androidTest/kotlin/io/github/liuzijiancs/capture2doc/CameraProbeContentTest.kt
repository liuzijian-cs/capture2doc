package io.github.liuzijiancs.capture2doc

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.click
import androidx.compose.ui.test.doubleClick
import androidx.compose.ui.test.getUnclippedBoundsInRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performTouchInput
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraCandidateUi
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraGateState
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeContent
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeTags
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeUiState
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CaptureJob
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CaptureStage
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.FocusIndicatorState
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.FocusStatus
import io.github.liuzijiancs.capture2doc.ui.theme.Capture2DocTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

class CameraProbeContentTest {
    @get:Rule
    val composeRule = createComposeRule()

    private var state by mutableStateOf(CameraProbeUiState())
    private var disconnected by mutableStateOf(false)

    @Test fun connectionWarningDoesNotResizeViewportOrDisableCaptureAndFinish() {
        state = CameraProbeUiState(gate = CameraGateState.Ready, draftReady = true,
            candidates = listOf(testCandidate("one")))
        setProbeContent(cameraSessionAvailable = true)
        val before = composeRule.onNodeWithTag(CameraProbeTags.VIEWPORT).getUnclippedBoundsInRoot()
        composeRule.runOnIdle { disconnected = true }
        composeRule.onNodeWithTag(CameraProbeTags.CONNECTION_WARNING).assertIsDisplayed()
        composeRule.onNodeWithTag(CameraProbeTags.SHUTTER).assertIsEnabled()
        composeRule.onNodeWithTag(CameraProbeTags.FINISH).assertIsEnabled()
        assertEquals(before, composeRule.onNodeWithTag(CameraProbeTags.VIEWPORT).getUnclippedBoundsInRoot())
    }

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
    fun candidateStripDoesNotChangeViewportBounds() {
        state = CameraProbeUiState(gate = CameraGateState.Ready)
        setProbeContent(cameraSessionAvailable = true)
        val emptyBounds = composeRule.onNodeWithTag(CameraProbeTags.VIEWPORT)
            .getUnclippedBoundsInRoot()

        updateState(
            CameraProbeUiState(
                gate = CameraGateState.Ready,
                candidates = listOf(
                    testCandidate("one", pageNumber = 1),
                    testCandidate("two", pageNumber = 2),
                    testCandidate("three", pageNumber = 3),
                ),
            ),
        )

        composeRule.onNodeWithTag(CameraProbeTags.THUMBNAILS).assertIsDisplayed()
        val populatedBounds = composeRule.onNodeWithTag(CameraProbeTags.VIEWPORT)
            .getUnclippedBoundsInRoot()
        assertEquals(emptyBounds, populatedBounds)
    }

    @Test
    fun portraitViewportIsThreeByFour() {
        state = CameraProbeUiState(gate = CameraGateState.Ready)
        setProbeContent(cameraSessionAvailable = true)

        val viewportBounds = composeRule.onNodeWithTag(CameraProbeTags.VIEWPORT)
            .getUnclippedBoundsInRoot()
        val viewportWidth = viewportBounds.right - viewportBounds.left
        val viewportHeight = viewportBounds.bottom - viewportBounds.top

        assertEquals(3f / 4f, viewportWidth / viewportHeight, 0.01f)
    }

    @Test
    fun landscapeViewportIsFourByThree() {
        state = CameraProbeUiState(gate = CameraGateState.Ready)
        setProbeContent(
            cameraSessionAvailable = true,
            landscapeLayoutOverride = true,
        )

        val viewportBounds = composeRule.onNodeWithTag(CameraProbeTags.VIEWPORT)
            .getUnclippedBoundsInRoot()
        val viewportWidth = viewportBounds.right - viewportBounds.left
        val viewportHeight = viewportBounds.bottom - viewportBounds.top

        assertEquals(4f / 3f, viewportWidth / viewportHeight, 0.01f)
    }

    @Test
    fun shutterIsHorizontallyCentered() {
        state = CameraProbeUiState(gate = CameraGateState.Ready)
        setProbeContent(cameraSessionAvailable = true)

        val screenBounds = composeRule.onNodeWithTag(CameraProbeTags.SCREEN)
            .getUnclippedBoundsInRoot()
        val shutterBounds = composeRule.onNodeWithTag(CameraProbeTags.SHUTTER)
            .getUnclippedBoundsInRoot()
        val screenCenterX = (screenBounds.left.value + screenBounds.right.value) / 2f
        val shutterCenterX = (shutterBounds.left.value + shutterBounds.right.value) / 2f

        assertEquals(screenCenterX, shutterCenterX, 0.5f)
    }

    @Test
    fun thirdPendingCaptureOnlyDisablesShutterAndShowsNoDebugCopy() {
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
        composeRule.onNodeWithTag(CameraProbeTags.SHUTTER).assertIsNotEnabled()
        composeRule.onNodeWithText("相机能力测试").assertDoesNotExist()
        composeRule.onNodeWithText("已拍", substring = true).assertDoesNotExist()
        composeRule.onNodeWithText("保存中", substring = true).assertDoesNotExist()
        composeRule.onNodeWithText("正在保存 3 张照片，请稍候…").assertDoesNotExist()
    }

    @Test
    fun finishRequiresAtLeastOneCandidateWithASafelyPersistedOriginal() {
        state = CameraProbeUiState(gate = CameraGateState.Ready, draftReady = true)
        setProbeContent(cameraSessionAvailable = true)
        composeRule.onNodeWithTag(CameraProbeTags.FINISH).assertIsNotEnabled()

        updateState(
            CameraProbeUiState(
                gate = CameraGateState.Ready,
                draftReady = true,
                candidates = listOf(
                    testCandidate(
                        pageId = "normalizing",
                        state = ScanPageState.NORMALIZING,
                        hasSafeOriginal = true,
                    ),
                ),
            ),
        )
        composeRule.onNodeWithTag(CameraProbeTags.FINISH).assertIsEnabled()

        updateState(
            CameraProbeUiState(
                gate = CameraGateState.Ready,
                draftReady = true,
                candidates = listOf(
                    testCandidate(
                        pageId = "capturing",
                        state = ScanPageState.CAPTURING,
                        hasSafeOriginal = false,
                    ),
                ),
            ),
        )
        composeRule.onNodeWithTag(CameraProbeTags.FINISH).assertIsNotEnabled()

        updateState(
            CameraProbeUiState(
                gate = CameraGateState.Ready,
                draftReady = true,
                candidates = listOf(
                    testCandidate(
                        pageId = "failed",
                        state = ScanPageState.FAILED,
                        hasSafeOriginal = true,
                    ),
                ),
            ),
        )
        composeRule.onNodeWithTag(CameraProbeTags.FINISH).assertIsEnabled()

        updateState(
            CameraProbeUiState(
                gate = CameraGateState.Ready,
                draftReady = true,
                candidates = listOf(
                    testCandidate(
                        pageId = "failed-without-original",
                        state = ScanPageState.FAILED,
                        hasSafeOriginal = false,
                    ),
                ),
            ),
        )
        composeRule.onNodeWithTag(CameraProbeTags.FINISH).assertIsNotEnabled()
    }

    @Test
    fun singleTapCandidateOpensPreview() {
        state = CameraProbeUiState(
            gate = CameraGateState.Ready,
            candidates = listOf(testCandidate("one")),
        )
        setProbeContent(cameraSessionAvailable = true)

        composeRule.onNodeWithTag(CameraProbeTags.candidate("one"))
            .performTouchInput { click() }

        composeRule.onNodeWithTag(CameraProbeTags.PREVIEW).assertIsDisplayed()
    }

    @Test
    fun doubleTapCandidateDeletesExactlyOnce() {
        var deletedPageIds = emptyList<String>()
        state = CameraProbeUiState(
            gate = CameraGateState.Ready,
            candidates = listOf(testCandidate("one")),
        )
        setProbeContent(
            cameraSessionAvailable = true,
            onDeleteCandidate = { deletedPageIds = deletedPageIds + it },
        )

        composeRule.onNodeWithTag(CameraProbeTags.candidate("one"))
            .performTouchInput { doubleClick() }

        composeRule.runOnIdle {
            assertEquals(listOf("one"), deletedPageIds)
        }
        composeRule.onNodeWithTag(CameraProbeTags.PREVIEW).assertDoesNotExist()
    }

    @Test
    fun rendersFailedCandidateAndFocusIndicator() {
        state = CameraProbeUiState(
            gate = CameraGateState.Ready,
            candidates = listOf(
                testCandidate(
                    pageId = "failed",
                    state = ScanPageState.FAILED,
                    hasSafeOriginal = false,
                ),
            ),
            focusIndicator = FocusIndicatorState(
                requestId = 1,
                x = 100f,
                y = 100f,
                status = FocusStatus.FOCUSING,
            ),
        )
        setProbeContent(cameraSessionAvailable = true)

        composeRule.onNodeWithTag(CameraProbeTags.CANDIDATE_ERROR).assertIsDisplayed()
        composeRule.onNodeWithTag(CameraProbeTags.FOCUS).assertIsDisplayed()
    }

    private fun setProbeContent(
        cameraSessionAvailable: Boolean,
        landscapeLayoutOverride: Boolean? = null,
        onDeleteCandidate: (String) -> Unit = {},
    ) {
        composeRule.setContent {
            Capture2DocTheme {
                CameraProbeContent(
                    uiState = state,
                    cameraSessionAvailable = cameraSessionAvailable,
                    onBack = {},
                    onRequestPermission = {},
                    onOpenSettings = {},
                    onCapture = {},
                    onFinish = {},
                    onDeleteCandidate = onDeleteCandidate,
                    onMoveCandidate = { _, _ -> },
                    onRetryCamera = {},
                    onFocus = { _, _ -> },
                    onClearFocus = {},
                    cameraPreview = {},
                    landscapeLayoutOverride = landscapeLayoutOverride,
                    disconnected = disconnected,
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

    private fun testCandidate(
        pageId: String,
        pageNumber: Int = 1,
        state: ScanPageState = ScanPageState.READY,
        hasSafeOriginal: Boolean = true,
        canDelete: Boolean = state != ScanPageState.CAPTURING,
    ) = CameraCandidateUi(
        pageId = pageId,
        pageNumber = pageNumber,
        originalPath = "/not-present/$pageId/original.jpg",
        normalizedPath = "/not-present/$pageId/normalized_1280.jpg",
        state = state,
        hasSafeOriginal = hasSafeOriginal,
        canDelete = canDelete,
    )

    private fun testJob(captureId: String, stage: CaptureStage) = CaptureJob(
        captureId = captureId,
        captureDirectoryPath = "/not-present/$captureId",
        originalPath = "/not-present/$captureId/original.jpg",
        normalizedPath = "/not-present/$captureId/normalized_1280.jpg",
        stage = stage,
        requestedAtElapsedMillis = 0,
    )
}
