package io.github.liuzijiancs.capture2doc.feature.capture2doc.draft

import android.content.res.Configuration
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
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
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.liuzijiancs.capture2doc.core.model.ScanDraft
import io.github.liuzijiancs.capture2doc.core.model.ScanPage
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import io.github.liuzijiancs.capture2doc.data.capture2doc.image.decodeSampledOrientedBitmap
import io.github.liuzijiancs.capture2doc.ui.theme.Capture2DocTheme
import io.github.liuzijiancs.capture2doc.ui.theme.statusColors
import java.io.File
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private const val MIN_PAGE_SIZE_DP = 150
private const val DOCUMENT_PAGE_ASPECT_RATIO = 0.72f

internal object ScanDraftTags {
    const val GRID = "scan_draft_grid"
    const val PREVIEW_PAGER = "scan_draft_preview_pager"
    const val EMPTY = "scan_draft_empty"
    const val PAGE_PREFIX = "scan_draft_page_"
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ScanDraftRoute(
    onBackToHome: () -> Unit,
    onSaveAndExit: () -> Unit,
    onContinueCapture: () -> Unit,
    onRetake: (String) -> Unit,
    retakePreparationInProgress: Boolean = false,
    modifier: Modifier = Modifier,
    viewModel: ScanDraftViewModel = androidx.lifecycle.viewmodel.compose.viewModel(),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    LaunchedEffect(Unit) {
        viewModel.ensureDraft()
    }

    val interactionsBlocked = uiState.isMutating || retakePreparationInProgress
    val displayedState = if (interactionsBlocked == uiState.isMutating) {
        uiState
    } else {
        uiState.copy(isMutating = interactionsBlocked)
    }

    val navigateBack = {
        when {
            interactionsBlocked -> Unit
            uiState.armedDeletePageId != null -> viewModel.cancelDelete()
            uiState.previewPageId != null -> viewModel.closePreview()
            else -> onBackToHome()
        }
    }
    BackHandler(onBack = navigateBack)

    ScanDraftScreen(
        uiState = displayedState,
        onNavigateBack = navigateBack,
        onSaveAndExit = {
            viewModel.cancelDelete()
            onSaveAndExit()
        },
        onContinueCapture = {
            viewModel.cancelDelete()
            onContinueCapture()
        },
        onOpenPreview = viewModel::openPreview,
        onClosePreview = viewModel::closePreview,
        onToggleDelete = viewModel::toggleDelete,
        onDelete = viewModel::deletePage,
        onReorder = viewModel::reorder,
        onRetake = { pageId ->
            viewModel.cancelDelete()
            onRetake(pageId)
        },
        onPreviewPageChanged = viewModel::openPreview,
        imagePathForPage = viewModel::previewImagePath,
        modifier = modifier,
    )
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun ScanDraftScreen(
    uiState: ScanDraftUiState,
    onNavigateBack: () -> Unit,
    onSaveAndExit: () -> Unit,
    onContinueCapture: () -> Unit,
    onOpenPreview: (String) -> Unit,
    onClosePreview: () -> Unit,
    onToggleDelete: (String) -> Unit,
    onDelete: (String) -> Unit,
    onReorder: (String, Int) -> Unit,
    onRetake: (String) -> Unit,
    onPreviewPageChanged: (String) -> Unit,
    imagePathForPage: (String) -> String?,
    modifier: Modifier = Modifier,
) {
    val pages = uiState.draft?.pages.orEmpty()

    if (uiState.isLoading) {
        Box(
            modifier = modifier.fillMaxSize(),
            contentAlignment = Alignment.Center,
        ) {
            CircularProgressIndicator()
        }
        return
    }

    Scaffold(
        modifier = modifier.fillMaxSize(),
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = "页面整理",
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                navigationIcon = {
                    IconButton(
                        onClick = onNavigateBack,
                        enabled = !uiState.isMutating,
                    ) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "返回",
                        )
                    }
                },
                actions = {
                    TextButton(
                        onClick = onSaveAndExit,
                        enabled = pages.isNotEmpty() && !uiState.isMutating,
                    ) {
                        Text("保存并退出")
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                ),
            )
        },
    ) { innerPadding ->
        if (uiState.previewPageId == null) {
            ScanDraftGrid(
                pages = pages,
                armedDeletePageId = uiState.armedDeletePageId,
                onOpenPreview = onOpenPreview,
                onToggleDelete = onToggleDelete,
                onDelete = onDelete,
                onReorder = onReorder,
                onContinueCapture = onContinueCapture,
                onRetake = onRetake,
                imagePathForPage = imagePathForPage,
                interactionsEnabled = !uiState.isMutating,
                modifier = Modifier.padding(innerPadding),
            )
        } else {
            ScanDraftPreview(
                pages = pages,
                initialPageId = uiState.previewPageId,
                armedDeletePageId = uiState.armedDeletePageId,
                onClose = onClosePreview,
                onToggleDelete = onToggleDelete,
                onDelete = { pageId ->
                    onDelete(pageId)
                    onClosePreview()
                },
                onRetake = onRetake,
                onPreviewPageChanged = onPreviewPageChanged,
                imagePathForPage = imagePathForPage,
                interactionsEnabled = !uiState.isMutating,
                modifier = Modifier.padding(innerPadding),
            )
        }

        if (pages.isEmpty()) {
            EmptyDraftHint(
                onContinueCapture = onContinueCapture,
                enabled = !uiState.isMutating,
                modifier = Modifier.padding(innerPadding),
            )
        }
    }
}

