package io.github.liuzijiancs.capture2doc.data.task

import android.util.AtomicFile
import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import io.github.liuzijiancs.capture2doc.core.model.DocumentTask
import io.github.liuzijiancs.capture2doc.core.model.PageRemotePhase
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import io.github.liuzijiancs.capture2doc.data.capture2doc.draft.ScanDraftRepository
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/** One atomic task catalog; each task owns a separate existing page repository. */
internal class TaskRepository(
    private val filesDirectory: File,
    private val scope: CoroutineScope,
    private val schedule: (String) -> Unit = {},
) {
    private val root = File(filesDirectory, "document_tasks")
    private val manifest = AtomicFile(File(root, "tasks.json"))
    private val mutex = Mutex()
    private val repositories = ConcurrentHashMap<String, ScanDraftRepository>()
    private val observed = ConcurrentHashMap.newKeySet<String>()
    private val _tasks = MutableStateFlow<List<DocumentTask>>(emptyList())
    val tasks = _tasks.asStateFlow()
    private val _errors = MutableStateFlow<String?>(null)
    val errors = _errors.asStateFlow()
    private var loaded = false

    suspend fun initialize() = withContext(Dispatchers.IO) {
        mutex.withLock {
            if (loaded) return@withLock
            val items = if (manifest.baseFile.exists() || File(manifest.baseFile.path + ".bak").exists()) {
                manifest.openRead().bufferedReader().use { decodeTasks(JSONObject(it.readText())) }
            } else emptyList()
            val legacyManifest = File(filesDirectory, "scan_draft/draft.json")
            val merged = if (legacyManifest.isFile && items.none { it.legacy }) {
                // Register in place. Never move/delete legacy image data during migration.
                items + DocumentTask(
                    taskId = "legacy", baseUrl = "", createdAt = legacyManifest.lastModified(), legacy = true,
                )
            } else items
            persist(merged)
            loaded = true
        }
    }

    suspend fun createLocalTask(baseUrl: String): DocumentTask {
        initialize()
        return withContext(Dispatchers.IO) {
            mutex.withLock {
                DocumentTask(taskId = UUID.randomUUID().toString(), baseUrl = baseUrl,
                        createdAt = System.currentTimeMillis()).also { persist(_tasks.value + it) }
            }
        }
    }

    fun task(id: String): DocumentTask = _tasks.value.first { it.taskId == id }

    /** A configured endpoint never follows a later build's endpoint change. */
    suspend fun bindUnconfiguredService(id: String, baseUrl: String) {
        if (baseUrl.isNotBlank()) update(id) {
            if (it.baseUrl.isBlank()) it.copy(baseUrl = baseUrl) else it
        }
    }

    suspend fun update(id: String, transform: (DocumentTask) -> DocumentTask) = withContext(Dispatchers.IO) {
        mutex.withLock {
            val current = task(id)
            val changed = transform(current)
            require(changed.taskId == current.taskId && changed.legacy == current.legacy)
            if (changed != current) persist(_tasks.value.map { if (it.taskId == id) changed else it })
        }
    }

    suspend fun hide(ids: Set<String>) = withContext(Dispatchers.IO) {
        mutex.withLock { persist(_tasks.value.map { if (it.taskId in ids) it.copy(hidden = true) else it }) }
        // Deliberately do not cancel workers, drop manifests, or remove images.
    }

    fun draftRepository(id: String): ScanDraftRepository {
        val task = task(id)
        return repositories.getOrPut(id) {
            ScanDraftRepository(
                if (task.legacy) filesDirectory else File(root, id),
                keepEmptyDraft = true, preserveUnreadable = true,
            )
        }
    }

    suspend fun openRepository(id: String): ScanDraftRepository {
        initialize()
        val repository = draftRepository(id)
        repository.initialize()
        repository.ensureDraft()
        refreshPages(id)
        if (observed.add(id)) {
            scope.launch {
                repository.draft.map { draft ->
                    draft?.pages.orEmpty()
                }.distinctUntilChanged().collect { pages ->
                    try {
                        refreshPages(id)
                        val current = task(id)
                        if (current.baseUrl.isNotBlank() && pages.any {
                            it.state != ScanPageState.FAILED && it.pageId !in current.uploadedPageIds
                        }) schedule(id)
                    } catch (cancelled: CancellationException) {
                        throw cancelled
                    } catch (error: Exception) {
                        _errors.value = "任务状态保存或调度失败：${error.message}"
                    }
                }
            }
        }
        return repository
    }

    suspend fun refreshPages(id: String) {
        update(id) { current ->
            val draft = draftRepository(id).draft.value
            if (draft == null) current else current.copy(
                pageIds = draft.pages.map { it.pageId },
                localError = draft.pages.firstOrNull { it.state == ScanPageState.FAILED }?.errorMessage,
            )
        }
    }

    suspend fun submit(id: String) {
        val repository = openRepository(id)
        val pages = requireNotNull(repository.draft.value).pages
        require(pages.isNotEmpty()) { "请先拍摄页面" }
        require(pages.none { it.state == ScanPageState.CAPTURING || it.state == ScanPageState.RETAKE_REQUIRED }) {
            "请等待原图保存完成"
        }
        require(pages.all { repository.hasValidOriginal(it.pageId) }) { "存在无效原图，请删除后再完成" }
        update(id) { current ->
            if (!current.isDraft) current else current.copy(submittedPageIds = pages.map { it.pageId })
        }
        if (task(id).baseUrl.isNotBlank()) schedule(id)
    }

    private fun persist(items: List<DocumentTask>) {
        check(root.exists() || root.mkdirs()) { "无法创建任务目录" }
        val stream = manifest.startWrite()
        try {
            stream.write(encodeTasks(items).toString().toByteArray(Charsets.UTF_8))
            manifest.finishWrite(stream)
        } catch (error: Throwable) {
            manifest.failWrite(stream)
            throw error
        }
        _tasks.value = items.toList()
        _errors.value = null
    }
}

