package io.github.liuzijiancs.capture2doc.ui.home

import android.app.Application
import android.net.ConnectivityManager
import android.net.Network
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import io.github.liuzijiancs.capture2doc.BuildConfig
import io.github.liuzijiancs.capture2doc.Capture2DocApplication
import io.github.liuzijiancs.capture2doc.data.task.TaskSyncWorker
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

internal enum class TaskDestination { HOME, CAMERA, DETAIL }
internal data class TaskHomeState(
    val busy: Boolean = true,
    val error: String? = null,
    val activeTaskId: String? = null,
    val destination: TaskDestination = TaskDestination.HOME,
)

internal class TaskHomeViewModel(application: Application, private val savedState: SavedStateHandle) : AndroidViewModel(application) {
    private val app = application as Capture2DocApplication
    val repository = app.taskRepository
    val tasks = repository.tasks
    private val connectivity = app.getSystemService(ConnectivityManager::class.java)
    // A network being available is not evidence that the document service responded.
    val networkAvailable = callbackFlow {
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) { trySend(true) }
            override fun onLost(network: Network) { trySend(false) }
        }
        trySend(connectivity.activeNetwork != null)
        connectivity.registerDefaultNetworkCallback(callback)
        awaitClose { connectivity.unregisterNetworkCallback(callback) }
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), connectivity.activeNetwork != null)
    private val _state = MutableStateFlow(TaskHomeState())
    val state = _state.asStateFlow()
    private var lastAction: (() -> Unit)? = null

    init {
        load()
        viewModelScope.launch {
            repository.errors.collect { message ->
                if (message != null) _state.update { it.copy(error = message) }
            }
        }
    }

    private fun load() {
        _state.update { it.copy(busy = false) }
        perform({ load() }) {
            repository.initialize()
            repository.tasks.value.forEach { task ->
                try {
                    repository.openRepository(task.taskId)
                    scheduleSync(task.taskId)
                } catch (cancelled: CancellationException) { throw cancelled }
                catch (error: Exception) { repository.update(task.taskId) { it.copy(error = "本地页面无法恢复：${error.message}", retryable = false) } }
            }
            val id: String? = savedState["activeTaskId"]
            val destination: String? = savedState["taskDestination"]
            val current = tasks.value.firstOrNull { it.taskId == id && it.isVisible }
            if (current != null) {
                val restored = TaskDestination.entries.firstOrNull { it.name == destination } ?: TaskDestination.HOME
                navigate(if (!current.isDraft && restored == TaskDestination.CAMERA) TaskDestination.DETAIL else restored, current.taskId)
            }
        }
    }

    fun createTask(): Unit = perform({ createTask() }) {
        repository.initialize()
        val pendingId: String? = savedState["pendingCreateTaskId"]
        val pending = repository.tasks.value.firstOrNull { it.taskId == pendingId }
            ?: repository.createLocalTask(BuildConfig.SERVICE_BASE_URL.trim().trimEnd('/'))
        savedState["pendingCreateTaskId"] = pending.taskId
        repository.openRepository(pending.taskId)
        savedState.remove<String>("pendingCreateTaskId")
        navigate(TaskDestination.CAMERA, pending.taskId)
        scheduleSync(pending.taskId)
    }

    fun openTask(id: String): Unit = perform({ openTask(id) }) {
        val task = repository.task(id)
        check(!task.hidden) { "任务已从本机列表移除" }
        repository.openRepository(id)
        navigate(if (task.isDraft) TaskDestination.CAMERA else TaskDestination.DETAIL, id)
        scheduleSync(id)
    }

    fun finishTask() {
        val id = _state.value.activeTaskId ?: return
        perform({ finishTask() }) { repository.submit(id); navigate(TaskDestination.HOME) }
    }

    fun hideTasks(ids: Set<String>): Unit = perform({ hideTasks(ids) }) {
        repository.hide(ids)
        if (_state.value.activeTaskId in ids) navigate(TaskDestination.HOME)
    }

    fun goHome() { if (!_state.value.busy) navigate(TaskDestination.HOME) }

    fun retryTask(id: String): Unit = perform({ retryTask(id) }) {
        repository.update(id) { it.copy(error = null, retryable = true) }
        scheduleSync(id, replace = true)
    }

    private suspend fun scheduleSync(id: String, replace: Boolean = false) {
        repository.bindUnconfiguredService(id, BuildConfig.SERVICE_BASE_URL.trim().trimEnd('/'))
        if (repository.task(id).baseUrl.isNotBlank()) TaskSyncWorker.enqueue(app, id, replace = replace)
    }

    fun retry() { lastAction?.invoke() }
    fun clearError() { _state.update { it.copy(error = null) } }

    private fun navigate(destination: TaskDestination, id: String? = null) {
        savedState["activeTaskId"] = id
        savedState["taskDestination"] = destination.name
        _state.update { it.copy(destination = destination, activeTaskId = id, error = null) }
    }

    private fun perform(retry: () -> Unit, action: suspend () -> Unit) {
        if (_state.value.busy) return
        lastAction = retry
        _state.update { it.copy(busy = true, error = null) }
        viewModelScope.launch {
            try { action() }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { _state.update { it.copy(error = error.message ?: "操作失败，请重试") } }
            finally { _state.update { it.copy(busy = false) } }
        }
    }
}