@Composable
private fun ScanDraftGrid(
    pages: List<ScanPage>,
    armedDeletePageId: String?,
    onOpenPreview: (String) -> Unit,
    onToggleDelete: (String) -> Unit,
    onDelete: (String) -> Unit,
    onReorder: (String, Int) -> Unit,
    onContinueCapture: () -> Unit,
    onRetake: (String) -> Unit,
    imagePathForPage: (String) -> String?,
    interactionsEnabled: Boolean,
    modifier: Modifier = Modifier,
) {
    if (pages.isEmpty()) {
        return
    }

    var draggingId by remember { mutableStateOf<String?>(null) }
    var draggingOffsetX by remember { androidx.compose.runtime.mutableFloatStateOf(0f) }
    var draggingOffsetY by remember { androidx.compose.runtime.mutableFloatStateOf(0f) }
    val itemRects = remember { mutableStateMapOf<String, androidx.compose.ui.geometry.Rect>() }

    val targetIndex by remember(draggingId, itemRects, draggingOffsetX, draggingOffsetY, pages) {
        derivedStateOf {
            if (draggingId == null) return@derivedStateOf -1
            val rect = itemRects[draggingId] ?: return@derivedStateOf -1
            val pointer = rect.center + androidx.compose.ui.geometry.Offset(draggingOffsetX, draggingOffsetY)
            var target = pages.indexOfFirst { it.pageId == draggingId }
            itemRects.forEach { (id, candidate) ->
                if (id != draggingId && candidate.contains(pointer)) {
                    target = pages.indexOfFirst { it.pageId == id }
                }
            }
            target
        }
    }

    LazyVerticalGrid(
        columns = GridCells.Adaptive(minSize = MIN_PAGE_SIZE_DP.dp),
        modifier = modifier
            .padding(12.dp)
            .testTag(ScanDraftTags.GRID),
        contentPadding = PaddingValues(bottom = 96.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(
            items = pages,
            key = ScanPage::pageId,
        ) { page ->
            val isArmed = armedDeletePageId == page.pageId
            val index = pages.indexOfFirst { it.pageId == page.pageId }
            val isDragging = draggingId == page.pageId

            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag(ScanDraftTags.PAGE_PREFIX + page.pageId)
                    .clip(RoundedCornerShape(12.dp))
                    .background(MaterialTheme.colorScheme.surfaceContainer)
                    .border(
                        width = if (isArmed) 2.dp else 0.dp,
                        color = if (isArmed) {
                            MaterialTheme.colorScheme.error
                        } else {
                            Color.Transparent
                        },
                        shape = RoundedCornerShape(12.dp),
                    )
                    .graphicsLayer {
                        translationX = if (isDragging) draggingOffsetX else 0f
                        translationY = if (isDragging) draggingOffsetY else 0f
                    }
                    .onGloballyPositioned { coordinates ->
                        itemRects[page.pageId] = coordinates.boundsInWindow()
                    }
                    .pointerInput(page.pageId) {
                        detectTapGestures(
                            onTap = {
                                if (!interactionsEnabled) return@detectTapGestures
                                if (armedDeletePageId != null) {
                                    onToggleDelete(armedDeletePageId)
                                } else {
                                    onOpenPreview(page.pageId)
                                }
                            },
                        )
                    }
                    .pointerInput(page.pageId) {
                        detectDragGesturesAfterLongPress(
                            onDragStart = {
                                if (!interactionsEnabled) return@detectDragGesturesAfterLongPress
                                draggingId = page.pageId
                                draggingOffsetX = 0f
                                draggingOffsetY = 0f
                                if (armedDeletePageId != null) {
                                    onToggleDelete(armedDeletePageId)
                                }
                            },
                            onDrag = { change, dragAmount ->
                                draggingOffsetX += dragAmount.x
                                draggingOffsetY += dragAmount.y
                                change.consume()
                            },
                            onDragCancel = {
                                draggingId = null
                                draggingOffsetX = 0f
                                draggingOffsetY = 0f
                            },
                            onDragEnd = {
                                val source = pages.indexOfFirst { it.pageId == draggingId }
                                val target = targetIndex
                                draggingId = null
                                draggingOffsetX = 0f
                                draggingOffsetY = 0f
                                if (source >= 0 && target >= 0 && source != target) {
                                    onReorder(page.pageId, target)
                                }
                            },
                        )
                    },
            ) {
                ScanDraftPageCard(
                    page = page,
                    index = index + 1,
                    isArmed = isArmed,
                    onDelete = {
                        when {
                            isArmed -> onDelete(page.pageId)
                            armedDeletePageId != null -> onToggleDelete(armedDeletePageId)
                            else -> onToggleDelete(page.pageId)
                        }
                    },
                    onRetake = { onRetake(page.pageId) },
                    imagePath = imagePathForPage(page.pageId),
                    interactionsEnabled = interactionsEnabled,
                    modifier = Modifier.fillMaxWidth(),
                )
            }
        }

        item {
            FilledTonalButton(
                onClick = onContinueCapture,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 4.dp),
                enabled = interactionsEnabled,
            ) {
                Text("继续拍摄")
            }
        }
    }
}

