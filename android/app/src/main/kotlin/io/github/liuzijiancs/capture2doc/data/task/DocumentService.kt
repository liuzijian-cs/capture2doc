package io.github.liuzijiancs.capture2doc.data.task

import io.github.liuzijiancs.capture2doc.BuildConfig
import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import io.github.liuzijiancs.capture2doc.core.model.PageRemotePhase
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URLEncoder
import java.security.MessageDigest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

internal data class RemoteDocument(
    val phase: DocumentPhase,
    val title: String?,
    val wordCount: Long?,
    val plainText: String?,
    val message: String?,
    val pages: Map<String, PageRemotePhase>,
)

internal class ServiceException(message: String, val retryable: Boolean) : IOException(message)

/** Domain interface, not a CameraX or XML contract. See docs/android_task_protocol.md. */
internal interface DocumentService {
    suspend fun create(baseUrl: String, clientTaskId: String): String
    suspend fun upload(baseUrl: String, documentId: String, pageId: String, image: File)
    suspend fun finalize(baseUrl: String, documentId: String, clientTaskId: String, pageIds: List<String>)
    suspend fun status(baseUrl: String, documentId: String): RemoteDocument
}

internal class HttpDocumentService : DocumentService {
    override suspend fun create(baseUrl: String, clientTaskId: String): String = request(
        baseUrl, "/v1/documents", "POST",
        JSONObject().put("clientTaskId", clientTaskId),
        headers = mapOf("Idempotency-Key" to clientTaskId),
    ).getString("documentId").also { require(it.isNotBlank()) { "服务端未返回文档 ID" } }

    override suspend fun upload(baseUrl: String, documentId: String, pageId: String, image: File) {
        val hash = withContext(Dispatchers.IO) {
            val digest = MessageDigest.getInstance("SHA-256")
            image.inputStream().use { input ->
                val buffer = ByteArray(8192)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                }
            }
            digest.digest().joinToString("") { "%02x".format(it) }
        }
        val result = request(baseUrl, "${path(documentId)}/pages/${segment(pageId)}", "PUT",
            file = image, headers = mapOf("X-Content-SHA256" to hash))
        require(result.getString("pageId") == pageId && result.getString("sha256") == hash) {
            "上传确认与页面内容不匹配"
        }
    }

    override suspend fun finalize(
        baseUrl: String, documentId: String, clientTaskId: String, pageIds: List<String>,
    ) {
        val result = request(baseUrl, "${path(documentId)}/finalize", "POST",
            JSONObject().put("pageIds", JSONArray(pageIds)),
            headers = mapOf("Idempotency-Key" to "$clientTaskId-finalize"))
        require(result.getBoolean("accepted")) { "服务端未接受最终页序" }
    }

    override suspend fun status(baseUrl: String, documentId: String): RemoteDocument {
        val result = request(baseUrl, path(documentId), "GET")
        require(result.getString("documentId") == documentId) { "返回文档 ID 不匹配" }
        val phase = DocumentPhase.valueOf(result.getString("flag"))
        val title = result.nullableString("title")
        val count = if (result.isNull("wordCount")) null else result.getLong("wordCount")
        val content = result.nullableString("plainText")
        require(count == null || count >= 0) { "文档字数无效" }
        if (phase == DocumentPhase.COMPLETED) {
            require(!title.isNullOrBlank() && count != null && content != null) { "最终文档结果不完整" }
        }
        val pages = result.getJSONArray("pages")
        val pageStates = buildMap {
            repeat(pages.length()) { index ->
                val page = pages.getJSONObject(index)
                val id = page.getString("pageId")
                require(id.isNotBlank() && !containsKey(id)) { "页面状态重复或无效" }
                put(id, PageRemotePhase.valueOf(page.getString("flag")))
            }
        }
        return RemoteDocument(phase, title, count, content, result.nullableString("message"), pageStates)
    }

    private suspend fun request(
        baseUrl: String,
        path: String,
        method: String,
        json: JSONObject? = null,
        file: File? = null,
        headers: Map<String, String> = emptyMap(),
    ): JSONObject = withContext(Dispatchers.IO) {
        if (baseUrl.isBlank()) throw ServiceException("尚未配置文档服务，照片保存在本地。联网同步需配置 capture2doc.baseUrl。", false)
        val uri = URI(baseUrl)
        require(uri.host != null && uri.userInfo == null && uri.query == null && uri.fragment == null) {
            "服务地址无效"
        }
        require(uri.scheme == "https" || (BuildConfig.DEBUG && uri.scheme == "http")) {
            "正式版本仅支持 HTTPS 服务"
        }
        val connection = URI(baseUrl.trimEnd('/') + path).toURL().openConnection() as HttpURLConnection
        try {
            connection.requestMethod = method
            connection.connectTimeout = 15_000
            connection.readTimeout = 30_000
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Accept", "application/json")
            headers.forEach { (key, value) -> connection.setRequestProperty(key, value) }
            if (file != null || json != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", if (file != null) "image/jpeg" else "application/json; charset=utf-8")
                if (file != null) {
                    connection.setFixedLengthStreamingMode(file.length())
                    connection.outputStream.use { output -> file.inputStream().use { it.copyTo(output) } }
                } else {
                    val bytes = requireNotNull(json).toString().toByteArray(Charsets.UTF_8)
                    connection.setFixedLengthStreamingMode(bytes.size)
                    connection.outputStream.use { it.write(bytes) }
                }
            }
            val code = connection.responseCode
            if (code !in 200..299) {
                throw ServiceException("文档服务请求失败（HTTP $code）", code == 408 || code == 429 || code >= 500)
            }
            val body = connection.inputStream.bufferedReader(Charsets.UTF_8).use { it.readText() }
            JSONObject(body)
        } finally {
            connection.disconnect()
        }
    }

    private fun path(documentId: String) = "/v1/documents/${segment(documentId)}"
    private fun segment(value: String) = URLEncoder.encode(value, "UTF-8").replace("+", "%20")
    private fun JSONObject.nullableString(key: String): String? =
        if (isNull(key)) null else getString(key)
}
