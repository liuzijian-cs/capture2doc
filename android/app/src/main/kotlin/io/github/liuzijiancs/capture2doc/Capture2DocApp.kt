package io.github.liuzijiancs.capture2doc

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeRoute
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeViewModel
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.countsAgainstCaptureLimit
import io.github.liuzijiancs.capture2doc.ui.home.*
import io.github.liuzijiancs.capture2doc.ui.theme.Capture2DocTheme

@Composable
fun Capture2DocApp() {
    val model: TaskHomeViewModel = viewModel()
    val state by model.state.collectAsStateWithLifecycle()
    val tasks by model.tasks.collectAsStateWithLifecycle()
    val networkAvailable by model.networkAvailable.collectAsStateWithLifecycle()
    val displayedTasks = if (networkAvailable) tasks else tasks.map { it.copy(connectionIssue = "网络未连接") }
    val app = LocalContext.current.applicationContext as Capture2DocApplication
    val active = displayedTasks.firstOrNull { it.taskId == state.activeTaskId }
    var exitDialog by rememberSaveable { mutableStateOf(false) }

    when {
        state.destination == TaskDestination.CAMERA && active != null -> {
            val factory = remember(active.taskId) {
                object : ViewModelProvider.Factory {
                    @Suppress("UNCHECKED_CAST")
                    override fun <T : ViewModel> create(modelClass: Class<T>): T =
                        CameraProbeViewModel(app, model.repository.draftRepository(active.taskId)) as T
                }
            }
            val camera: CameraProbeViewModel = viewModel(key = "camera-${active.taskId}", factory = factory)
            val cameraState by camera.uiState.collectAsStateWithLifecycle()
            LaunchedEffect(state.busy) { camera.setWorkflowBlocked(state.busy) }
            Capture2DocTheme(darkTheme = true) {
                CameraProbeRoute(
                    onBack = { if (!state.busy) exitDialog = true },
                    onDraftReady = {
                        if (!state.busy) {
                            // Close the shutter gate synchronously, before the next composition.
                            camera.setWorkflowBlocked(true)
                            model.finishTask()
                        }
                    },
                    interactionsEnabled = !state.busy && !exitDialog,
                    disconnected = active.isDisconnected,
                    viewModel = camera,
                )
                if (exitDialog) {
                    val safeToExit = cameraState.captureJobs.none { it.stage.countsAgainstCaptureLimit }
                    AlertDialog(
                        onDismissRequest = { if (!state.busy) exitDialog = false },
                        title = { Text("保存草稿？") },
                        text = { Text(if (safeToExit) "保存后可从首页继续拍摄。选择移除时，仅从本机列表移除，后台上传和处理仍会继续。" else "正在保存照片，请稍候。") },
                        confirmButton = {
                            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                                TextButton(enabled = safeToExit && !state.busy, onClick = { exitDialog = false; model.goHome() }) { Text("保存并退出") }
                                TextButton(enabled = safeToExit && !state.busy, onClick = {
                                    exitDialog = false; model.hideTasks(setOf(active.taskId))
                                }) { Text("仅从本机列表移除") }
                            }
                        },
                        dismissButton = { TextButton(onClick = { exitDialog = false }) { Text("继续拍摄") } },
                    )
                }
            }
        }
        state.destination == TaskDestination.DETAIL && active != null -> Capture2DocTheme(darkTheme = false) {
            TaskDetailScreen(active, model::goHome, { model.retryTask(active.taskId) })
        }
        else -> Capture2DocTheme(darkTheme = false) {
            HomeScreen(displayedTasks, model::createTask, model::openTask, model::hideTasks,
                disconnected = !networkAvailable || BuildConfig.SERVICE_BASE_URL.isBlank(),
                busy = state.busy, error = state.error, onRetry = model::retry)
        }
    }
    if (state.error != null && state.destination != TaskDestination.HOME) AlertDialog(
        onDismissRequest = model::clearError, title = { Text("操作未完成") }, text = { Text(state.error.orEmpty()) },
        confirmButton = { TextButton(onClick = model::retry) { Text("重试") } },
        dismissButton = { TextButton(onClick = model::clearError) { Text("关闭") } },
    )
}
