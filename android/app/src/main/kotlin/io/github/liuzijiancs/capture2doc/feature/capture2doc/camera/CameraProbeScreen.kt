package io.github.liuzijiancs.capture2doc.feature.capture2doc.camera

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import io.github.liuzijiancs.capture2doc.R
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import io.github.liuzijiancs.capture2doc.data.capture2doc.image.decodeSampledOrientedBitmap
import io.github.liuzijiancs.capture2doc.ui.theme.Capture2DocTheme
import io.github.liuzijiancs.capture2doc.ui.theme.statusColors
import java.io.File
import kotlin.math.roundToInt
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

object CameraProbeTags {
    const val SCREEN = "camera_probe_screen"
    const val PERMISSION = "camera_probe_permission"
    const val REQUESTING_PERMISSION = "camera_probe_requesting_permission"
    const val PERMISSION_DENIED = "camera_probe_permission_denied"
    const val STARTING = "camera_probe_starting"
    const val READY = "camera_probe_ready"
    const val ERROR = "camera_probe_error"
    const val UNAVAILABLE = "camera_probe_unavailable"
    const val BACK = "camera_probe_back"
    const val SHUTTER = "camera_probe_shutter"
    const val FINISH = "camera_probe_finish"
    const val FOCUS = "camera_probe_focus"
    const val THUMBNAILS = "camera_probe_thumbnails"
    const val VIEWPORT = "camera_probe_viewport"
    const val CONTROLS = "camera_probe_controls"
    const val PREVIEW = "camera_probe_candidate_preview"
    const val CANDIDATE_PROGRESS = "camera_probe_candidate_progress"
    const val CANDIDATE_ERROR = "camera_probe_candidate_error"
    const val CONNECTION_WARNING = "camera_connection_warning"

    fun candidate(pageId: String): String = "camera_probe_candidate_$pageId"
}

