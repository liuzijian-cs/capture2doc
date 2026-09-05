package io.github.liuzijiancs.capture2doc

import android.app.Application
import io.github.liuzijiancs.capture2doc.data.capture2doc.draft.ScanDraftRepository
import io.github.liuzijiancs.capture2doc.data.task.HttpDocumentService
import io.github.liuzijiancs.capture2doc.data.task.TaskRepository
import io.github.liuzijiancs.capture2doc.data.task.TaskSynchronizer
import io.github.liuzijiancs.capture2doc.data.task.TaskSyncWorker
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

class Capture2DocApplication : Application() {
    private val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    internal val connectionSettings by lazy { io.github.liuzijiancs.capture2doc.data.settings.ConnectionSettings(filesDir) }
    internal val documentService by lazy { HttpDocumentService(connectionSettings::token) }
    internal val taskRepository by lazy {
        TaskRepository(filesDir, applicationScope) { TaskSyncWorker.enqueue(this, it) }
    }
    internal val documentContents by lazy { io.github.liuzijiancs.capture2doc.data.document.DocumentContentRepository(filesDir, taskRepository) }
    internal val taskSynchronizer by lazy { TaskSynchronizer(taskRepository, documentService, documentContents) }
    internal val scanDraftRepository: ScanDraftRepository by lazy {
        ScanDraftRepository(filesDir)
    }
}
