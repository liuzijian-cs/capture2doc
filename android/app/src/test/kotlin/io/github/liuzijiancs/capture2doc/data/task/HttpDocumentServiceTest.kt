package io.github.liuzijiancs.capture2doc.data.task

import io.github.liuzijiancs.capture2doc.core.model.DocumentPhase
import io.github.liuzijiancs.capture2doc.data.document.FinalDocumentResult
import io.github.liuzijiancs.capture2doc.data.document.sha256
import java.nio.file.Files
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class HttpDocumentServiceTest {
    private val service = HttpDocumentService({ "test-device-token" })
    private fun response(body: String, code: Int = 200) = MockResponse().setResponseCode(code).setBody(body).setHeader("Content-Type", "application/json")
    private val xml = "<document xmlns=\"urn:capture2doc:c2d:1\" schema-version=\"0.1\"><title>文档</title><p>正文 &amp; 中文</p></document>"
    private fun complete() = FinalDocumentResult("remote", DocumentPhase.COMPLETED, "文档", 7, xml, sha256(xml.toByteArray())).json().toString()

    @Test fun createUsesBearerAndPersistentIdentity() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(response("{\"documentId\":\"remote\"}"))
            assertEquals("remote", service.create(server.url("/").toString(), "client-id"))
            val request = server.takeRequest()
            assertEquals("POST", request.method)
            assertEquals("/v1/documents", request.path)
            assertEquals("Bearer test-device-token", request.getHeader("Authorization"))
            assertEquals("client-id", request.getHeader("Idempotency-Key"))
            assertEquals("client-id", JSONObject(request.body.readUtf8()).getString("clientTaskId"))
        }
    }
    @Test fun uploadConfirmsHashAndEncodesPath() = runBlocking {
        val file = Files.createTempFile("c2d-upload-", ".jpg").toFile()
        try {
            file.writeBytes(byteArrayOf(1, 2, 3, 4))
            MockWebServer().use { server ->
                server.enqueue(response(JSONObject().put("pageId", "page A").put("sha256", sha256(file.readBytes())).toString()))
                service.upload(server.url("/").toString(), "remote", "page A", file)
                val request = server.takeRequest()
                assertEquals("/v1/documents/remote/pages/page%20A", request.path)
                assertEquals("image/jpeg", request.getHeader("Content-Type"))
                assertEquals(sha256(file.readBytes()), request.getHeader("X-Content-SHA256"))
                assertEquals("Bearer test-device-token", request.getHeader("Authorization"))
                assertArrayEquals(file.readBytes(), request.body.readByteArray())
            }
        } finally { file.delete() }
    }
    @Test fun finalizeKeepsFrozenOrderAndIdempotencyKey() = runBlocking {
        MockWebServer().use { server ->
            repeat(2) { server.enqueue(response("{\"accepted\":true}", 202)); service.finalize(server.url("/").toString(), "remote", "client", listOf("C", "A")) }
            repeat(2) {
                val request = server.takeRequest()
                assertEquals("client-finalize", request.getHeader("Idempotency-Key"))
                assertEquals("[\"C\",\"A\"]", JSONObject(request.body.readUtf8()).getJSONArray("pageIds").toString())
            }
        }
    }
    @Test fun completedResponseUsesPublicJsonAndRawUtf8Hash() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(response(complete()))
            val result = service.status(server.url("/").toString(), "remote")
            assertEquals(DocumentPhase.COMPLETED, result.status)
            assertEquals(7L, result.wordCount)
            assertNotNull(result.validatedDocument())
            assertEquals(xml, result.c2dXml)
        }
    }
    @Test fun legacyFlagResponseIsRejected() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(response("""{"documentId":"remote","flag":"COMPLETED","plainText":"old","pages":[]}"""))
            assertFails { service.status(server.url("/").toString(), "remote") }
        }
    }
    @Test fun invalidIdentityAndVersionAreRejected() = runBlocking {
        MockWebServer().use { server ->
            listOf(complete().replace("remote", "other"), complete().replace("0.1", "9.0")).forEach { body ->
                server.enqueue(response(body)); assertFails { service.status(server.url("/").toString(), "remote") }
            }
        }
    }
    @Test fun connectionTestIsAuthenticatedReadOnly() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(response("""{"openapi":"3.1.0","components":{"schemas":{"DocumentResult":{}}}}"""))
            service.testConnection(server.url("/").toString())
            val request = server.takeRequest()
            assertEquals("GET", request.method)
            assertEquals("/openapi.json", request.path)
            assertEquals("Bearer test-device-token", request.getHeader("Authorization"))
        }
    }
    @Test fun errorsRetainSafeMessageAndRetryClassification() = runBlocking {
        MockWebServer().use { server ->
            listOf(401, 409, 429, 503).forEach { code ->
                server.enqueue(response("{\"message\":\"可读说明\"}", code))
                try { service.create(server.url("/").toString(), "client"); fail("error accepted") }
                catch (e: ServiceException) {
                    assertEquals(code, e.statusCode)
                    assertEquals(code == 429 || code == 503, e.retryable)
                    if (code != 401) assertEquals("可读说明", e.message)
                }
            }
        }
    }
    @Test fun missingTokenDoesNotSendRequest() = runBlocking {
        MockWebServer().use { server ->
            assertFails { HttpDocumentService().create(server.url("/").toString(), "client") }
            assertEquals(0, server.requestCount)
        }
    }
    @Test fun sseResumesWithoutDroppingEvents() = runBlocking {
        MockWebServer().use { server ->
            val body = (8..107).joinToString("") { "id: $it\nevent: document.progress\ndata: {\"documentId\":\"remote\"}\n\n" }
            server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream").setBody(body))
            val events = service.events(server.url("/").toString(), "remote", 7).toList()
            assertEquals((8L..107L).toList(), events.map { it.id })
            val request = server.takeRequest()
            assertEquals("7", request.getHeader("Last-Event-ID"))
            assertEquals("Bearer test-device-token", request.getHeader("Authorization"))
        }
    }
    @Test fun terminal204ClosesStreamForGetAnd409RequiresSnapshot() = runBlocking {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setResponseCode(204))
            assertTrue(service.events(server.url("/").toString(), "remote", 7).toList().isEmpty())
            server.enqueue(response("{}", 409))
            try { service.events(server.url("/").toString(), "remote", 7).toList(); fail("cursor error ignored") }
            catch (e: ServiceException) { assertEquals(409, e.statusCode) }
        }
    }
    private suspend fun assertFails(block: suspend () -> Unit) {
        try { block(); fail("invalid result accepted") } catch (_: Exception) { }
    }
}
