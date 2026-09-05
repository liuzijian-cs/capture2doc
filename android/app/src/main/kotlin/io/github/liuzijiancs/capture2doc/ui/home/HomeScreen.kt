package io.github.liuzijiancs.capture2doc.ui.home

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.InlineTextContent
import androidx.compose.foundation.text.appendInlineContent
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.semantics.CustomAccessibilityAction
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.customActions
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.Placeholder
import androidx.compose.ui.text.PlaceholderVerticalAlign
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import io.github.liuzijiancs.capture2doc.core.model.*
import io.github.liuzijiancs.capture2doc.ui.theme.Capture2DocTheme
import io.github.liuzijiancs.capture2doc.ui.theme.statusColors
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlin.math.roundToInt

object HomeScreenTags {
    const val START_SCAN_BUTTON = "home_start_scan"
    const val SEARCH = "home_search"
    const val DELETE_CONFIRM = "home_delete_confirm"
    fun task(id: String) = "home_task_$id"
}

@Composable
internal fun HomeScreen(
    tasks: List<DocumentTask>,
    onStartScan: () -> Unit,
    onOpenTask: (String) -> Unit,
    onHideTasks: (Set<String>) -> Unit,
    modifier: Modifier = Modifier,
    busy: Boolean = false,
    error: String? = null,
    onRetry: () -> Unit = onStartScan,
    disconnected: Boolean = false,
    onConnectionSettings: () -> Unit = {},
) {
    var query by rememberSaveable { mutableStateOf("") }
    var selectedIds by rememberSaveable { mutableStateOf<List<String>>(emptyList()) }
    var pendingDelete by rememberSaveable { mutableStateOf<List<String>>(emptyList()) }
    val filtered = visibleTasks(tasks, query)
    val selection = selectedIds.toSet()
    BackHandler(selection.isNotEmpty()) { selectedIds = emptyList() }
    if (pendingDelete.isNotEmpty()) AlertDialog(
        onDismissRequest = { pendingDelete = emptyList() },
        title = { Text("移除 ${pendingDelete.size} 个任务？") },
        text = { Text("仅从本机列表移除，后台上传和处理仍会继续。") },
        confirmButton = { TextButton(enabled = !busy, onClick = {
            onHideTasks(pendingDelete.toSet()); pendingDelete = emptyList(); selectedIds = emptyList()
        }, modifier = Modifier.testTag(HomeScreenTags.DELETE_CONFIRM)) { Text("移除") } },
        dismissButton = { TextButton(onClick = { pendingDelete = emptyList() }) { Text("取消") } },
    )
    Scaffold(modifier = modifier, floatingActionButton = {
        if (selection.isEmpty()) FloatingActionButton(
            onClick = { if (!busy) onStartScan() }, containerColor = MaterialTheme.colorScheme.primary,
            contentColor = Color.White, shape = CircleShape,
            modifier = Modifier.testTag(HomeScreenTags.START_SCAN_BUTTON).semantics { contentDescription = "新建文档" },
        ) {
            if (busy) CircularProgressIndicator(Modifier.size(24.dp).testTag("home_busy"), color = Color.White, strokeWidth = 2.dp)
            else Text("+", fontSize = 32.sp)
        }
    }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            TextButton(onClick = onConnectionSettings, enabled = !busy, modifier = Modifier.align(Alignment.End).testTag("home_connection_settings")) { Text("连接设置") }
            if (selection.isEmpty()) OutlinedTextField(
                value = query, onValueChange = { query = it }, singleLine = true,
                placeholder = { Text("搜索文档标题") }, shape = RoundedCornerShape(28.dp),
                trailingIcon = { if (query.isNotEmpty()) TextButton(onClick = { query = "" }) { Text("清除") } },
                modifier = Modifier.fillMaxWidth().padding(16.dp).testTag(HomeScreenTags.SEARCH),
            ) else Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = { selectedIds = emptyList() }) { Text("取消") }
                Text("已选 ${selection.size} 项", Modifier.weight(1f))
                TextButton(onClick = { selectedIds = filtered.map { it.taskId } }) { Text("全选") }
                TextButton(onClick = { pendingDelete = selection.toList() }) { Text("删除") }
            }
            if (disconnected) Text("未连接服务器 · 可先拍摄，照片保存在本地",
                modifier = Modifier.padding(horizontal = 20.dp, vertical = 8.dp).testTag("home_connection_warning"),
                color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodySmall)
            if (error != null) Surface(color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.padding(horizontal = 16.dp)) {
                Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(error, Modifier.weight(1f), color = MaterialTheme.colorScheme.onErrorContainer, style = MaterialTheme.typography.bodySmall)
                    TextButton(onClick = onRetry, enabled = !busy) { Text("重试") }
                }
            }
            if (filtered.isEmpty()) Box(Modifier.fillMaxSize().padding(32.dp), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(if (query.isBlank()) "还没有文档" else "没有匹配的文档", style = MaterialTheme.typography.titleMedium)
                    Text(if (query.isBlank()) "点击右下角 ＋ 开始拍摄" else "试试其他标题关键词",
                        color = MaterialTheme.colorScheme.onSurfaceVariant, style = MaterialTheme.typography.bodyMedium)
                }
            } else LazyColumn(contentPadding = PaddingValues(bottom = 100.dp)) {
                items(filtered, key = { it.taskId }) { task ->
                    TaskRow(task, task.taskId in selection, selection.isNotEmpty(), !busy,
                        onClick = {
                            if (selection.isEmpty()) onOpenTask(task.taskId)
                            else selectedIds = (if (task.taskId in selection) selection - task.taskId else selection + task.taskId).toList()
                        }, onLongClick = { selectedIds = (selection + task.taskId).toList() },
                        onDelete = { pendingDelete = listOf(task.taskId) })
                }
            }
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun TaskRow(task: DocumentTask, selected: Boolean, selectionMode: Boolean, enabled: Boolean,
    onClick: () -> Unit, onLongClick: () -> Unit, onDelete: () -> Unit,
) {
    var reveal by remember(task.taskId) { mutableFloatStateOf(0f) }
    val revealWidth = with(LocalDensity.current) { 88.dp.toPx() }
    LaunchedEffect(selectionMode) { reveal = 0f }
    Box(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
        if (reveal < 0) TextButton(onClick = onDelete, enabled = enabled,
            modifier = Modifier.align(Alignment.CenterEnd).width(88.dp).fillMaxHeight().background(MaterialTheme.colorScheme.errorContainer)) {
            Text("删除", color = MaterialTheme.colorScheme.onErrorContainer)
        }
        Column(Modifier.fillMaxWidth().offset { IntOffset(reveal.roundToInt(), 0) }
            .background(if (selected) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface)
            .testTag(HomeScreenTags.task(task.taskId)).semantics {
                this.selected = selected
                customActions = if (enabled) listOf(CustomAccessibilityAction("删除任务") { onDelete(); true }) else emptyList()
            }.pointerInput(selectionMode, enabled) {
                if (!selectionMode && enabled) detectHorizontalDragGestures(
                    onDragEnd = { reveal = if (reveal < -revealWidth / 2) -revealWidth else 0f },
                    onDragCancel = { reveal = 0f },
                ) { change, amount -> change.consume(); reveal = (reveal + amount).coerceIn(-revealWidth, 0f) }
            }.combinedClickable(enabled = enabled, onClick = { if (reveal < 0) reveal = 0f else onClick() }, onLongClick = onLongClick)
            .padding(horizontal = 20.dp, vertical = 18.dp), verticalArrangement = Arrangement.spacedBy(9.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (selectionMode) Text(if (selected) "☑" else "□", color = MaterialTheme.colorScheme.primary)
                Text(task.title, Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis,
                    fontWeight = FontWeight.Medium, style = MaterialTheme.typography.titleMedium)
                if (task.isDraft) Surface(color = MaterialTheme.colorScheme.surfaceContainerHigh, shape = RoundedCornerShape(4.dp)) {
                    Text("草稿", Modifier.padding(horizontal = 5.dp, vertical = 2.dp), style = MaterialTheme.typography.labelSmall)
                }
                TaskStatus(task.displayStatus)
            }
            TaskMetadata(task)
        }
    }
    HorizontalDivider(Modifier.padding(horizontal = 20.dp), color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = .5f))
}