@Composable
private fun EmptyDraftHint(
    onContinueCapture: () -> Unit,
    enabled: Boolean = true,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier = modifier
            .fillMaxSize()
            .testTag(ScanDraftTags.EMPTY),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            modifier = Modifier.padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                text = "暂无页面",
                style = MaterialTheme.typography.titleMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Text(
                text = "拍摄文档后，这里会显示页面缩略图和顺序。",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Button(onClick = onContinueCapture, enabled = enabled) {
                Text("立即拍摄")
            }
        }
    }
}

@Composable
private fun ScanDraftPageCard(
    page: ScanPage,
    index: Int,
    isArmed: Boolean,
    onDelete: () -> Unit,
    onRetake: () -> Unit,
    imagePath: String?,
    interactionsEnabled: Boolean,
    modifier: Modifier = Modifier,
) {
    val imageBitmap by produceState<ImageBitmap?>(null, imagePath, page.state) {
        value = imagePath?.let { path ->
            withContext(Dispatchers.IO) {
                decodeSampledOrientedBitmap(File(path), maxEdgePx = 512)?.asImageBitmap()
            }
        }
    }

    val cardColors = MaterialTheme.statusColors
    val stateText = when (page.state) {
        ScanPageState.READY -> "页面 $index"
        ScanPageState.CAPTURING -> "拍照中"
        ScanPageState.NORMALIZING -> "处理中"
        ScanPageState.FAILED -> "处理失败"
        ScanPageState.RETAKE_REQUIRED -> "待重拍"
    }

    Column(modifier = modifier) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(DOCUMENT_PAGE_ASPECT_RATIO),
            contentAlignment = Alignment.Center,
        ) {
            if (imageBitmap != null) {
                androidx.compose.foundation.Image(
                    bitmap = imageBitmap!!,
                    contentDescription = "页面 $index",
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Crop,
                )
            } else {
                ScanPagePlaceholder(
                    title = stateText,
                    state = page.state,
                    modifier = Modifier.fillMaxSize(),
                )
            }

            if (isArmed) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = 0.42f)),
                )
            }

            if (isArmed) {
                Box(
                    modifier = Modifier
                        .align(Alignment.TopEnd)
                        .padding(6.dp)
                        .background(MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.9f))
                        .clip(RoundedCornerShape(12.dp)),
                ) {
                    Text(
                        text = "再次点击以删除",
                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                    )
                }
            }

            IconButton(
                onClick = onDelete,
                enabled = interactionsEnabled && page.state != ScanPageState.CAPTURING,
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .padding(6.dp)
                    .size(if (isArmed) 34.dp else 26.dp)
                    .background(
                        if (isArmed) {
                            MaterialTheme.colorScheme.error.copy(alpha = 0.85f)
                        } else {
                            MaterialTheme.colorScheme.surfaceContainerHigh
                        },
                        shape = CircleShape,
                    )
                    .clip(CircleShape),
            ) {
                Icon(
                    imageVector = Icons.Filled.Close,
                    contentDescription = if (isArmed) "确认删除" else "删除",
                    tint = if (isArmed) {
                        MaterialTheme.colorScheme.onError
                    } else {
                        cardColors.error
                    },
                )
            }

            Text(
                text = "${index.toString().padStart(2, '0')}",
                modifier = Modifier
                    .align(Alignment.BottomStart)
                    .padding(6.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(MaterialTheme.colorScheme.surfaceContainerHigh.copy(alpha = 0.82f))
                    .padding(horizontal = 6.dp, vertical = 2.dp),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }

        Card(
            colors = CardDefaults.cardColors(
                containerColor = MaterialTheme.colorScheme.surfaceContainer,
            ),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Row(
                modifier = Modifier
                    .padding(horizontal = 10.dp, vertical = 8.dp)
                    .fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(
                    onClick = onRetake,
                    enabled = interactionsEnabled && page.state != ScanPageState.CAPTURING,
                ) {
                    Text("重拍")
                }
            }
        }
    }
}

