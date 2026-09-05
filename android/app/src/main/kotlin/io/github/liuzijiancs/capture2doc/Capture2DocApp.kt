package io.github.liuzijiancs.capture2doc

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeRoute
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeViewModel
import io.github.liuzijiancs.capture2doc.feature.capture2doc.draft.ScanDraftRoute
import io.github.liuzijiancs.capture2doc.feature.capture2doc.draft.ScanDraftViewModel
import io.github.liuzijiancs.capture2doc.ui.home.HomeScreen

private enum class AppDestination {
    HOME,
    CAMERA_PROBE,
    DRAFT_REVIEW,
}

@Composable
fun Capture2DocApp() {
    val snackbarHostState = remember { SnackbarHostState() }
    var destinationName by rememberSaveable { mutableStateOf(AppDestination.HOME.name) }
    val destination = AppDestination.valueOf(destinationName)
    val draftViewModel: ScanDraftViewModel = viewModel()
    val cameraViewModel: CameraProbeViewModel = viewModel()
    val draftState by draftViewModel.uiState.collectAsStateWithLifecycle()
    val cameraState by cameraViewModel.uiState.collectAsStateWithLifecycle()
    var pendingRetakePageId by rememberSaveable { mutableStateOf<String?>(null) }
    var showContinueDraftDialog by rememberSaveable { mutableStateOf(false) }
    var showExitDraftDialog by rememberSaveable { mutableStateOf(false) }
    var startScanWhenDraftLoaded by rememberSaveable { mutableStateOf(false) }

    LaunchedEffect(Unit) {
        draftViewModel.refreshDraft()
    }

    fun navigateToHomeFromDraft() {
        if (draftState.draft?.pages.orEmpty().isEmpty()) {
            draftViewModel.discardDraft()
        }
        destinationName = AppDestination.HOME.name
        pendingRetakePageId = null
        showExitDraftDialog = false
    }

    fun requestDraftExit() {
        if (draftState.draft?.pages.orEmpty().isNotEmpty()) {
            showExitDraftDialog = true
        } else {
            navigateToHomeFromDraft()
        }
    }

    fun openScanEntry() {
        if (draftState.draft?.pages.orEmpty().isNotEmpty()) {
            showContinueDraftDialog = true
        } else {
            pendingRetakePageId = null
            destinationName = AppDestination.CAMERA_PROBE.name
        }
    }

    fun onStartScan() {
        if (draftState.isLoading) {
            startScanWhenDraftLoaded = true
        } else {
            openScanEntry()
        }
    }

    LaunchedEffect(draftState.isLoading, startScanWhenDraftLoaded) {
        if (startScanWhenDraftLoaded && !draftState.isLoading) {
            startScanWhenDraftLoaded = false
            openScanEntry()
        }
    }

    if (showContinueDraftDialog) {
        AlertDialog(
            onDismissRequest = { showContinueDraftDialog = false },
            title = { Text("检测到本地草稿") },
            text = { Text("检测到未完成草稿。选择“继续草稿”进入页面整理，或者“放弃并新建”重新开始拍照。") },
            confirmButton = {
                Button(
                    onClick = {
                        showContinueDraftDialog = false
                        destinationName = AppDestination.DRAFT_REVIEW.name
                    },
                ) {
                    Text("继续草稿")
                }
            },
            dismissButton = {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(top = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedButton(
                        onClick = {
                            showContinueDraftDialog = false
                            draftViewModel.discardDraft {
                                pendingRetakePageId = null
                                destinationName = AppDestination.CAMERA_PROBE.name
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("放弃并新建")
                    }
                    TextButton(
                        onClick = { showContinueDraftDialog = false },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("取消")
                    }
                }
            },
        )
    }

    if (showExitDraftDialog) {
        AlertDialog(
            onDismissRequest = { showExitDraftDialog = false },
            title = { Text("草稿未完成") },
            text = { Text("当前草稿尚未上传。保存后可返回继续编辑。") },
            confirmButton = {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Button(
                        onClick = {
                            showExitDraftDialog = false
                            navigateToHomeFromDraft()
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("保存并退出")
                    }
                    OutlinedButton(
                        onClick = {
                            showExitDraftDialog = false
                            draftViewModel.discardDraft {
                                destinationName = AppDestination.HOME.name
                                pendingRetakePageId = null
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("放弃草稿")
                    }
                    TextButton(
                        onClick = {
                            showExitDraftDialog = false
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Text("继续编辑")
                    }
                }
            },
            dismissButton = {
                TextButton(
                    onClick = { showExitDraftDialog = false },
                ) {
                    Text("取消")
                }
            },
        )
    }

    when (destination) {
        AppDestination.HOME -> HomeScreen(
            snackbarHostState = snackbarHostState,
            onStartScan = { onStartScan() },
        )

        AppDestination.CAMERA_PROBE -> CameraProbeRoute(
            onBack = { hasAcceptedCaptureOrPage ->
                pendingRetakePageId = null
                if (
                    hasAcceptedCaptureOrPage ||
                    draftState.draft?.pages.orEmpty().isNotEmpty()
                ) {
                    destinationName = AppDestination.DRAFT_REVIEW.name
                } else {
                    navigateToHomeFromDraft()
                }
            },
            onDraftReady = {
                pendingRetakePageId = null
                destinationName = AppDestination.DRAFT_REVIEW.name
            },
            retakePageId = pendingRetakePageId,
            viewModel = cameraViewModel,
        )

        AppDestination.DRAFT_REVIEW -> ScanDraftRoute(
            onBackToHome = { requestDraftExit() },
            onSaveAndExit = { navigateToHomeFromDraft() },
            onContinueCapture = {
                pendingRetakePageId = null
                destinationName = AppDestination.CAMERA_PROBE.name
            },
            onRetake = { pageId ->
                cameraViewModel.prepareRetake(pageId) { prepared ->
                    if (
                        prepared &&
                        destinationName == AppDestination.DRAFT_REVIEW.name
                    ) {
                        pendingRetakePageId = pageId
                        destinationName = AppDestination.CAMERA_PROBE.name
                    }
                }
            },
            retakePreparationInProgress = cameraState.retakePreparationInProgress,
            viewModel = draftViewModel,
        )
    }
}