@Composable
internal fun TaskStatus(status: TaskDisplayStatus) {
    if (status == TaskDisplayStatus.IDLE) return
    val color = when (status) {
        TaskDisplayStatus.UPLOADING -> MaterialTheme.colorScheme.primaryContainer
        TaskDisplayStatus.PROCESSING, TaskDisplayStatus.NOT_CONNECTED -> MaterialTheme.statusColors.warningContainer
        TaskDisplayStatus.COMPLETED -> MaterialTheme.statusColors.successContainer
        TaskDisplayStatus.FAILED -> MaterialTheme.colorScheme.errorContainer
        else -> MaterialTheme.colorScheme.surface
    }
    Surface(color = color, shape = RoundedCornerShape(8.dp)) {
        Text(status.label, Modifier.padding(horizontal = 7.dp, vertical = 4.dp), color = Color(0xFF111318),
            style = MaterialTheme.typography.labelSmall, maxLines = 1)
    }
}

@Composable
private fun TaskMetadata(task: DocumentTask) {
    val time = remember(task.createdAt) { DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm", Locale.ROOT)
        .withZone(ZoneId.systemDefault()).format(Instant.ofEpochMilli(task.createdAt)) }
    val count = task.effectivePageIds.size
    val words = task.visibleWordCount?.toString() ?: "—"
    val tint = MaterialTheme.colorScheme.onSurfaceVariant
    val label = buildAnnotatedString {
        append("$time | "); appendInlineContent("photo", "照片"); append(" $count | ")
        appendInlineContent("words", "字数"); append(" $words")
    }
    val inline = mapOf(
        "photo" to InlineTextContent(Placeholder(14.sp, 13.sp, PlaceholderVerticalAlign.TextCenter)) {
            Canvas(Modifier.fillMaxSize()) {
                drawRoundRect(tint, cornerRadius = CornerRadius(2f), style = Stroke(1.dp.toPx()))
                drawLine(tint, Offset(size.width * .12f, size.height * .78f), Offset(size.width * .44f, size.height * .42f), 1.dp.toPx())
                drawLine(tint, Offset(size.width * .44f, size.height * .42f), Offset(size.width * .84f, size.height * .78f), 1.dp.toPx())
            }
        },
        "words" to InlineTextContent(Placeholder(14.sp, 13.sp, PlaceholderVerticalAlign.TextCenter)) {
            Text("字", color = tint, fontSize = 11.sp, maxLines = 1)
        },
    )
    Text(label, inlineContent = inline, color = tint, fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis,
        modifier = Modifier.fillMaxWidth().semantics { contentDescription = "$time，照片 $count 张，字数 $words" })
}