@Composable
private fun ScanDraftPreview(
    pages: List<ScanPage>,
    initialPageId: String?,
    armedDeletePageId: String?,
    onClose: () -> Unit,
    onToggleDelete: (String) -> Unit,
    onDelete: (String) -> Unit,
    onRetake: (String) -> Unit,
    onPreviewPageChanged: (String) -> Unit,
    imagePathForPage: (String) -> String?,
    interactionsEnabled: Boolean,
    modifier: Modifier = Modifier,
) {
    if (pages.isEmpty()) {
        EmptyDraftHint(
            onContinueCapture = onClose,
            enabled = interactionsEnabled,
            modifier = modifier,
        )
        return
    }

    val initialIndex = remember(initialPageId, pages) {
        pages.indexOfFirst { it.pageId == initialPageId }.coerceAtLeast(0)
    }
    val pagerState = rememberPagerState(initialPage = initialIndex) { pages.size }

    LaunchedEffect(pagerState.currentPage, pages) {
        pages.getOrNull(pagerState.currentPage)?.pageId?.let(onPreviewPageChanged)
    }

    HorizontalPager(
        state = pagerState,
        modifier = modifier
            .fillMaxSize()
            .testTag(ScanDraftTags.PREVIEW_PAGER),
    ) { index ->
        val page = pages[index]
        val isArmed = armedDeletePageId == page.pageId
        val imagePath = imagePathForPage(page.pageId)
        val imageBitmap by produceState<ImageBitmap?>(null, imagePath, page.state) {
            value = imagePath?.let { path ->
                withContext(Dispatchers.IO) {
                    decodeSampledOrientedBitmap(File(path), maxEdgePx = 1280)?.asImageBitmap()
                }
            }
        }
        val placeholderText = when (page.state) {
            ScanPageState.READY -> "页面 ${index + 1}"
            ScanPageState.CAPTURING -> "拍照中"
            ScanPageState.NORMALIZING -> "处理中"
            ScanPageState.FAILED -> "处理失败，可重拍或删除"
            ScanPageState.RETAKE_REQUIRED -> "待重拍"
        }

        Box(
            modifier = Modifier
                .fillMaxSize()
                .pointerInput(isArmed) {
                    detectTapGestures {
                        if (isArmed) onToggleDelete(page.pageId)
                    }
                },
        ) {
            if (imageBitmap == null) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(MaterialTheme.colorScheme.background),
                    contentAlignment = Alignment.Center,
                ) {
                    ScanPagePlaceholder(
                        title = placeholderText,
                        state = page.state,
                        modifier = Modifier
                            .fillMaxWidth(0.84f)
                            .aspectRatio(DOCUMENT_PAGE_ASPECT_RATIO)
                            .clip(RoundedCornerShape(16.dp)),
                    )
                }
            } else {
                androidx.compose.foundation.Image(
                    bitmap = imageBitmap!!,
                    contentDescription = "页面 ${index + 1}",
                    modifier = Modifier.fillMaxSize(),
                    contentScale = ContentScale.Fit,
                )
            }


            if (isArmed) {
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .background(Color.Black.copy(alpha = 0.42f)),
                )
            }

            OutlinedButton(
                onClick = {
                    if (isArmed) {
                        onToggleDelete(page.pageId)
                    } else {
                        onClose()
                    }
                },
                modifier = Modifier
                    .align(Alignment.TopStart)
                    .padding(12.dp),
                enabled = interactionsEnabled,
            ) {
                Text("返回")
            }

            Row(
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(12.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp),
            ) {
                TextButton(
                    onClick = {
                        if (isArmed) {
                            onDelete(page.pageId)
                        } else {
                            onToggleDelete(page.pageId)
                        }
                    },
                    enabled = interactionsEnabled && page.state != ScanPageState.CAPTURING,
                ) {
                    Text(if (isArmed) "确认删除" else "删除")
                }
                FilledTonalButton(
                    onClick = { onRetake(page.pageId) },
                    enabled = interactionsEnabled && page.state != ScanPageState.CAPTURING,
                ) {
                    Text("重拍")
                }
            }
        }
    }
}