@Composable
internal fun CameraProbeRoute(
    onBack: (hasAcceptedCaptureOrPage: Boolean) -> Unit,
    onDraftReady: () -> Unit,
    modifier: Modifier = Modifier,
    interactionsEnabled: Boolean = true,
    disconnected: Boolean = false,
    viewModel: CameraProbeViewModel = viewModel(),
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val view = LocalView.current
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

    val systemBarController = remember(context, view) {
        (context as? Activity)?.let { activity ->
            WindowCompat.getInsetsController(activity.window, view)
        }
    }
    DisposableEffect(systemBarController) {
        val previousLightStatus = systemBarController?.isAppearanceLightStatusBars
        val previousLightNavigation = systemBarController?.isAppearanceLightNavigationBars
        onDispose {
            if (previousLightStatus != null) {
                systemBarController.isAppearanceLightStatusBars = previousLightStatus
            }
            if (previousLightNavigation != null) {
                systemBarController.isAppearanceLightNavigationBars = previousLightNavigation
            }
        }
    }
    SideEffect {
        systemBarController?.isAppearanceLightStatusBars = false
        systemBarController?.isAppearanceLightNavigationBars = false
    }

    val navigateBack = {
        onBack(uiState.hasAcceptedCaptureOrPage)
    }
    BackHandler(onBack = navigateBack)

    CameraProbeContent(
        uiState = uiState,
        cameraSessionAvailable = cameraSession != null,
        disconnected = disconnected,
        onBack = navigateBack,
        onRequestPermission = {
            viewModel.onPermissionRequestStarted()
            permissionLauncher.launch(Manifest.permission.CAMERA)
        },
        onOpenSettings = { context.openAppSettings() },
        onCapture = { if (interactionsEnabled) cameraSession?.let(viewModel::capture) },
        onFinish = {
            if (interactionsEnabled && viewModel.canFinishNow()) onDraftReady()
        },
        onDeleteCandidate = { if (interactionsEnabled) viewModel.deleteCandidate(it) },
        onMoveCandidate = { id, index -> if (interactionsEnabled) viewModel.moveCandidate(id, index) },
        onRetryCamera = viewModel::retryCamera,
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

@Composable
internal fun CameraProbeContent(
    uiState: CameraProbeUiState,
    cameraSessionAvailable: Boolean,
    onBack: () -> Unit,
    onRequestPermission: () -> Unit,
    onOpenSettings: () -> Unit,
    onCapture: () -> Unit,
    onFinish: () -> Unit,
    onDeleteCandidate: (String) -> Unit,
    onMoveCandidate: (String, Int) -> Unit,
    onRetryCamera: () -> Unit,
    onFocus: (Float, Float) -> Unit,
    onClearFocus: (Long) -> Unit,
    cameraPreview: @Composable (Modifier) -> Unit,
    landscapeLayoutOverride: Boolean? = null,
    disconnected: Boolean = false,
    modifier: Modifier = Modifier,
) {
    var previewPageId by rememberSaveable { mutableStateOf<String?>(null) }
    val previewCandidate = uiState.candidates.firstOrNull { it.pageId == previewPageId }

    LaunchedEffect(previewPageId, uiState.candidates) {
        if (previewPageId != null && previewCandidate == null) previewPageId = null
    }
    BackHandler(enabled = previewCandidate != null) { previewPageId = null }

    Box(
        modifier = modifier
            .fillMaxSize()
            .background(Color.Black)
            .testTag(CameraProbeTags.SCREEN),
    ) {
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
                onOpenCandidate = { previewPageId = it },
                onDeleteCandidate = onDeleteCandidate,
                onMoveCandidate = onMoveCandidate,
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

        if (previewCandidate == null) {
            CameraBackButton(
                onClick = onBack,
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .statusBarsPadding()
                    .padding(start = 12.dp, top = 8.dp),
            )
        }

        previewCandidate?.let { candidate ->
            CandidatePreview(
                candidate = candidate,
                onBack = { previewPageId = null },
                onDelete = {
                    onDeleteCandidate(candidate.pageId)
                    previewPageId = null
                },
            )
        }
        if (disconnected) Surface(
            modifier = Modifier.align(Alignment.TopCenter).statusBarsPadding()
                .padding(start = 64.dp, end = 64.dp, top = 12.dp)
                .testTag(CameraProbeTags.CONNECTION_WARNING),
            shape = RoundedCornerShape(12.dp),
            color = MaterialTheme.statusColors.warningContainer,
        ) {
            Text("未连接服务器 · 照片保存在本地", Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                color = MaterialTheme.statusColors.onWarning,
                style = MaterialTheme.typography.labelSmall)
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
    onCapture: () -> Unit,
    onFinish: () -> Unit,
    onOpenCandidate: (String) -> Unit,
    onDeleteCandidate: (String) -> Unit,
    onMoveCandidate: (String, Int) -> Unit,
    onFocus: (Float, Float) -> Unit,
    onClearFocus: (Long) -> Unit,
    cameraPreview: @Composable (Modifier) -> Unit,
    landscapeLayoutOverride: Boolean?,
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
        val viewportRatio = if (isLandscape) 4f / 3f else 3f / 4f

        Box(
            modifier = Modifier.fillMaxSize(),
            contentAlignment = Alignment.Center,
        ) {
            CameraViewport(
                uiState = uiState,
                cameraSessionAvailable = cameraSessionAvailable,
                shutterFlashAlpha = shutterFlashAlpha,
                onFocus = onFocus,
                cameraPreview = cameraPreview,
                modifier = Modifier.aspectRatio(viewportRatio),
            )
        }

        CameraControlsOverlay(
            uiState = uiState,
            cameraSessionAvailable = cameraSessionAvailable,
            onCapture = {
                if (uiState.canCapture && cameraSessionAvailable) {
                    onCapture()
                    haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                    shutterFlashVisible = true
                }
            },
            onFinish = onFinish,
            onOpenCandidate = onOpenCandidate,
            onDeleteCandidate = onDeleteCandidate,
            onMoveCandidate = onMoveCandidate,
        )
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

        if (
            uiState.gate is CameraGateState.StartingCamera ||
            !cameraSessionAvailable ||
            !uiState.draftReady
        ) {
            Box(
                modifier = Modifier
                    .matchParentSize()
                    .background(Color.Black.copy(alpha = 0.42f)),
                contentAlignment = Alignment.Center,
            ) {
                CircularProgressIndicator(color = Color.White)
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
        FocusStatus.SUCCESS -> MaterialTheme.statusColors.success
        FocusStatus.FAILED -> MaterialTheme.statusColors.warning
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
private fun CameraControlsOverlay(
    uiState: CameraProbeUiState,
    cameraSessionAvailable: Boolean,
    onCapture: () -> Unit,
    onFinish: () -> Unit,
    onOpenCandidate: (String) -> Unit,
    onDeleteCandidate: (String) -> Unit,
    onMoveCandidate: (String, Int) -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .testTag(CameraProbeTags.CONTROLS),
    ) {
        Box(
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .fillMaxWidth()
                .fillMaxHeight(0.46f)
                .background(
                    Brush.verticalGradient(
                        colors = listOf(
                            Color.Transparent,
                            Color.Black.copy(alpha = 0.35f),
                            Color.Black.copy(alpha = 0.94f),
                        ),
                    ),
                ),
        )

        if (uiState.candidates.isNotEmpty()) {
            CandidateStrip(
                candidates = uiState.candidates,
                onOpenCandidate = onOpenCandidate,
                onDeleteCandidate = onDeleteCandidate,
                onMoveCandidate = onMoveCandidate,
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .navigationBarsPadding()
                    .padding(bottom = 118.dp),
            )
        }

        ShutterButton(
            enabled = uiState.canCapture && cameraSessionAvailable,
            onClick = onCapture,
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .navigationBarsPadding()
                .padding(bottom = 22.dp),
        )

        Button(
            onClick = onFinish,
            enabled = uiState.canFinish,
            shape = RoundedCornerShape(18.dp),
            colors = ButtonDefaults.buttonColors(
                containerColor = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                disabledContainerColor = Color.White.copy(alpha = 0.16f),
                disabledContentColor = Color.White.copy(alpha = 0.42f),
            ),
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .navigationBarsPadding()
                .padding(end = 18.dp, bottom = 34.dp)
                .height(48.dp)
                .width(88.dp)
                .testTag(CameraProbeTags.FINISH),
            contentPadding = PaddingValues(horizontal = 12.dp),
        ) {
            Text(stringResource(R.string.camera_finish))
        }
    }
}

@Composable
private fun ShutterButton(
    enabled: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Button(
        onClick = onClick,
        enabled = enabled,
        shape = CircleShape,
        border = BorderStroke(
            width = 4.dp,
            color = if (enabled) {
                MaterialTheme.colorScheme.primary
            } else {
                Color.White.copy(alpha = 0.28f)
            },
        ),
        colors = ButtonDefaults.buttonColors(
            containerColor = Color.White,
            contentColor = Color.Black,
            disabledContainerColor = Color.White.copy(alpha = 0.36f),
            disabledContentColor = Color.Transparent,
        ),
        contentPadding = PaddingValues(0.dp),
        modifier = modifier
            .size(82.dp)
            .semantics { contentDescription = "拍照" }
            .testTag(CameraProbeTags.SHUTTER),
    ) {}
}

@Composable
private fun CandidateStrip(
    candidates: List<CameraCandidateUi>,
    onOpenCandidate: (String) -> Unit,
    onDeleteCandidate: (String) -> Unit,
    onMoveCandidate: (String, Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val haptics = LocalHapticFeedback.current
    val slotWidthPx = with(LocalDensity.current) { 76.dp.toPx() }
    var displayedCandidates by remember { mutableStateOf(candidates) }
    var draggingPageId by remember { mutableStateOf<String?>(null) }
    var dragStartIndex by remember { mutableStateOf(-1) }
    var dragOffsetX by remember { mutableFloatStateOf(0f) }

    LaunchedEffect(candidates) {
        if (draggingPageId == null) displayedCandidates = candidates
    }
    LaunchedEffect(candidates.lastOrNull()?.pageId) {
        if (candidates.isNotEmpty() && draggingPageId == null) {
            listState.animateScrollToItem(candidates.lastIndex)
        }
    }

    LazyRow(
        state = listState,
        modifier = modifier
            .fillMaxWidth()
            .height(76.dp)
            .testTag(CameraProbeTags.THUMBNAILS),
        contentPadding = PaddingValues(horizontal = 14.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        items(displayedCandidates, key = CameraCandidateUi::pageId) { candidate ->
            val isDragging = draggingPageId == candidate.pageId
            val displayedIndex = displayedCandidates.indexOfFirst {
                it.pageId == candidate.pageId
            }
            CandidateThumbnail(
                candidate = candidate.copy(pageNumber = displayedIndex + 1),
                isDragging = isDragging,
                dragOffsetX = if (isDragging) dragOffsetX else 0f,
                onOpen = { onOpenCandidate(candidate.pageId) },
                onDelete = { onDeleteCandidate(candidate.pageId) },
                onDragStart = {
                    draggingPageId = candidate.pageId
                    dragStartIndex = displayedIndex
                    dragOffsetX = 0f
                    haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                },
                onDrag = { deltaX ->
                    if (draggingPageId != candidate.pageId) return@CandidateThumbnail
                    dragOffsetX += deltaX
                    val currentIndex = displayedCandidates.indexOfFirst {
                        it.pageId == candidate.pageId
                    }
                    val direction = when {
                        dragOffsetX > slotWidthPx * 0.55f -> 1
                        dragOffsetX < -slotWidthPx * 0.55f -> -1
                        else -> 0
                    }
                    val targetIndex = currentIndex + direction
                    if (direction != 0 && targetIndex in displayedCandidates.indices) {
                        displayedCandidates = displayedCandidates.move(currentIndex, targetIndex)
                        dragOffsetX -= direction * slotWidthPx
                        scope.launch { listState.animateScrollToItem(targetIndex) }
                    }
                },
                onDragEnd = {
                    val pageId = draggingPageId
                    val finalIndex = displayedCandidates.indexOfFirst { it.pageId == pageId }
                    draggingPageId = null
                    dragOffsetX = 0f
                    if (pageId != null && finalIndex >= 0 && finalIndex != dragStartIndex) {
                        onMoveCandidate(pageId, finalIndex)
                    } else {
                        displayedCandidates = candidates
                    }
                    dragStartIndex = -1
                },
                onDragCancel = {
                    draggingPageId = null
                    dragStartIndex = -1
                    dragOffsetX = 0f
                    displayedCandidates = candidates
                },
            )
        }
    }
}

@Composable
private fun CandidateThumbnail(
    candidate: CameraCandidateUi,
    isDragging: Boolean,
    dragOffsetX: Float,
    onOpen: () -> Unit,
    onDelete: () -> Unit,
    onDragStart: () -> Unit,
    onDrag: (Float) -> Unit,
    onDragEnd: () -> Unit,
    onDragCancel: () -> Unit,
) {
    val bitmap = rememberCandidateImage(candidate, maxEdgePx = 512)
    val stateLabel = candidateStateLabel(candidate.state)

    Box(
        modifier = Modifier
            .size(68.dp)
            .graphicsLayer {
                translationX = dragOffsetX
                scaleX = if (isDragging) 1.07f else 1f
                scaleY = if (isDragging) 1.07f else 1f
            }
            .clip(RoundedCornerShape(14.dp))
            .background(Color(0xFF202124))
            .border(
                width = if (isDragging) 3.dp else 1.dp,
                color = if (isDragging) {
                    MaterialTheme.colorScheme.primary
                } else {
                    Color.White.copy(alpha = 0.34f)
                },
                shape = RoundedCornerShape(14.dp),
            )
            .semantics {
                contentDescription = "第 ${candidate.pageNumber} 页"
                stateDescription = stateLabel
            }
            .testTag(CameraProbeTags.candidate(candidate.pageId))
            .pointerInput(candidate.pageId, candidate.canDelete) {
                detectTapGestures(
                    onTap = { onOpen() },
                    onDoubleTap = {
                        if (candidate.canDelete) onDelete()
                    },
                )
            }
            .pointerInput(candidate.pageId) {
                detectDragGesturesAfterLongPress(
                    onDragStart = { onDragStart() },
                    onDrag = { change, dragAmount ->
                        change.consume()
                        onDrag(dragAmount.x)
                    },
                    onDragEnd = onDragEnd,
                    onDragCancel = onDragCancel,
                )
            },
        contentAlignment = Alignment.Center,
    ) {
        bitmap?.let { image ->
            Image(
                bitmap = image,
                contentDescription = null,
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Crop,
            )
        }

        when (candidate.state) {
            ScanPageState.CAPTURING -> CircularProgressIndicator(
                modifier = Modifier
                    .size(24.dp)
                    .testTag(CameraProbeTags.CANDIDATE_PROGRESS),
                strokeWidth = 2.dp,
                color = Color.White,
            )

            ScanPageState.NORMALIZING -> CircularProgressIndicator(
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .padding(6.dp)
                    .size(16.dp)
                    .testTag(CameraProbeTags.CANDIDATE_PROGRESS),
                strokeWidth = 2.dp,
                color = MaterialTheme.colorScheme.primary,
            )

            ScanPageState.FAILED,
            ScanPageState.RETAKE_REQUIRED,
            -> ErrorBadge(Modifier.align(Alignment.Center))

            ScanPageState.READY -> Unit
        }

        Text(
            text = candidate.pageNumber.toString(),
            color = MaterialTheme.colorScheme.onPrimary,
            style = MaterialTheme.typography.labelSmall,
            fontWeight = FontWeight.Bold,
            textAlign = TextAlign.Center,
            modifier = Modifier
                .align(Alignment.BottomStart)
                .padding(5.dp)
                .size(22.dp)
                .clip(CircleShape)
                .background(MaterialTheme.colorScheme.primary)
                .padding(top = 3.dp),
        )
    }
}

@Composable
private fun ErrorBadge(modifier: Modifier = Modifier) {
    Box(
        modifier = modifier
            .size(28.dp)
            .clip(CircleShape)
            .background(MaterialTheme.colorScheme.error)
            .testTag(CameraProbeTags.CANDIDATE_ERROR),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = "!",
            color = MaterialTheme.colorScheme.onError,
            fontWeight = FontWeight.Black,
        )
    }
}

@Composable
private fun CandidatePreview(
    candidate: CameraCandidateUi,
    onBack: () -> Unit,
    onDelete: () -> Unit,
) {
    val bitmap = rememberCandidateImage(candidate, maxEdgePx = 1280)
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.Black)
            .testTag(CameraProbeTags.PREVIEW)
            .pointerInput(candidate.pageId, candidate.canDelete) {
                detectTapGestures(
                    onDoubleTap = {
                        if (candidate.canDelete) onDelete()
                    },
                )
            },
        contentAlignment = Alignment.Center,
    ) {
        bitmap?.let { image ->
            Image(
                bitmap = image,
                contentDescription = "第 ${candidate.pageNumber} 页大图",
                modifier = Modifier.fillMaxSize(),
                contentScale = ContentScale.Fit,
            )
        }
        if (bitmap == null && candidate.state == ScanPageState.CAPTURING) {
            CircularProgressIndicator(color = Color.White)
        }
        if (
            candidate.state == ScanPageState.FAILED ||
            candidate.state == ScanPageState.RETAKE_REQUIRED
        ) {
            ErrorBadge()
        }
        CameraBackButton(
            onClick = onBack,
            modifier = Modifier
                .align(Alignment.TopStart)
                .statusBarsPadding()
                .padding(start = 12.dp, top = 8.dp),
        )
    }
}

@Composable
private fun CameraBackButton(
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    IconButton(
        onClick = onClick,
        modifier = modifier
            .size(48.dp)
            .clip(CircleShape)
            .background(Color.Black.copy(alpha = 0.48f))
            .testTag(CameraProbeTags.BACK),
    ) {
        Icon(
            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
            contentDescription = stringResource(R.string.camera_probe_back),
            tint = Color.White,
        )
    }
}

@Composable
private fun rememberCandidateImage(
    candidate: CameraCandidateUi,
    maxEdgePx: Int,
): ImageBitmap? {
    candidate.postviewBitmap?.let { return it.asImageBitmap() }
    val path = remember(
        candidate.originalPath,
        candidate.normalizedPath,
        candidate.state,
    ) {
        when {
            File(candidate.originalPath).isFile -> candidate.originalPath
            File(candidate.normalizedPath).isFile -> candidate.normalizedPath
            else -> null
        }
    }
    val stamp = path?.let { File(it).lastModified() } ?: 0L
    val loaded by produceState<ImageBitmap?>(
        initialValue = null,
        key1 = path,
        key2 = stamp,
        key3 = maxEdgePx,
    ) {
        value = path?.let { imagePath ->
            withContext(Dispatchers.IO) {
                decodeSampledOrientedBitmap(File(imagePath), maxEdgePx)?.asImageBitmap()
            }
        }
    }
    return loaded
}

private fun candidateStateLabel(state: ScanPageState): String = when (state) {
    ScanPageState.CAPTURING -> "正在保存"
    ScanPageState.NORMALIZING -> "原图已保存，正在生成派生图"
    ScanPageState.READY -> "已就绪"
    ScanPageState.FAILED -> "处理失败"
    ScanPageState.RETAKE_REQUIRED -> "等待重拍"
}

private fun <T> List<T>.move(fromIndex: Int, toIndex: Int): List<T> {
    if (fromIndex == toIndex) return this
    return toMutableList().apply {
        add(toIndex, removeAt(fromIndex))
    }
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
            CircularProgressIndicator(color = Color.White)
            Text(
                text = message,
                color = Color.White,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(horizontal = 24.dp),
            )
        }
    }
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

private fun previewCandidate(
    pageId: String,
    pageNumber: Int,
    state: ScanPageState,
    safeOriginal: Boolean,
): CameraCandidateUi = CameraCandidateUi(
    pageId = pageId,
    pageNumber = pageNumber,
    originalPath = "/preview/$pageId/original.jpg",
    normalizedPath = "/preview/$pageId/normalized_1280.jpg",
    state = state,
    hasSafeOriginal = safeOriginal,
    canDelete = state != ScanPageState.CAPTURING,
)

@Composable
private fun CameraPreviewFixture(
    state: CameraProbeUiState,
    landscape: Boolean = false,
    disconnected: Boolean = false,
) {
    Capture2DocTheme(darkTheme = true) {
        CameraProbeContent(
            uiState = state.copy(draftReady = true),
            cameraSessionAvailable = true,
            onBack = {},
            onRequestPermission = {},
            onOpenSettings = {},
            onCapture = {},
            onFinish = {},
            onDeleteCandidate = {},
            onMoveCandidate = { _, _ -> },
            onRetryCamera = {},
            onFocus = { _, _ -> },
            onClearFocus = {},
            cameraPreview = { previewModifier ->
                Box(previewModifier.background(Color(0xFF46515B)))
            },
            landscapeLayoutOverride = landscape,
            disconnected = disconnected,
        )
    }
}

@Preview(name = "Camera empty", widthDp = 393, heightDp = 852)
@Composable
private fun CameraEmptyPreview() {
    CameraPreviewFixture(CameraProbeUiState(gate = CameraGateState.Ready))
}

@Preview(name = "Camera offline", widthDp = 393, heightDp = 852)
@Preview(name = "Camera offline large font", widthDp = 360, heightDp = 780, fontScale = 1.5f)
@Composable
private fun CameraOfflinePreview() {
    CameraPreviewFixture(CameraProbeUiState(gate = CameraGateState.Ready), disconnected = true)
}

@Preview(name = "Camera saving", widthDp = 393, heightDp = 852)
@Composable
private fun CameraSavingPreview() {
    CameraPreviewFixture(
        CameraProbeUiState(
            gate = CameraGateState.Ready,
            candidates = listOf(
                previewCandidate("saving", 1, ScanPageState.CAPTURING, false),
            ),
        ),
    )
}

@Preview(name = "Camera pages", widthDp = 393, heightDp = 852)
@Composable
private fun CameraPagesPreview() {
    CameraPreviewFixture(
        CameraProbeUiState(
            gate = CameraGateState.Ready,
            candidates = listOf(
                previewCandidate("one", 1, ScanPageState.READY, true),
                previewCandidate("two", 2, ScanPageState.NORMALIZING, true),
                previewCandidate("three", 3, ScanPageState.READY, true),
            ),
        ),
    )
}

@Preview(name = "Camera failure", widthDp = 393, heightDp = 852)
@Composable
private fun CameraFailurePreview() {
    CameraPreviewFixture(
        CameraProbeUiState(
            gate = CameraGateState.Ready,
            candidates = listOf(
                previewCandidate("failed", 1, ScanPageState.FAILED, false),
            ),
        ),
    )
}

@Preview(
    name = "Camera dark",
    widthDp = 393,
    heightDp = 852,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun CameraDarkPreview() {
    CameraPagesPreview()
}

@Preview(name = "Camera landscape", widthDp = 852, heightDp = 393)
@Composable
private fun CameraLandscapePreview() {
    CameraPreviewFixture(
        state = CameraProbeUiState(
            gate = CameraGateState.Ready,
            candidates = listOf(
                previewCandidate("one", 1, ScanPageState.READY, true),
                previewCandidate("two", 2, ScanPageState.READY, true),
            ),
        ),
        landscape = true,
    )
}
