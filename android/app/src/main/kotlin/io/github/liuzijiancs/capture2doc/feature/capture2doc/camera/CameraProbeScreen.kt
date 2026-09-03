package io.github.liuzijiancs.capture2doc.feature.capture2doc.camera

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import io.github.liuzijiancs.capture2doc.R
import io.github.liuzijiancs.capture2doc.core.model.CaptureArtifact
import java.util.Locale
import kotlin.math.roundToInt
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

object CameraProbeTags {
    const val SCREEN = "camera_probe_screen"
    const val PERMISSION = "camera_probe_permission"
    const val REQUESTING_PERMISSION = "camera_probe_requesting_permission"
    const val PERMISSION_DENIED = "camera_probe_permission_denied"
    const val STARTING = "camera_probe_starting"
    const val READY = "camera_probe_ready"
    const val RESULTS = "camera_probe_results"
    const val ERROR = "camera_probe_error"
    const val UNAVAILABLE = "camera_probe_unavailable"
    const val SHUTTER = "camera_probe_shutter"
    const val FINISH = "camera_probe_finish"
    const val QUEUE_FULL = "camera_probe_queue_full"
    const val FOCUS = "camera_probe_focus"
    const val THUMBNAILS = "camera_probe_thumbnails"
    const val VIEWPORT = "camera_probe_viewport"
    const val CONTROLS = "camera_probe_controls"
}