@Composable
private fun ScanPagePlaceholder(
    title: String,
    state: ScanPageState,
    modifier: Modifier = Modifier,
) {
    val statusColors = MaterialTheme.statusColors
    val background = when (state) {
        ScanPageState.FAILED -> statusColors.errorContainer
        ScanPageState.RETAKE_REQUIRED -> statusColors.warningContainer
        else -> MaterialTheme.colorScheme.surfaceContainerHigh
    }
    val foreground = when (state) {
        ScanPageState.FAILED -> statusColors.onError
        ScanPageState.RETAKE_REQUIRED -> statusColors.onWarning
        else -> MaterialTheme.colorScheme.onSurfaceVariant
    }

    Column(
        modifier = modifier
            .background(background)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(
            text = title,
            style = MaterialTheme.typography.labelLarge,
            color = foreground,
        )
        listOf(1f, 0.88f, 0.94f, 0.68f, 0.82f).forEach { widthFraction ->
            Box(
                modifier = Modifier
                    .fillMaxWidth(widthFraction)
                    .size(height = 5.dp, width = 1.dp)
                    .background(
                        color = foreground.copy(alpha = 0.2f),
                        shape = RoundedCornerShape(3.dp),
                    ),
            )
        }
    }
}

private val PreviewReadyPages = listOf(
    previewPage("page-01", ScanPageState.READY),
    previewPage("page-02", ScanPageState.READY),
    previewPage("page-03", ScanPageState.READY),
    previewPage("page-04", ScanPageState.NORMALIZING),
)

private val PreviewRecoveryPages = listOf(
    previewPage("page-01", ScanPageState.READY),
    previewPage("page-02", ScanPageState.FAILED, "原图缺失或损坏，请重拍"),
    previewPage("page-03", ScanPageState.RETAKE_REQUIRED, "等待重新拍摄"),
)

private fun previewPage(
    pageId: String,
    state: ScanPageState,
    errorMessage: String? = null,
) = ScanPage(
    pageId = pageId,
    originalRelativePath = "pages/$pageId/original.jpg",
    normalizedRelativePath = "pages/$pageId/normalized_1280.jpg",
    state = state,
    errorMessage = errorMessage,
)

