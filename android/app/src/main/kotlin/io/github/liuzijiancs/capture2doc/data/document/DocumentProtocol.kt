package io.github.liuzijiancs.capture2doc.data.document

import io.github.liuzijiancs.capture2doc.core.document.C2dDocument
import io.github.liuzijiancs.capture2doc.core.document.C2dParser
import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest

internal fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
internal fun JSONObject.stringOrNull(key: String): String? = if (isNull(key)) null else getString(key)
internal val DocumentPhase.terminal: Boolean get() = this == DocumentPhase.COMPLETED || this == DocumentPhase.FAILED

internal data class FinalDocumentResult(
    val documentId: String, val status: DocumentPhase, val title: String?, val wordCount: Long?,
    val c2dXml: String?, val sha256: String?, val message: String? = null,
) {
    fun validatedDocument(): C2dDocument? {
        require(status == DocumentPhase.COMPLETED && !title.isNullOrBlank() && wordCount != null && wordCount >= 0) { "最终结果不完整" }
        if (c2dXml == null) {
            require(sha256 == null && wordCount == 0L) { "空文档结果不一致" }
            return null
        }
        require(sha256(c2dXml.toByteArray(Charsets.UTF_8)) == sha256) { "文档摘要不匹配，请重新获取" }
        return C2dParser.document(c2dXml)
    }
    fun json(): JSONObject = JSONObject().put("documentId", documentId).put("schemaVersion", "0.1")
        .put("status", status.name).put("title", title ?: JSONObject.NULL).put("wordCount", wordCount ?: JSONObject.NULL)
        .put("needsReview", false).put("c2dXml", c2dXml ?: JSONObject.NULL).put("sha256", sha256 ?: JSONObject.NULL)
        .put("message", message ?: JSONObject.NULL)
    companion object {
        fun parse(json: JSONObject, expectedId: String): FinalDocumentResult {
            require(json.getString("documentId") == expectedId && json.getString("schemaVersion") == "0.1") { "返回的文档身份或版本不匹配" }
            require(!json.getBoolean("needsReview")) { "不支持的复核协议" }
            listOf("title", "wordCount", "c2dXml", "sha256").forEach { require(json.has(it)) { "结果缺少 $it" } }
            val result = FinalDocumentResult(expectedId, DocumentPhase.valueOf(json.getString("status")), json.stringOrNull("title"),
                if (json.isNull("wordCount")) null else json.getLong("wordCount"), json.stringOrNull("c2dXml"), json.stringOrNull("sha256"), json.stringOrNull("message"))
            require(result.wordCount == null || result.wordCount >= 0) { "字数无效" }
            if (result.status != DocumentPhase.COMPLETED) require(result.c2dXml == null && result.sha256 == null) { "非最终结果包含正文" }
            return result
        }
    }
}

internal data class PreviewBlock(val blockId: String, val version: Long, val xml: String) {
    fun json(): JSONObject = JSONObject().put("blockId", blockId).put("version", version).put("xml", xml)
    companion object {
        fun parse(json: JSONObject): PreviewBlock = PreviewBlock(json.getString("blockId"), json.getLong("version"), json.getString("xml")).also {
            require(it.blockId.isNotBlank() && it.version > 0)
            C2dParser.block(it.xml)
        }
    }
}
internal data class PreviewState(
    val documentId: String, val blocks: List<PreviewBlock> = emptyList(), val revision: Long = 0, val cursor: Long? = null,
    val status: DocumentPhase = DocumentPhase.IDLE, val completedOcrImages: Int = 0, val completedImages: Int = 0,
    val totalImages: Int = 0, val message: String? = null, val currentImageId: String? = null,
) {
    fun json(): JSONObject = JSONObject().put("documentId", documentId).put("blocks", JSONArray(blocks.map { it.json() }))
        .put("revision", revision).put("cursor", cursor ?: JSONObject.NULL).put("status", status.name)
        .put("completedOcrImages", completedOcrImages).put("completedImages", completedImages).put("totalImages", totalImages)
        .put("message", message ?: JSONObject.NULL)
        .put("currentImageId", currentImageId ?: JSONObject.NULL)
}
internal data class DocumentEvent(val id: Long, val type: String, val data: JSONObject)
internal class PreviewResetRequired : IllegalArgumentException("预览版本不一致，正在重新获取")