@Composable
internal fun CameraProbeRoute(
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
    viewModel: CameraProbeViewModel = viewModel(),
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val hasCamera = remember {
        context.packageManager.hasSystemFeature(PackageManager.FEATURE_CAMERA_ANY)
    }
    var permissionGranted by remember {
        mutableStateOf(context.hasCameraPermission())
    }
    var cameraSession by remember { mutableStateOf<CameraSessionHandle?>(null) }

    val permissionLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.RequestPermission(),
    ) { granted ->
        permissionGranted = granted
        viewModel.onPermissionResult(granted)
    }

    LaunchedEffect(hasCamera) {
        if (hasCamera) {
            viewModel.synchronizePermission(permissionGranted)
        } else {
            viewModel.onCameraUnavailable()
        }
    }

    DisposableEffect(lifecycleOwner, hasCamera) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME && hasCamera) {
                val latestPermission = context.hasCameraPermission()
                permissionGranted = latestPermission
                viewModel.synchronizePermission(latestPermission)
            }
        }
        lifecycleOwner.lifecycle.addObserver(observer)
        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
    }

    BackHandler(onBack = onBack)

    CameraProbeContent(
        uiState = uiState,
        cameraSessionAvailable = cameraSession != null,
        onBack = onBack,
        onRequestPermission = {
            viewModel.onPermissionRequestStarted()
            permissionLauncher.launch(Manifest.permission.CAMERA)
        },
        onOpenSettings = { context.openAppSettings() },
        onCapture = { cameraSession?.let(viewModel::capture) ?: false },
        onFinish = viewModel::onFinishRequested,
        onContinue = viewModel::continueCapturing,
        onRetryCamera = viewModel::retryCamera,
        onRetryJob = viewModel::retryJob,
        onRemoveJob = viewModel::removeJob,
        onFocus = { x, y ->
            cameraSession?.let { session ->
                val requestId = viewModel.onFocusStarted(x, y)
                session.focusAt(x, y) { successful ->
                    viewModel.onFocusResult(requestId, successful)
                }
            }
        },
        onClearFocus = viewModel::clearFocusIndicator,
        cameraPreview = { previewModifier ->
            CameraPreview(
                onSessionReady = { session ->
                    cameraSession = session
                    if (session == null) {
                        viewModel.onCameraStopped()
                    } else {
                        viewModel.onCameraReady(
                            resolution = session.captureResolution,
                            postviewSupported = session.postviewSupported,
                        )
                    }
                },
                onError = viewModel::onCameraError,
                modifier = previewModifier,
            )
        },
        modifier = modifier,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun CameraProbeContent(
    uiState: CameraProbeUiState,
    cameraSessionAvailable: Boolean,
    onBack: () -> Unit,
    onRequestPermission: () -> Unit,
    onOpenSettings: () -> Unit,
    onCapture: () -> Boolean,
    onFinish: () -> Unit,
    onContinue: () -> Unit,
    onRetryCamera: () -> Unit,
    onRetryJob: (String) -> Unit,
    onRemoveJob: (String) -> Unit,
    onFocus: (Float, Float) -> Unit,
    onClearFocus: (Long) -> Unit,
    cameraPreview: @Composable (Modifier) -> Unit,
    landscapeLayoutOverride: Boolean? = null,
    modifier: Modifier = Modifier,
) {
    Scaffold(
        modifier = modifier
            .fillMaxSize()
            .testTag(CameraProbeTags.SCREEN),
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = stringResource(R.string.camera_probe_title),
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                navigationIcon = {
                    TextButton(onClick = onBack) {
                        Text(stringResource(R.string.camera_probe_back))
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            if (uiState.showResults) {
                ResultsContent(
                    jobs = uiState.captureJobs,
                    onContinue = onContinue,
                    onBack = onBack,
                    onRetryJob = onRetryJob,
                    onRemoveJob = onRemoveJob,
                )
            } else {
                when (val gate = uiState.gate) {
                    CameraGateState.PermissionRequired -> PermissionRequiredContent(
                        onRequestPermission = onRequestPermission,
                    )

                    CameraGateState.RequestingPermission -> ProgressContent(
                        message = stringResource(R.string.camera_permission_requesting),
                        tag = CameraProbeTags.REQUESTING_PERMISSION,
                    )

                    CameraGateState.PermissionDenied -> PermissionDeniedContent(
                        onRequestPermission = onRequestPermission,
                        onOpenSettings = onOpenSettings,
                    )

                    CameraGateState.StartingCamera,
                    CameraGateState.Ready,
                    -> CameraSurface(
                        uiState = uiState,
                        cameraSessionAvailable = cameraSessionAvailable,
                        onCapture = onCapture,
                        onFinish = onFinish,
                        onFocus = onFocus,
                        onClearFocus = onClearFocus,
                        cameraPreview = cameraPreview,
                        landscapeLayoutOverride = landscapeLayoutOverride,
                    )

                    is CameraGateState.Error -> ErrorContent(
                        message = gate.message,
                        onRetry = onRetryCamera,
                    )

                    CameraGateState.CameraUnavailable -> CameraUnavailableContent()
                }
            }
        }
    }
}

@Composable
private fun PermissionRequiredContent(onRequestPermission: () -> Unit) {
    StatusCard(
        title = stringResource(R.string.camera_permission_title),
        description = stringResource(R.string.camera_permission_description),
        tag = CameraProbeTags.PERMISSION,
    ) {
        Button(
            onClick = onRequestPermission,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(stringResource(R.string.camera_permission_action))
        }
    }
}

@Composable
private fun PermissionDeniedContent(
    onRequestPermission: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    StatusCard(
        title = stringResource(R.string.camera_permission_denied_title),
        description = stringResource(R.string.camera_permission_denied_description),
        tag = CameraProbeTags.PERMISSION_DENIED,
    ) {
        Button(
            onClick = onRequestPermission,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(stringResource(R.string.camera_permission_retry))
        }
        OutlinedButton(
            onClick = onOpenSettings,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(stringResource(R.string.camera_open_settings))
        }
    }
}

@Composable
private fun CameraUnavailableContent() {
    StatusCard(
        title = stringResource(R.string.camera_unavailable_title),
        description = stringResource(R.string.camera_unavailable_description),
        tag = CameraProbeTags.UNAVAILABLE,
    )
}

@Composable
private fun StatusCard(
    title: String,
    description: String,
    tag: String,
    actions: @Composable ColumnScope.() -> Unit = {},
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Card(
            modifier = Modifier
                .fillMaxWidth()
                .testTag(tag),
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceContainer,
            ),
        ) {
            Column(
                modifier = Modifier.padding(24.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.headlineSmall,
                    fontWeight = FontWeight.Bold,
                )
                Text(
                    text = description,
                    style = MaterialTheme.typography.bodyLarge,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                actions()
            }
        }
    }
}

@Composable
private fun CameraSurface(
    uiState: CameraProbeUiState,
    cameraSessionAvailable: Boolean,
    onCapture: () -> Boolean,
    onFinish: () -> Unit,
    onFocus: (Float, Float) -> Unit,
    onClearFocus: (Long) -> Unit,
    cameraPreview: @Composable (Modifier) -> Unit,
    landscapeLayoutOverride: Boolean? = null,
) {
    val haptics = LocalHapticFeedback.current
    var shutterFlashVisible by remember { mutableStateOf(false) }
    val shutterFlashAlpha by animateFloatAsState(
        targetValue = if (shutterFlashVisible) 0.24f else 0f,
        animationSpec = tween(durationMillis = 70),
        label = "shutter flash",
    )

    LaunchedEffect(shutterFlashVisible) {
        if (shutterFlashVisible) {
            delay(70)
            shutterFlashVisible = false
        }
    }
    uiState.focusIndicator?.let { focus ->
        LaunchedEffect(focus.requestId, focus.status) {
            if (focus.status != FocusStatus.FOCUSING) {
                delay(900)
                onClearFocus(focus.requestId)
            }
        }
    }

    BoxWithConstraints(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .testTag(
                if (uiState.gate is CameraGateState.Ready) {
                    CameraProbeTags.READY
                } else {
                    CameraProbeTags.STARTING
                },
            ),
    ) {
        val isLandscape = landscapeLayoutOverride ?: (maxWidth > maxHeight)
        val viewport: @Composable (Modifier) -> Unit = { viewportModifier ->
            CameraViewport(
                uiState = uiState,
                cameraSessionAvailable = cameraSessionAvailable,
                shutterFlashAlpha = shutterFlashAlpha,
                onFocus = onFocus,
                cameraPreview = cameraPreview,
                modifier = viewportModifier,
            )
        }
        val captureAction = {
            if (onCapture()) {
                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                shutterFlashVisible = true
            }
        }

        if (isLandscape) {
            Row(
                modifier = Modifier.fillMaxSize(),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxHeight(),
                    contentAlignment = Alignment.Center,
                ) {
                    viewport(Modifier.aspectRatio(4f / 3f))
                }
                CameraControls(
                    uiState = uiState,
                    cameraSessionAvailable = cameraSessionAvailable,
                    onCapture = captureAction,
                    onFinish = onFinish,
                    modifier = Modifier
                        .width(280.dp)
                        .fillMaxHeight(),
                )
            }
        } else {
            Column(
                modifier = Modifier.fillMaxSize(),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    viewport(Modifier.aspectRatio(3f / 4f))
                }
                CameraControls(
                    uiState = uiState,
                    cameraSessionAvailable = cameraSessionAvailable,
                    onCapture = captureAction,
                    onFinish = onFinish,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }
    }
}

@Composable
private fun CameraViewport(
    uiState: CameraProbeUiState,
    cameraSessionAvailable: Boolean,
    shutterFlashAlpha: Float,
    onFocus: (Float, Float) -> Unit,
    cameraPreview: @Composable (Modifier) -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .background(Color.Black)
            .testTag(CameraProbeTags.VIEWPORT),
    ) {
        cameraPreview(Modifier.fillMaxSize())

        if (uiState.gate is CameraGateState.Ready && cameraSessionAvailable) {
            Box(
                modifier = Modifier
                    .matchParentSize()
                    .pointerInput(Unit) {
                        detectTapGestures { offset -> onFocus(offset.x, offset.y) }
                    },
            )
            uiState.focusIndicator?.let { FocusIndicator(it) }
        }

        if (shutterFlashAlpha > 0f) {
            Box(
                modifier = Modifier
                    .matchParentSize()
                    .background(Color.White.copy(alpha = shutterFlashAlpha)),
            )
        }

        if (uiState.gate is CameraGateState.StartingCamera || !cameraSessionAvailable) {
            Box(
                modifier = Modifier
                    .matchParentSize()
                    .background(Color.Black.copy(alpha = 0.42f)),
                contentAlignment = Alignment.Center,
            ) {
                Column(
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(14.dp),
                ) {
                    CircularProgressIndicator(color = Color.White)
                    Text(stringResource(R.string.camera_starting), color = Color.White)
                }
            }
        }
    }
}