internal fun encodeTasks(tasks: List<DocumentTask>): JSONObject = JSONObject().apply {
    put("schemaVersion", 1)
    put("tasks", JSONArray().apply {
        tasks.forEach { task -> put(JSONObject().apply {
            put("taskId", task.taskId)
            put("documentId", task.documentId ?: JSONObject.NULL)
            put("baseUrl", task.baseUrl)
            put("createdAt", task.createdAt)
            put("title", task.title)
            put("pageIds", JSONArray(task.pageIds))
            put("submittedPageIds", task.submittedPageIds?.let { JSONArray(it) } ?: JSONObject.NULL)
            put("uploadedPageIds", JSONArray(task.uploadedPageIds.toList()))
            put("submissionAccepted", task.submissionAccepted)
            put("phase", task.phase.name)
            put("pagePhases", JSONObject().apply { task.pagePhases.forEach { (id, phase) -> put(id, phase.name) } })
            put("wordCount", task.wordCount ?: JSONObject.NULL)
            put("plainText", task.plainText ?: JSONObject.NULL)
            put("error", task.error ?: JSONObject.NULL)
            put("localError", task.localError ?: JSONObject.NULL)
            put("connectionIssue", task.connectionIssue ?: JSONObject.NULL)
            put("retryable", task.retryable)
            put("hidden", task.hidden)
            put("legacy", task.legacy)
        }) }
    })
}

internal fun decodeTasks(json: JSONObject): List<DocumentTask> {
    require(json.getInt("schemaVersion") == 1) { "不支持的任务清单版本" }
    val array = json.getJSONArray("tasks")
    val tasks = buildList {
        repeat(array.length()) { index ->
            val item = array.getJSONObject(index)
            val id = item.getString("taskId")
            require(id.matches(Regex("[A-Za-z0-9_-]+"))) { "任务 ID 无效" }
            fun string(key: String) = if (item.isNull(key)) null else item.getString(key)
            val phases = item.getJSONObject("pagePhases")
            add(DocumentTask(
                taskId = id, documentId = string("documentId"), baseUrl = item.getString("baseUrl"),
                createdAt = item.getLong("createdAt"), title = item.getString("title"),
                pageIds = item.getJSONArray("pageIds").strings(),
                submittedPageIds = if (item.isNull("submittedPageIds")) null else item.getJSONArray("submittedPageIds").strings(),
                uploadedPageIds = item.getJSONArray("uploadedPageIds").strings().toSet(),
                submissionAccepted = item.getBoolean("submissionAccepted"),
                phase = DocumentPhase.valueOf(item.getString("phase")),
                pagePhases = phases.keys().asSequence().associateWith { PageRemotePhase.valueOf(phases.getString(it)) },
                wordCount = if (item.isNull("wordCount")) null else item.getLong("wordCount"),
                plainText = string("plainText"), error = string("error"), localError = string("localError"),
                connectionIssue = string("connectionIssue"),
                retryable = item.getBoolean("retryable"), hidden = item.getBoolean("hidden"), legacy = item.getBoolean("legacy"),
            ))
        }
    }
    require(tasks.map { it.taskId }.distinct().size == tasks.size) { "重复任务 ID" }
    require(tasks.count { it.legacy } <= 1) { "重复旧草稿" }
    return tasks
}

private fun JSONArray.strings(): List<String> = List(length()) { getString(it) }.also {
    require(it.distinct().size == it.size && it.all(String::isNotBlank)) { "无效或重复页面 ID" }
}