internal fun previewDocumentTasks(): List<DocumentTask> = listOf(
    DocumentTask("one", "doc-1", "", 1788581040000, title = "多模态模型训练笔记",
        pageIds = listOf("A", "B"), submittedPageIds = listOf("A", "B")),
    DocumentTask("two", "doc-2", "", 1788577440000, pageIds = listOf("C"), uploadedPageIds = setOf("C"), phase = DocumentPhase.OCR),
    DocumentTask("three", "doc-3", "", 1788573840000, title = "本周学习记录与项目讨论",
        pageIds = listOf("D"), submittedPageIds = listOf("D"), uploadedPageIds = setOf("D"),
        submissionAccepted = true, phase = DocumentPhase.COMPLETED, wordCount = 12680, plainText = "这是一份仅用于预览的文档。"),
    DocumentTask("four", "doc-4", "", 1788570240000, error = "网络连接失败"),
)

@Preview(name = "Tasks", showBackground = true, widthDp = 390, heightDp = 844)
@Preview(name = "Tasks large font", showBackground = true, widthDp = 360, fontScale = 1.5f)
@Composable
private fun TaskHomePreview() { Capture2DocTheme { HomeScreen(previewDocumentTasks(), {}, {}, {}) } }

@Preview(name = "Tasks offline", showBackground = true, widthDp = 390, heightDp = 844)
@Composable
private fun TaskHomeOfflinePreview() {
    Capture2DocTheme {
        HomeScreen(listOf(DocumentTask("offline", baseUrl = "", createdAt = 1788581040000,
            pageIds = listOf("A"))), {}, {}, {}, disconnected = true)
    }
}

@Preview(name = "Tasks empty", showBackground = true, widthDp = 390, heightDp = 844)
@Composable
private fun EmptyTaskHomePreview() { Capture2DocTheme { HomeScreen(emptyList(), {}, {}, {}) } }

@Preview(name = "Create failed", showBackground = true, widthDp = 390)
@Composable
private fun FailedTaskHomePreview() {
    Capture2DocTheme { HomeScreen(emptyList(), {}, {}, {}, error = "无法连接文档服务，请检查后重试。") }
}
