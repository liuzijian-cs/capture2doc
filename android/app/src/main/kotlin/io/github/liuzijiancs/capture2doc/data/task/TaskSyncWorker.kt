package io.github.liuzijiancs.capture2doc.data.task

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkInfo
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import io.github.liuzijiancs.capture2doc.Capture2DocApplication
import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import io.github.liuzijiancs.capture2doc.core.model.PageRemotePhase
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import java.io.IOException
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

internal enum class SyncOutcome { DONE, WAITING, RETRY, BLOCKED }

internal class TaskSynchronizer(
    private val repository: TaskRepository,
    private val service: DocumentService,
) {
    private val locks = ConcurrentHashMap<String, Mutex>()

    suspend fun sync(id: String): SyncOutcome = locks.getOrPut(id) { Mutex() }.withLock {
        repository.initialize()
        var task = repository.task(id)
        if (task.baseUrl.isBlank()) return@withLock SyncOutcome.DONE
        if (task.error != null && !task.retryable) return@withLock SyncOutcome.BLOCKED
        try {
            val draft = repository.openRepository(id)
            val documentId = task.documentId ?: service.create(task.baseUrl, task.taskId).also { remoteId ->
                // Persist the real server identity before uploading; retries reuse taskId.
                require(remoteId.isNotBlank()) { "服务端未返回文档 ID" }
                repository.update(id) { it.copy(documentId = remoteId, connectionIssue = null) }
            }
            // In-process normalization belongs to the camera. Cold initialization recovers files.
            val pages = draft.draft.value?.pages.orEmpty()
            for (page in pages) {
                task = repository.task(id)
                if (page.pageId !in task.effectivePageIds || page.pageId in task.uploadedPageIds) continue
                if (page.state != ScanPageState.READY) continue
                try {
                    service.upload(task.baseUrl, documentId, page.pageId, draft.resolve(page.normalizedRelativePath))
                    repository.update(id) { it.copy(uploadedPageIds = it.uploadedPageIds + page.pageId) }
                } catch (error: Exception) {
                    if (error is CancellationException) throw error
                    // Deleting a candidate during upload must not fail the remaining document.
                    if (draft.draft.value?.pages?.any { it.pageId == page.pageId } == true) throw error
                }
            }
            task = repository.task(id)
            val snapshot = task.submittedPageIds
            if (snapshot != null && !task.submissionAccepted && snapshot.all { it in task.uploadedPageIds }) {
                service.finalize(task.baseUrl, documentId, task.taskId, snapshot)
                repository.update(id) { it.copy(submissionAccepted = true) }
            }
            val status = service.status(task.baseUrl, documentId)
            repository.update(id) { current -> current.copy(
                phase = status.phase,
                title = status.title?.takeIf(String::isNotBlank) ?: current.title,
                wordCount = status.wordCount,
                plainText = status.plainText,
                pagePhases = status.pages,
                error = status.message.takeIf { status.phase == DocumentPhase.FAILED }
                    ?: if (status.phase == DocumentPhase.FAILED) "服务端处理失败" else null,
                retryable = status.phase != DocumentPhase.FAILED,
                connectionIssue = null,
            ) }
            task = repository.task(id)
            when {
                task.phase == DocumentPhase.FAILED || task.localError != null ||
                    task.effectivePageIds.any { task.pagePhases[it] == PageRemotePhase.FAILED } -> SyncOutcome.BLOCKED
                task.effectivePageIds.any { it !in task.uploadedPageIds } -> SyncOutcome.WAITING
                !task.isDraft && (!task.submissionAccepted || task.phase != DocumentPhase.COMPLETED) -> SyncOutcome.WAITING
                task.isDraft && (task.phase == DocumentPhase.OCR ||
                    task.effectivePageIds.any { task.pagePhases[it] in setOf(PageRemotePhase.QUEUED, PageRemotePhase.PROCESSING) }) -> SyncOutcome.WAITING
                else -> SyncOutcome.DONE
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: Exception) {
            val retryable = if (error is ServiceException) error.retryable else error is IOException
            repository.update(id) {
                if (retryable) it.copy(connectionIssue = error.message ?: "未连接服务器", retryable = true)
                else it.copy(error = error.message ?: "同步失败", retryable = false)
            }
            if (retryable) SyncOutcome.RETRY else SyncOutcome.BLOCKED
        }
    }
}

class TaskSyncWorker(context: Context, parameters: WorkerParameters) : CoroutineWorker(context, parameters) {
    override suspend fun doWork(): Result {
        val id = inputData.getString(TASK_ID) ?: return Result.failure()
        val app = applicationContext as Capture2DocApplication
        return try {
            when (app.taskSynchronizer.sync(id)) {
                SyncOutcome.DONE -> Result.success()
                SyncOutcome.WAITING -> {
                    // Existing page-change successors will poll too. Only the last worker adds
                    // a delayed successor, so polling never multiplies with the number of photos.
                    val hasSuccessor = withContext(Dispatchers.IO) {
                        WorkManager.getInstance(applicationContext)
                            .getWorkInfosForUniqueWork("document-sync-$id").get()
                            .any { it.id != this@TaskSyncWorker.id && it.state in setOf(
                                WorkInfo.State.ENQUEUED, WorkInfo.State.BLOCKED, WorkInfo.State.RUNNING,
                            ) }
                    }
                    if (!hasSuccessor) enqueue(applicationContext, id, delaySeconds = 5)
                    Result.success()
                }
                SyncOutcome.RETRY -> Result.retry()
                SyncOutcome.BLOCKED -> Result.failure()
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            // Corrupt catalog remains on disk for diagnosis; never replace it with empty data.
            Result.failure()
        }
    }

    companion object {
        private const val TASK_ID = "taskId"
        fun enqueue(context: Context, taskId: String, delaySeconds: Long = 0, replace: Boolean = false) {
            val request = OneTimeWorkRequestBuilder<TaskSyncWorker>()
                .setInputData(workDataOf(TASK_ID to taskId))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
                .setInitialDelay(delaySeconds, TimeUnit.SECONDS)
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(
                "document-sync-$taskId",
                if (replace) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.APPEND_OR_REPLACE,
                request,
            )
        }
    }
}
