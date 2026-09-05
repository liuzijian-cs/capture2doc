package io.github.liuzijiancs.capture2doc.ui.document

import android.animation.ValueAnimator
import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.repeatOnLifecycle
import io.github.liuzijiancs.capture2doc.Capture2DocApplication
import io.github.liuzijiancs.capture2doc.core.document.C2dParser
import io.github.liuzijiancs.capture2doc.core.document.C2dRenderer
import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import io.github.liuzijiancs.capture2doc.core.model.DocumentTask
import io.github.liuzijiancs.capture2doc.data.document.DocumentContent
import io.github.liuzijiancs.capture2doc.data.document.terminal
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun DocumentReaderScreen(task: DocumentTask, app: Capture2DocApplication, networkAvailable: Boolean,
    configurationRevision: Long, onBack: () -> Unit, onSettings: () -> Unit, onRetry: () -> Unit) {
    val context = LocalContext.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    var active by remember { mutableStateOf(lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) }
    var systemMotion by remember { mutableStateOf(ValueAnimator.areAnimatorsEnabled()) }
    val session = remember(task.taskId) { DocumentReaderSession(app.documentService, app.documentContents, app.taskRepository, task.taskId) }
    val content by app.documentContents.observe(task.taskId).collectAsStateWithLifecycle()
    val connection by session.connection.collectAsStateWithLifecycle()
    var retry by remember { mutableIntStateOf(0) }
    var menu by remember { mutableStateOf(false) }
    var copyMenu by remember { mutableStateOf(false) }
    var copying by remember { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val tokenAvailable = remember(task.baseUrl, configurationRevision) { runCatching { app.connectionSettings.token(task.baseUrl).isNotBlank() }.getOrDefault(false) }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, _ ->
            active = lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)
            systemMotion = ValueAnimator.areAnimatorsEnabled()
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer) }
    }
    DisposableEffect(context) {
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) { systemMotion = ValueAnimator.areAnimatorsEnabled() }
        }
        context.contentResolver.registerContentObserver(Settings.Global.getUriFor(Settings.Global.ANIMATOR_DURATION_SCALE), false, observer)
        onDispose { context.contentResolver.unregisterContentObserver(observer) }
    }
    LaunchedEffect(task.taskId, task.documentId) { task.documentId?.let { app.documentContents.load(task.taskId, it) } }
    LaunchedEffect(task.taskId, task.documentId, task.baseUrl, configurationRevision, networkAvailable, retry) {
        if (networkAvailable && tokenAvailable && task.documentId != null) lifecycle.repeatOnLifecycle(Lifecycle.State.RESUMED) { session.follow(task) }
    }
    val terminal = content.result != null || task.phase.terminal || content.preview?.status?.terminal == true
    val issue = when {
        content.result != null -> null
        !networkAvailable -> "网络未连接 · 已显示本地内容"
        !tokenAvailable -> "尚未配置连接 · 照片保存在本地"
        else -> connection.message ?: task.error ?: task.localError ?: content.issue ?: task.connectionIssue
    }
    val waiting = active && networkAvailable && tokenAvailable && !terminal && task.error == null && task.localError == null &&
        (task.documentId == null || connection.waiting) && task.connectionIssue == null
    val stage = when {
        content.result != null && content.document == null -> "解析完成，未识别到可用内容"
        content.result != null -> "解析完成 · ${content.result!!.wordCount} 字"
        issue != null -> issue
        terminal && (task.phase == DocumentPhase.FAILED || content.preview?.status == DocumentPhase.FAILED) -> "解析失败，已保留预览"
        terminal -> "正在校验最终结果"
        task.effectivePageIds.any { it !in task.uploadedPageIds } -> "上传中 · ${task.effectivePageIds.count { it in task.uploadedPageIds }} / ${task.effectivePageIds.size} 页"
        !task.submissionAccepted -> "正在确认最终页序"
        else -> when (content.preview?.status ?: task.phase) {
            DocumentPhase.OCR -> content.preview?.let { "识别中 · ${it.completedOcrImages} / ${it.totalImages} 页" } ?: "识别中"
            DocumentPhase.ASSEMBLING -> "整理文档中"
            DocumentPhase.VALIDATING -> "最终校验中"
            else -> "等待解析"
        }
    }
    val payload by produceState("{\"blocks\":[],\"ready\":false,\"waiting\":false,\"motion\":false}", content, waiting, active, systemMotion, task.plainText) {
        value = withContext(Dispatchers.Default) { readerPayload(content, task.plainText, waiting, active && systemMotion) }
    }
    BackHandler { onBack() }
    Scaffold(topBar = {
        CenterAlignedTopAppBar(title = { Text(content.result?.title ?: task.title, maxLines = 1, overflow = TextOverflow.Ellipsis) },
            navigationIcon = { TextButton(onClick = onBack, modifier = Modifier.testTag("reader_back")) { Text("返回") } },
            actions = {
                TextButton(onClick = { menu = true; copyMenu = false }, modifier = Modifier.testTag("reader_menu").semantics { contentDescription = "更多操作" }) { Text("⋯") }
                DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                    if (!copyMenu) DropdownMenuItem(text = { Text("复制 ›") }, onClick = { copyMenu = true }, enabled = content.document != null && !copying, modifier = Modifier.testTag("reader_copy"))
                    else CopyMode.entries.forEach { mode -> DropdownMenuItem(text = { Text(mode.label) }, enabled = content.document != null && !copying, onClick = {
                        menu = false
                        val document = content.document ?: return@DropdownMenuItem
                        copying = true
                        scope.launch {
                            try {
                                val clip = withContext(Dispatchers.Default) { DocumentClipboard.clip(document, mode) }
                                DocumentClipboard.write(context, clip)
                                snackbar.showSnackbar("已复制")
                            } catch (e: CancellationException) { throw e }
                            catch (e: Exception) { snackbar.showSnackbar(e.message ?: "剪贴板写入失败，未复制") }
                            finally { copying = false }
                        }
                    }) }
                }
            })
    }, snackbarHost = { SnackbarHost(snackbar) }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            Surface(color = MaterialTheme.colorScheme.surfaceContainerLow) {
                Column(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 8.dp)) {
                    Text(stage, style = MaterialTheme.typography.bodySmall, modifier = Modifier.testTag("reader_status"))
                    if (content.result == null && (issue != null || terminal)) Row {
                        TextButton(onClick = { retry++; onRetry() }) { Text("重新获取结果") }
                        if (!tokenAvailable || connection.authenticating || task.error?.contains("令牌") == true) TextButton(onClick = onSettings) { Text("连接设置") }
                    }
                }
            }
            DocumentWebView(payload, active, Modifier.weight(1f).fillMaxWidth().testTag("reader_document"))
        }
    }
}

