package io.github.liuzijiancs.capture2doc.ui.document

import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import io.github.liuzijiancs.capture2doc.core.model.DocumentTask
import io.github.liuzijiancs.capture2doc.data.document.PreviewResetRequired
import io.github.liuzijiancs.capture2doc.data.document.DocumentContentRepository
import io.github.liuzijiancs.capture2doc.data.document.terminal
import io.github.liuzijiancs.capture2doc.data.task.DocumentService
import io.github.liuzijiancs.capture2doc.data.task.TaskRepository
import io.github.liuzijiancs.capture2doc.data.task.ServiceException
import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.takeWhile
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext

internal data class ReaderConnection(val message: String? = null, val waiting: Boolean = false, val authenticating: Boolean = false)

/** Owned by the visible reader. WorkManager is intentionally independent of this lifetime. */
internal class DocumentReaderSession(private val service: DocumentService, private val contents: DocumentContentRepository,
    private val tasks: TaskRepository, private val taskId: String) {
    val connection = MutableStateFlow(ReaderConnection())
    private var activeRun = 0L
    suspend fun follow(task: DocumentTask) {
        val documentId = task.documentId ?: return
        val run = ++activeRun
        fun publish(value: ReaderConnection) { if (run == activeRun) connection.value = value }
        contents.load(taskId, documentId)
        val generation = contents.begin(taskId)
        var delayMs = 1_000L
        try {
            while (currentCoroutineContext().isActive) {
                if (contents.observe(taskId).value.result != null) { publish(ReaderConnection()); return }
                try {
                    if (contents.observe(taskId).value.preview?.status?.terminal == true || task.phase.terminal) {
                        publish(ReaderConnection("正在校验最终结果", waiting = true))
                        if (refresh(task)) { publish(ReaderConnection()); return }
                    }
                    publish(ReaderConnection(waiting = true))
                    service.events(task.baseUrl, documentId, contents.observe(taskId).value.preview?.cursor)
                        .takeWhile { event ->
                            contents.accept(taskId, generation, event)
                            publish(ReaderConnection(waiting = true))
                            delayMs = 1_000
                            contents.observe(taskId).value.preview?.status?.terminal != true
                        }.collect { }
                    // Both terminal events/snapshots and HTTP 204 require the authoritative GET.
                    publish(ReaderConnection("正在校验最终结果", waiting = true))
                    if (refresh(task)) { publish(ReaderConnection()); return }
                    publish(ReaderConnection("连接已断开，正在重连"))
                } catch (error: CancellationException) { throw error }
                catch (error: Exception) {
                    when {
                        error is PreviewResetRequired || (error is ServiceException && error.statusCode == 409) -> {
                            contents.resetCursor(taskId, generation)
                            publish(ReaderConnection("正在重新获取完整预览"))
                        }
                        error is ServiceException && !error.retryable -> {
                            publish(ReaderConnection(error.message, authenticating = error.statusCode == 401))
                            return
                        }
                        error is IOException -> publish(ReaderConnection("连接中断，稍后重连"))
                        else -> { publish(ReaderConnection(error.message ?: "结果校验失败，请重新获取")); return }
                    }
                }
                delay(delayMs)
                delayMs = (delayMs * 2).coerceAtMost(30_000)
            }
        } finally {
            withContext(NonCancellable) { contents.end(taskId, generation) }
            if (run == activeRun) connection.value = connection.value.copy(waiting = false)
        }
    }
    private suspend fun refresh(task: DocumentTask): Boolean {
        val result = service.status(task.baseUrl, requireNotNull(task.documentId))
        when (result.status) {
            DocumentPhase.COMPLETED -> contents.storeResult(taskId, result)
            DocumentPhase.FAILED -> {
                tasks.update(taskId) { if (contents.observe(taskId).value.result != null) it else it.copy(phase = DocumentPhase.FAILED, error = result.message ?: "服务端处理失败", retryable = false) }
            }
            else -> return false
        }
        return true
    }
}