@Composable
private fun FocusIndicator(focus: FocusIndicatorState) {
    val size = 56.dp
    val halfSizePx = with(LocalDensity.current) { size.toPx() / 2f }
    val color = when (focus.status) {
        FocusStatus.FOCUSING -> Color.White
        FocusStatus.SUCCESS -> Color(0xFF70E000)
        FocusStatus.FAILED -> Color(0xFFFFB703)
    }
    Box(
        modifier = Modifier
            .offset {
                IntOffset(
                    x = (focus.x - halfSizePx).roundToInt(),
                    y = (focus.y - halfSizePx).roundToInt(),
                )
            }
            .size(size)
            .border(2.dp, color, RoundedCornerShape(8.dp))
            .testTag(CameraProbeTags.FOCUS),
    )
}

@Composable
private fun CameraControls(
    uiState: CameraProbeUiState,
    cameraSessionAvailable: Boolean,
    onCapture: () -> Unit,
    onFinish: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .background(Color.Black.copy(alpha = 0.70f))
            .padding(horizontal = 16.dp, vertical = 12.dp)
            .testTag(CameraProbeTags.CONTROLS),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (uiState.captureJobs.isNotEmpty()) {
            LazyRow(
                modifier = Modifier
                    .fillMaxWidth()
                    .height(72.dp)
                    .testTag(CameraProbeTags.THUMBNAILS),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(uiState.captureJobs, key = CaptureJob::captureId) { job ->
                    CaptureThumbnail(job)
                }
            }
        }

        val normalizingCount = uiState.captureJobs.count {
            it.stage == CaptureStage.NORMALIZING
        }
        Text(
            text = stringResource(
                R.string.camera_capture_status,
                uiState.captureJobs.size,
                uiState.pendingSaveCount,
                normalizingCount,
            ),
            color = Color.White,
            style = MaterialTheme.typography.bodySmall,
        )
        uiState.captureResolution?.let { resolution ->
            Text(
                text = stringResource(
                    R.string.camera_capture_resolution,
                    resolution.width,
                    resolution.height,
                ),
                color = Color.White.copy(alpha = 0.72f),
                style = MaterialTheme.typography.labelSmall,
            )
        }
        Text(
            text = stringResource(
                if (uiState.postviewSupported) {
                    R.string.camera_postview_supported
                } else {
                    R.string.camera_postview_unsupported
                },
            ),
            color = Color.White.copy(alpha = 0.72f),
            style = MaterialTheme.typography.labelSmall,
        )

        if (uiState.pendingSaveCount >= CameraCaptureProfile.MAX_PENDING_CAPTURES) {
            Text(
                text = stringResource(R.string.camera_queue_full),
                color = Color(0xFFFFD166),
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.testTag(CameraProbeTags.QUEUE_FULL),
            )
        }

        if (uiState.finishRequested) {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                CircularProgressIndicator(
                    modifier = Modifier.size(20.dp),
                    strokeWidth = 2.dp,
                    color = Color.White,
                )
                Text(
                    text = stringResource(
                        R.string.camera_finishing,
                        uiState.pendingWorkCount,
                    ),
                    color = Color.White,
                    modifier = Modifier.padding(start = 10.dp),
                )
            }
        } else {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                OutlinedButton(
                    onClick = onFinish,
                    enabled = uiState.captureJobs.isNotEmpty(),
                    modifier = Modifier.testTag(CameraProbeTags.FINISH),
                ) {
                    Text(stringResource(R.string.camera_finish))
                }
                Button(
                    onClick = onCapture,
                    enabled = uiState.canCapture && cameraSessionAvailable,
                    modifier = Modifier
                        .size(72.dp)
                        .clip(CircleShape)
                        .testTag(CameraProbeTags.SHUTTER),
                    contentPadding = PaddingValues(0.dp),
                ) {
                    Text(stringResource(R.string.camera_shutter))
                }
            }
        }
    }
}