internal object PreviewReducer {
    private fun blocks(json: JSONObject): List<PreviewBlock> = json.getJSONArray("blocks").let { array ->
        List(array.length()) { PreviewBlock.parse(array.getJSONObject(it)) }
    }.also { require(it.map(PreviewBlock::blockId).distinct().size == it.size) }

    fun restore(json: JSONObject, expectedId: String): PreviewState {
        val cursor = if (json.isNull("cursor")) null else json.getLong("cursor")
        return snapshot(json, expectedId, cursor)
    }
    private fun snapshot(data: JSONObject, expectedId: String, cursor: Long?): PreviewState {
        require(data.getString("documentId") == expectedId && (cursor == null || cursor >= 0))
        return PreviewState(expectedId, blocks(data), data.getLong("revision"), cursor,
            DocumentPhase.valueOf(data.getString("status")), data.optInt("completedOcrImages"), data.optInt("completedImages"),
            data.optInt("totalImages"), data.stringOrNull("message"), data.stringOrNull("currentImageId")).also {
            require(it.revision >= 0 && it.totalImages >= 0 && it.completedImages in 0..it.totalImages && it.completedOcrImages in 0..it.totalImages)
        }
    }

    fun apply(old: PreviewState, event: DocumentEvent): PreviewState {
        require(event.data.getString("documentId") == old.documentId && event.id >= 0) { "事件文档身份无效" }
        if (old.status.terminal) return old
        if (event.type == "document.snapshot") {
            if (old.cursor != null && event.id < old.cursor) return old
            return snapshot(event.data, old.documentId, event.id)
        }
        if (old.cursor != null && event.id <= old.cursor) return old
        if (old.cursor == null || event.id != old.cursor + 1) throw PreviewResetRequired()
        val data = event.data
        val updated = when (event.type) {
            "blocks.patch" -> {
                if (data.getLong("baseRevision") != old.revision || data.getLong("revision") != old.revision + 1) throw PreviewResetRequired()
                val anchor = data.stringOrNull("afterBlockId")
                val index = if (anchor == null) 0 else old.blocks.indexOfFirst { it.blockId == anchor }.let { if (it < 0) throw PreviewResetRequired() else it + 1 }
                val removed = data.getJSONArray("removeBlockIds").let { a -> List(a.length()) { a.getString(it) } }
                if (index + removed.size > old.blocks.size || old.blocks.drop(index).take(removed.size).map { it.blockId } != removed) throw PreviewResetRequired()
                val inserted = blocks(data)
                inserted.forEach { next -> old.blocks.firstOrNull { it.blockId == next.blockId }?.let {
                    if (next.version < it.version || (next.version == it.version && next != it)) throw PreviewResetRequired()
                } }
                val next = old.blocks.take(index) + inserted + old.blocks.drop(index + removed.size)
                if (next.map { it.blockId }.distinct().size != next.size) throw PreviewResetRequired()
                old.copy(blocks = next, revision = data.getLong("revision"))
            }
            "document.progress" -> {
                val status = DocumentPhase.valueOf(data.getString("status"))
                require(!status.terminal)
                val total = data.getInt("totalImages")
                val ocr = data.getInt("completedOcrImages")
                val images = data.getInt("completedImages")
                require(total >= 0 && ocr in 0..total && images in 0..total)
                old.copy(status = status, totalImages = total, completedImages = images, completedOcrImages = ocr, currentImageId = data.stringOrNull("currentImageId"))
            }
            "document.completed" -> { require(data.getString("status") == "COMPLETED"); old.copy(status = DocumentPhase.COMPLETED) }
            "document.failed" -> { require(data.getString("status") == "FAILED"); old.copy(status = DocumentPhase.FAILED, message = data.stringOrNull("message")) }
            else -> throw IllegalArgumentException("不支持的文档事件")
        }
        return updated.copy(cursor = event.id)
    }
}