internal fun readerPayload(content: DocumentContent, legacyText: String?, waiting: Boolean, motion: Boolean): String {
    val preview = content.preview?.blocks.orEmpty().map { block ->
        "block-${block.blockId}" to C2dRenderer.html(C2dParser.block(block.xml), true)
    }
    val blocks = when {
        content.document != null -> {
            // Final XML has no transport IDs. Keep matching preview nodes (including duplicates)
            // so validation completion does not replace or flash the entire document.
            val idsByHtml = preview.groupBy({ it.second }, { it.first }).mapValues { ArrayDeque(it.value) }
            content.document.blocks.mapIndexed { index, node ->
                val html = C2dRenderer.html(node, true)
                JSONObject().put("id", idsByHtml[html]?.removeFirstOrNull() ?: "final-$index").put("html", html)
            }
        }
        content.result != null -> emptyList()
        preview.isNotEmpty() -> preview.map { (id, html) -> JSONObject().put("id", id).put("html", html) }
        content.preview != null -> emptyList()
        !legacyText.isNullOrBlank() -> listOf(JSONObject().put("id", "legacy").put("html", "<div class=\"wide\"><pre>${C2dRenderer.escape(legacyText)}</pre></div>"))
        else -> emptyList()
    }
    return JSONObject().put("blocks", JSONArray(blocks)).put("ready", content.document != null)
        .put("waiting", waiting).put("motion", motion).toString()
}
