package io.github.liuzijiancs.capture2doc.ui.home

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import io.github.liuzijiancs.capture2doc.core.model.DocumentTask
import io.github.liuzijiancs.capture2doc.core.model.TaskDisplayStatus

@Composable
internal fun TaskDetailScreen(task: DocumentTask, onBack: () -> Unit, onRetry: () -> Unit) {
    BackHandler(onBack = onBack)
    Scaffold { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(20.dp).verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            TextButton(onClick = onBack) { Text("返回") }
            Text(task.title, style = MaterialTheme.typography.headlineSmall)
            TaskStatus(task.displayStatus)
            Text("${task.effectivePageIds.size} 张照片 · ${task.visibleWordCount?.toString() ?: "—"} 字",
                color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (task.hasCompletedDocument) {
                SelectionContainer { Text(task.plainText.orEmpty(), style = MaterialTheme.typography.bodyLarge) }
            } else {
                Text(task.localError ?: task.error ?: when (task.displayStatus) {
                    TaskDisplayStatus.NOT_CONNECTED -> "未连接服务器，照片和最终页序已保存在本地。服务配置并连接后继续后台上传。"
                    TaskDisplayStatus.UPLOADING -> "照片正在后台上传，可以返回首页。"
                    TaskDisplayStatus.FAILED -> "部分页面处理失败，请检查服务端任务后重试同步。"
                    else -> "等待页面识别及文档生成，当前内容不可编辑。"
                })
                TextButton(onClick = onRetry) { Text("重新同步") }
            }
        }
    }
}