@Composable
private fun ScanDraftPrototype(
    initialPages: List<ScanPage>,
    initialPreviewPageId: String? = null,
    initialArmedDeletePageId: String? = null,
) {
    var pages by remember { mutableStateOf(initialPages) }
    var previewPageId by remember { mutableStateOf(initialPreviewPageId) }
    var armedDeletePageId by remember { mutableStateOf(initialArmedDeletePageId) }

    val navigateBack = {
        when {
            armedDeletePageId != null -> armedDeletePageId = null
            previewPageId != null -> previewPageId = null
        }
    }

    Capture2DocTheme {
        ScanDraftScreen(
            uiState = ScanDraftUiState(
                isLoading = false,
                draft = ScanDraft(
                    draftId = "preview-draft",
                    createdAtEpochMillis = 0L,
                    updatedAtEpochMillis = 0L,
                    pages = pages,
                ),
                previewPageId = previewPageId,
                armedDeletePageId = armedDeletePageId,
            ),
            onNavigateBack = navigateBack,
            onSaveAndExit = {},
            onContinueCapture = {
                val pageNumber = pages.size + 1
                pages = pages + previewPage("page-${pageNumber.toString().padStart(2, '0')}", ScanPageState.READY)
            },
            onOpenPreview = { pageId ->
                previewPageId = pageId
                armedDeletePageId = null
            },
            onClosePreview = { previewPageId = null },
            onToggleDelete = { pageId ->
                armedDeletePageId = if (armedDeletePageId == pageId) null else pageId
            },
            onDelete = { pageId ->
                pages = pages.filterNot { it.pageId == pageId }
                armedDeletePageId = null
            },
            onReorder = { pageId, targetIndex ->
                val sourceIndex = pages.indexOfFirst { it.pageId == pageId }
                if (sourceIndex >= 0 && targetIndex in pages.indices && sourceIndex != targetIndex) {
                    pages = pages.toMutableList().apply {
                        add(targetIndex, removeAt(sourceIndex))
                    }
                }
            },
            onRetake = { pageId ->
                pages = pages.map { page ->
                    if (page.pageId == pageId) {
                        page.copy(
                            state = ScanPageState.RETAKE_REQUIRED,
                            errorMessage = "等待重新拍摄",
                        )
                    } else {
                        page
                    }
                }
                previewPageId = null
            },
            onPreviewPageChanged = { pageId -> previewPageId = pageId },
            imagePathForPage = { null },
        )
    }
}

@Preview(name = "01 网格 - 浅色", showBackground = true, widthDp = 390, heightDp = 844)
@Composable
private fun ScanDraftGridLightPreview() {
    ScanDraftPrototype(initialPages = PreviewReadyPages)
}

@Preview(
    name = "02 网格 - 深色",
    showBackground = true,
    widthDp = 390,
    heightDp = 844,
    uiMode = Configuration.UI_MODE_NIGHT_YES,
)
@Composable
private fun ScanDraftGridDarkPreview() {
    ScanDraftPrototype(initialPages = PreviewReadyPages)
}

@Preview(name = "03 删除确认", showBackground = true, widthDp = 390, heightDp = 844)
@Composable
private fun ScanDraftDeletePreview() {
    ScanDraftPrototype(
        initialPages = PreviewReadyPages,
        initialArmedDeletePageId = "page-02",
    )
}

@Preview(name = "04 大图浏览", showBackground = true, widthDp = 390, heightDp = 844)
@Composable
private fun ScanDraftLargePreview() {
    ScanDraftPrototype(
        initialPages = PreviewReadyPages,
        initialPreviewPageId = "page-02",
    )
}

@Preview(name = "05 空草稿", showBackground = true, widthDp = 390, heightDp = 844)
@Composable
private fun ScanDraftEmptyPreview() {
    ScanDraftPrototype(initialPages = emptyList())
}

@Preview(name = "06 恢复占位", showBackground = true, widthDp = 390, heightDp = 844)
@Composable
private fun ScanDraftRecoveryPreview() {
    ScanDraftPrototype(initialPages = PreviewRecoveryPages)
}