@Composable
private fun CaptureThumbnail(job: CaptureJob) {
    val readyBitmap by produceState<ImageBitmap?>(
        initialValue = null,
        key1 = job.artifact?.normalizedPath,
    ) {
        value = job.artifact?.normalizedPath?.let { path ->
            withContext(Dispatchers.IO) {
                BitmapFactory.decodeFile(path)?.asImageBitmap()
            }
        }
    }
    val bitmap = job.postviewBitmap?.asImageBitmap() ?: readyBitmap

    Box(
        modifier = Modifier
            .size(72.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(Color(0xFF272727)),
        contentAlignment = Alignment.Center,
    ) {
        if (bitmap != null) {
            Image(
                bitmap = bitmap,
                contentDescription = stringResource(R.string.camera_thumbnail_description),
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
            )
        } else if (job.stage == CaptureStage.FAILED) {
            Text("!", color = Color(0xFFFF6B6B), fontWeight = FontWeight.Bold)
        } else {
            CircularProgressIndicator(
                modifier = Modifier.size(24.dp),
                strokeWidth = 2.dp,
                color = Color.White,
            )
        }
        Text(
            text = stageLabel(job.stage),
            color = Color.White,
            style = MaterialTheme.typography.labelSmall,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .background(Color.Black.copy(alpha = 0.62f))
                .padding(vertical = 2.dp),
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun ResultsContent(
    jobs: List<CaptureJob>,
    onContinue: () -> Unit,
    onBack: () -> Unit,
    onRetryJob: (String) -> Unit,
    onRemoveJob: (String) -> Unit,
) {
    LazyColumn(
        modifier = Modifier
            .fillMaxSize()
            .testTag(CameraProbeTags.RESULTS),
        contentPadding = PaddingValues(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        item {
            Text(
                text = stringResource(R.string.camera_results_title, jobs.size),
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
            )
        }
        items(jobs, key = CaptureJob::captureId) { job ->
            if (job.artifact != null) {
                CaptureResultCard(job.artifact)
            } else {
                FailedCaptureCard(
                    job = job,
                    onRetry = { onRetryJob(job.captureId) },
                    onRemove = { onRemoveJob(job.captureId) },
                )
            }
        }
        item {
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                OutlinedButton(onClick = onBack, modifier = Modifier.weight(1f)) {
                    Text(stringResource(R.string.camera_back_home))
                }
                Button(onClick = onContinue, modifier = Modifier.weight(1f)) {
                    Text(stringResource(R.string.camera_continue))
                }
            }
        }
    }
}

@Composable
private fun CaptureResultCard(artifact: CaptureArtifact) {
    val previewBitmap by produceState<ImageBitmap?>(
        initialValue = null,
        key1 = artifact.normalizedPath,
    ) {
        value = withContext(Dispatchers.IO) {
            BitmapFactory.decodeFile(artifact.normalizedPath)?.asImageBitmap()
        }
    }
    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.surfaceContainer,
        ),
    ) {
        Column(
            modifier = Modifier.padding(14.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .heightIn(min = 160.dp, max = 320.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(Color.Black),
                contentAlignment = Alignment.Center,
            ) {
                previewBitmap?.let { bitmap ->
                    Image(
                        bitmap = bitmap,
                        contentDescription = stringResource(
                            R.string.camera_result_image_description,
                        ),
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Fit,
                    )
                } ?: CircularProgressIndicator(color = Color.White)
            }
            MetricText(
                stringResource(
                    R.string.camera_original_metrics,
                    artifact.originalWidth,
                    artifact.originalHeight,
                    formatBytes(artifact.originalBytes),
                ),
            )
            MetricText(
                stringResource(
                    R.string.camera_normalized_metrics,
                    artifact.normalizedWidth,
                    artifact.normalizedHeight,
                    formatBytes(artifact.normalizedBytes),
                ),
            )
            MetricText(
                stringResource(
                    R.string.camera_rotation_metrics,
                    artifact.sourceRotationDegrees,
                ),
            )
            MetricText(
                stringResource(
                    R.string.camera_capture_started_metrics,
                    formatDuration(artifact.timings.requestToCaptureStartedMillis),
                ),
            )
            MetricText(
                stringResource(
                    R.string.camera_postview_metrics,
                    formatDuration(artifact.timings.requestToPostviewMillis),
                ),
            )
            MetricText(
                stringResource(
                    R.string.camera_saved_metrics,
                    artifact.timings.requestToImageSavedMillis,
                ),
            )
            MetricText(
                stringResource(
                    R.string.camera_duration_metrics,
                    artifact.timings.normalizationMillis,
                ),
            )
            MetricText(stringResource(R.string.camera_capture_id, artifact.captureId))
        }
    }
}

@Composable
private fun FailedCaptureCard(
    job: CaptureJob,
    onRetry: () -> Unit,
    onRemove: () -> Unit,
) {
    Card(
        colors = CardDefaults.cardColors(
            containerColor = MaterialTheme.colorScheme.errorContainer,
        ),
    ) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(
                text = stringResource(R.string.camera_error_title),
                fontWeight = FontWeight.Bold,
            )
            Text(job.errorMessage ?: stringResource(R.string.camera_error_unknown))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(onClick = onRetry) {
                    Text(stringResource(R.string.camera_retry))
                }
                OutlinedButton(onClick = onRemove) {
                    Text(stringResource(R.string.camera_remove))
                }
            }
        }
    }
}

