package io.github.liuzijiancs.capture2doc.data.task

import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import java.net.ServerSocket
import java.net.InetAddress
import java.io.BufferedInputStream
import java.io.DataInputStream
import java.nio.file.Files
import java.security.MessageDigest
import kotlin.concurrent.thread
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class HttpDocumentServiceTest {
    @Test fun createSendsPersistentIdempotencyIdentity() = runBlocking {
        server { url, requests ->
            assertEquals("remote", HttpDocumentService().create(url, "client-id"))
            val request = requests.single()
            assertEquals("POST /v1/documents", request.route)
            assertEquals("client-id", request.idempotencyKey)
            assertEquals("client-id", JSONObject(String(request.body)).getString("clientTaskId"))
        }
    }
    @Test fun uploadSendsFileDigestAndVerifiesConfirmation() = runBlocking {
        val image = Files.createTempFile("upload-contract-", ".jpg").toFile()
        try {
            image.writeBytes(byteArrayOf(1, 2, 3, 4))
            server { url, requests ->
                HttpDocumentService().upload(url, "remote", "page A", image)
                val request = requests.single()
                assertEquals("PUT /v1/documents/remote/pages/page%20A", request.route)
                assertEquals("image/jpeg", request.contentType)
                assertArrayEquals(image.readBytes(), request.body)
            }
        } finally { image.delete() }
    }
    @Test fun finalizeSendsExactlyTheFrozenOrder() = runBlocking {
        server { url, requests ->
            HttpDocumentService().finalize(url, "remote", "client", listOf("C", "A"))
            assertEquals("client-finalize", requests.single().idempotencyKey)
            assertEquals("[\"C\",\"A\"]", JSONObject(String(requests.single().body)).getJSONArray("pageIds").toString())
        }
    }
    @Test fun completedResponseProvidesFinalTitleCountAndText() = runBlocking {
        server(statusBody = completeResult()) { url, _ ->
            val result = HttpDocumentService().status(url, "remote")
            assertEquals(DocumentPhase.COMPLETED, result.phase)
            assertEquals(7L, result.wordCount)
            assertEquals("文档", result.title)
        }
    }
    @Test fun unknownFlagDoesNotBecomeCompleted() = runBlocking {
        server(statusBody = completeResult().replace("COMPLETED", "NEW_FLAG")) { url, _ ->
            try { HttpDocumentService().status(url, "remote"); fail("unknown flag accepted") }
            catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun completedMissingContentIsRejected() = runBlocking {
        server(statusBody = completeResult().replace("\"正文\"", "null")) { url, _ ->
            try { HttpDocumentService().status(url, "remote"); fail("incomplete result accepted") }
            catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun wrongDocumentIdIsRejected() = runBlocking {
        server(statusBody = completeResult().replace("remote", "other")) { url, _ ->
            try { HttpDocumentService().status(url, "remote"); fail("wrong ID accepted") }
            catch (_: IllegalArgumentException) { }
        }
    }
    @Test fun httpFailureDistinguishesRetryFromBlocked() = runBlocking {
        for (code in listOf(401, 429, 503)) server(code = code) { url, _ ->
            try { HttpDocumentService().create(url, "client"); fail("HTTP failure accepted") }
            catch (error: ServiceException) { assertEquals(code != 401, error.retryable) }
        }
    }
    @Test fun missingConfigurationCannotCreateFakeDocument() = runBlocking {
        try { HttpDocumentService().create("", "client"); fail("fake ID created") }
        catch (error: ServiceException) { assertFalse(error.retryable) }
    }

    private data class Request(val route: String, val body: ByteArray, val idempotencyKey: String?, val contentType: String?)
    private fun completeResult() = """{"documentId":"remote","flag":"COMPLETED","title":"文档","wordCount":7,"plainText":"正文","pages":[]}"""
    private suspend fun server(code: Int = 200, statusBody: String = completeResult(), block: suspend (String, List<Request>) -> Unit) {
        val requests = java.util.Collections.synchronizedList(mutableListOf<Request>())
        val server = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))
        val failure = java.util.concurrent.atomic.AtomicReference<Throwable?>()
        val worker = thread(isDaemon = true) {
            try { server.accept().use { socket ->
            socket.soTimeout = 5_000
            val input = BufferedInputStream(socket.getInputStream())
            fun line(): String {
                val bytes = java.io.ByteArrayOutputStream()
                while (true) {
                    val value = input.read()
                    if (value < 0 || value == 10) break
                    if (value != 13) bytes.write(value)
                }
                return bytes.toString("UTF-8")
            }
            val first = line().split(' ')
            val method = first[0]
            val path = first[1]
            val headers = mutableMapOf<String, String>()
            while (true) {
                val header = line()
                if (header.isEmpty()) break
                headers[header.substringBefore(':').lowercase()] = header.substringAfter(':').trim()
            }
            val body = ByteArray(headers["content-length"]?.toInt() ?: 0)
            DataInputStream(input).readFully(body)
            requests += Request("$method $path", body, headers["idempotency-key"], headers["content-type"])
            val response = when {
                method == "PUT" -> JSONObject().put("pageId", "page A")
                    .put("sha256", MessageDigest.getInstance("SHA-256").digest(body).joinToString("") { "%02x".format(it) }).toString()
                path.endsWith("finalize") -> "{\"accepted\":true}"
                method == "POST" -> "{\"documentId\":\"remote\"}"
                else -> statusBody
            }.toByteArray(Charsets.UTF_8)
            socket.getOutputStream().use {
                it.write("HTTP/1.1 $code Test\r\nContent-Type: application/json\r\nContent-Length: ${response.size}\r\nConnection: close\r\n\r\n".toByteArray())
                it.write(response)
            }
            } } catch (error: Throwable) { if (!server.isClosed) failure.set(error) }
        }
        try { block("http://127.0.0.1:${server.localPort}", requests) }
        finally { server.close(); worker.join(1_000) }
        failure.get()?.let { throw it }
    }
}
