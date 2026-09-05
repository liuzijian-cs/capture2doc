package io.github.liuzijiancs.capture2doc.data.task

import io.github.liuzijiancs.capture2doc.data.document.DocumentEvent
import io.github.liuzijiancs.capture2doc.data.document.FinalDocumentResult
import io.github.liuzijiancs.capture2doc.data.settings.normalizeServiceUrl
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import org.json.JSONArray
import org.json.JSONObject

internal typealias RemoteDocument = FinalDocumentResult
internal class ServiceException(message: String, val retryable: Boolean, val statusCode: Int? = null) : IOException(message)

internal interface DocumentService {
    suspend fun create(baseUrl: String, clientTaskId: String): String
    suspend fun upload(baseUrl: String, documentId: String, pageId: String, image: File)
    suspend fun finalize(baseUrl: String, documentId: String, clientTaskId: String, pageIds: List<String>)
    suspend fun status(baseUrl: String, documentId: String): RemoteDocument
    fun events(baseUrl: String, documentId: String, cursor: Long?): Flow<DocumentEvent> = error("Events unavailable")
}

internal class HttpDocumentService(
    private val tokenProvider: (String) -> String = { "" },
    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(45, TimeUnit.SECONDS)
        .writeTimeout(60, TimeUnit.SECONDS).followRedirects(false).followSslRedirects(false).build(),
) : DocumentService {
    private fun request(baseUrl: String, vararg segments: String): Request.Builder {
        if (baseUrl.isBlank()) throw ServiceException("请在连接设置中配置服务地址和设备令牌", false, 401)
        val base = normalizeServiceUrl(baseUrl)
        val token = tokenProvider(base)
        if (token.isBlank()) throw ServiceException("请在连接设置中填写设备令牌", false, 401)
        require(token.none { it <= ' ' || it.code > 126 }) { "设备令牌格式无效" }
        val url = base.toHttpUrl().newBuilder().apply { segments.forEach(::addPathSegment) }.build()
        return Request.Builder().url(url).header("Authorization", "Bearer $token").header("Accept", "application/json")
    }

    override suspend fun create(baseUrl: String, clientTaskId: String): String = json(
        request(baseUrl, "v1", "documents").header("Idempotency-Key", clientTaskId)
            .post(JSONObject().put("clientTaskId", clientTaskId).body()).build(),
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
        val result = json(request(baseUrl, "v1", "documents", documentId, "pages", pageId)
            .header("X-Content-SHA256", hash).put(image.asRequestBody("image/jpeg".toMediaType())).build())
        require(result.getString("pageId") == pageId && result.getString("sha256") == hash) { "上传确认与页面内容不匹配" }
    }

    override suspend fun finalize(baseUrl: String, documentId: String, clientTaskId: String, pageIds: List<String>) {
        val result = json(request(baseUrl, "v1", "documents", documentId, "finalize")
            .header("Idempotency-Key", "$clientTaskId-finalize")
            .post(JSONObject().put("pageIds", JSONArray(pageIds)).body()).build())
        require(result.getBoolean("accepted")) { "服务端未接受最终页序" }
    }

    override suspend fun status(baseUrl: String, documentId: String): RemoteDocument =
        FinalDocumentResult.parse(json(request(baseUrl, "v1", "documents", documentId).get().build()), documentId)

    suspend fun testConnection(baseUrl: String) {
        val result = json(request(baseUrl, "openapi.json").get().build())
        require(result.has("openapi") && result.getJSONObject("components").getJSONObject("schemas").has("DocumentResult")) {
            "服务未提供兼容的文档接口"
        }
    }

    override fun events(baseUrl: String, documentId: String, cursor: Long?): Flow<DocumentEvent> = callbackFlow {
        val request = request(baseUrl, "v1", "documents", documentId, "events")
            .header("Accept", "text/event-stream")
            .apply { cursor?.let { header("Last-Event-ID", it.toString()) } }.get().build()
        val source = EventSources.createFactory(client).newEventSource(request, object : EventSourceListener() {
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                try {
                    val event = DocumentEvent(requireNotNull(id).toLong(), requireNotNull(type), JSONObject(data))
                    trySend(event).getOrThrow()
                } catch (error: Exception) { close(error) }
            }
            override fun onClosed(eventSource: EventSource) { close() }
            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                if (response?.code == 204) close()
                else close(response?.let(::failure) ?: t ?: IOException("事件连接已中断"))
            }
        })
        awaitClose { source.cancel() }
    }.buffer(Channel.UNLIMITED)

    private suspend fun json(request: Request): JSONObject = suspendCancellableCoroutine { continuation ->
        val call = client.newCall(request)
        continuation.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) { if (!continuation.isCancelled) continuation.resumeWithException(e) }
            override fun onResponse(call: Call, response: Response) {
                try {
                    val value = response.use {
                        if (!it.isSuccessful) throw failure(it)
                        JSONObject(requireNotNull(it.body).string())
                    }
                    continuation.resume(value)
                } catch (error: Exception) { if (!continuation.isCancelled) continuation.resumeWithException(error) }
            }
        })
    }

    private fun failure(response: Response): ServiceException {
        val code = response.code
        val message = if (code == 401) "设备令牌无效，请更新连接设置" else runCatching {
            val body = JSONObject(response.peekBody(4096).string())
            body.optString("message").ifBlank { body.optString("detail") }.take(300)
        }.getOrNull().orEmpty().ifBlank { "文档服务请求失败（HTTP $code）" }
        return ServiceException(message, code == 408 || code == 429 || code >= 500, code)
    }

    private fun JSONObject.body() = toString().toRequestBody("application/json; charset=utf-8".toMediaType())
}