@Composable
private fun MetricText(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}

@Composable
private fun ErrorContent(
    message: String,
    onRetry: () -> Unit,
) {
    StatusCard(
        title = stringResource(R.string.camera_error_title),
        description = message,
        tag = CameraProbeTags.ERROR,
    ) {
        Button(
            onClick = onRetry,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text(stringResource(R.string.camera_retry))
        }
    }
}

@Composable
private fun ProgressContent(
    message: String,
    tag: String,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .testTag(tag),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            CircularProgressIndicator()
            Text(
                text = message,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(horizontal = 24.dp),
            )
        }
    }
}

private fun stageLabel(stage: CaptureStage): String = when (stage) {
    CaptureStage.QUEUED -> "排队"
    CaptureStage.CAPTURING -> "拍摄"
    CaptureStage.SAVING -> "保存"
    CaptureStage.NORMALIZING -> "处理"
    CaptureStage.READY -> "完成"
    CaptureStage.FAILED -> "失败"
}

private fun Context.hasCameraPermission(): Boolean =
    ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) ==
        PackageManager.PERMISSION_GRANTED

private fun Context.openAppSettings() {
    startActivity(
        Intent(
            Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
            Uri.fromParts("package", packageName, null),
        ),
    )
}

private fun formatDuration(durationMillis: Long?): String =
    durationMillis?.let { "$it ms" } ?: "不支持"

private fun formatBytes(bytes: Long): String = when {
    bytes >= 1024L * 1024L -> String.format(
        Locale.US,
        "%.2f MB",
        bytes / (1024.0 * 1024.0),
    )

    bytes >= 1024L -> String.format(Locale.US, "%.1f KB", bytes / 1024.0)
    else -> "$bytes B"
}
