package io.github.liuzijiancs.capture2doc.data.document

import android.util.AtomicFile
import io.github.liuzijiancs.capture2doc.core.document.C2dDocument
import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import io.github.liuzijiancs.capture2doc.data.task.TaskRepository
import java.io.File
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONObject

internal data class DocumentContent(
    val preview: PreviewState? = null,
    val result: FinalDocumentResult? = null,
    val document: C2dDocument? = null,
    val issue: String? = null,
)

/** Preview, revision and cursor are one atomic transaction; the verified result is independent. */
internal class DocumentContentRepository(private val filesDir: File, private val tasks: TaskRepository) {
    private class Entry {
        val mutex = Mutex()
        val state = MutableStateFlow(DocumentContent())
        var loaded = false
        var generation = 0L
    }
    private val entries = ConcurrentHashMap<String, Entry>()
    private fun entry(id: String) = entries.getOrPut(id) { Entry() }
    fun observe(id: String): StateFlow<DocumentContent> = entry(id).state
    private fun file(id: String, name: String): AtomicFile {
        require(id.matches(Regex("[A-Za-z0-9_-]+")))
        return AtomicFile(File(filesDir, "document_tasks/$id/$name"))
    }
    suspend fun load(id: String, documentId: String) = withContext(Dispatchers.IO) {
        val entry = entry(id)
        entry.mutex.withLock {
            if (entry.loaded) return@withLock
            var issue: String? = null
            val preview = runCatching { read(file(id, "preview.json"))?.let { PreviewReducer.restore(it, documentId) } }
                .getOrElse { issue = "预览缓存不可用，将重新获取"; null }
            val result = runCatching { read(file(id, "result.json"))?.let { FinalDocumentResult.parse(it, documentId).also { r -> r.validatedDocument() } } }
                .getOrElse { issue = "最终结果缓存校验失败，请重新获取"; null }
            entry.state.value = DocumentContent(preview, result, result?.validatedDocument(), issue)
            entry.loaded = true
            if (result != null) reconcile(id, result)
        }
    }
    suspend fun begin(id: String): Long = entry(id).let { e -> e.mutex.withLock { ++e.generation } }
    suspend fun end(id: String, generation: Long) = entry(id).let { e -> e.mutex.withLock { if (e.generation == generation) ++e.generation } }
    suspend fun accept(id: String, generation: Long, event: DocumentEvent) = withContext(Dispatchers.IO) {
        val e = entry(id)
        e.mutex.withLock {
            if (generation != e.generation || e.state.value.result != null) return@withLock
            val before = e.state.value.preview ?: PreviewState(event.data.getString("documentId"))
            val next = PreviewReducer.apply(before, event)
            if (next != before) {
                write(file(id, "preview.json"), next.json())
                e.state.value = e.state.value.copy(preview = next, issue = null)
            }
        }
    }
    suspend fun resetCursor(id: String, generation: Long) = withContext(Dispatchers.IO) {
        val e = entry(id)
        e.mutex.withLock {
            if (e.generation != generation) return@withLock
            e.state.value.preview?.copy(cursor = null)?.let {
                write(file(id, "preview.json"), it.json())
                e.state.value = e.state.value.copy(preview = it)
            }
        }
    }
    suspend fun storeResult(id: String, result: FinalDocumentResult) = withContext(Dispatchers.IO) {
        val e = entry(id)
        e.mutex.withLock {
            val document = result.validatedDocument()
            require(tasks.task(id).documentId == result.documentId) { "最终结果身份不匹配" }
            write(file(id, "result.json"), result.json())
            e.state.value = e.state.value.copy(result = result, document = document, issue = null)
            reconcile(id, result)
        }
    }
    private suspend fun reconcile(id: String, result: FinalDocumentResult) {
        tasks.update(id) { it.copy(phase = DocumentPhase.COMPLETED, title = result.title!!,
            wordCount = result.wordCount, error = null, connectionIssue = null, retryable = true) }
    }
    private fun read(file: AtomicFile): JSONObject? = try {
        file.openRead().bufferedReader(Charsets.UTF_8).use { JSONObject(it.readText()) }
    } catch (_: java.io.FileNotFoundException) { null }
    private fun write(file: AtomicFile, json: JSONObject) {
        file.baseFile.parentFile!!.mkdirs()
        val output = file.startWrite()
        try { output.write(json.toString().toByteArray(Charsets.UTF_8)); file.finishWrite(output) }
        catch (error: Exception) { file.failWrite(output); throw error }
    }
}
