package io.github.liuzijiancs.capture2doc.data.capture2doc.draft

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import java.io.File
import java.io.FileOutputStream
import java.util.UUID
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ScanDraftRepositoryInstrumentedTest {
    @Test
    fun ensureDraftRunsRecoveryAndCleansOrphansOnFirstAccess() = runBlocking {
        val filesDirectory = newTestFilesDirectory()
        try {
            val writer = ScanDraftRepository(filesDirectory)
            writer.createNewDraft()
            val pageFiles = writer.reservePage(pageId = "page-one", replaceExisting = false)
            createJpeg(File(pageFiles.originalPath))

            val orphanDirectory = File(filesDirectory, "scan_draft/pages/orphan").apply {
                assertTrue(mkdirs())
            }
            File(orphanDirectory, "late.tmp").writeText("orphan")

            val reloaded = ScanDraftRepository(filesDirectory)
            val recovered = reloaded.ensureDraft()

            assertEquals(listOf("page-one"), recovered.pages.map { it.pageId })
            assertEquals(ScanPageState.READY, recovered.pages.single().state)
            assertTrue(reloaded.isOriginalKnownValid("page-one"))
            assertTrue(File(pageFiles.normalizedPath).isFile)
            assertTrue(isDecodable(File(pageFiles.normalizedPath)))
            assertFalse(orphanDirectory.exists())
        } finally {
            filesDirectory.deleteRecursively()
        }
    }

    @Test
    fun deletedPageIgnoresLateStateAndCleansRecreatedFilesIdempotently() = runBlocking {
        val filesDirectory = newTestFilesDirectory()
        try {
            val repository = ScanDraftRepository(filesDirectory)
            repository.createNewDraft()
            val pageFiles = repository.reservePage(pageId = "page-one", replaceExisting = false)
            createJpeg(File(pageFiles.originalPath))
            repository.markNormalizing("page-one")

            assertTrue(repository.deletePage("page-one"))
            assertTrue(repository.draft.value?.pages.orEmpty().isEmpty())

            val manifest = File(filesDirectory, "scan_draft/draft.json")
            val manifestAfterDelete = manifest.readText()
            assertEquals(
                0,
                JSONObject(manifestAfterDelete).getJSONArray("pages").length(),
            )

            // Simulate a camera/normalization callback recreating files after manifest-first delete.
            createJpeg(File(pageFiles.originalPath))
            repository.markReady("page-one")
            repository.markFailed("page-one", "late failure")

            assertTrue(repository.draft.value?.pages.orEmpty().isEmpty())
            assertEquals(manifestAfterDelete, manifest.readText())
            assertTrue(repository.cleanupPageDirectory("page-one"))
            assertFalse(File(pageFiles.directoryPath).exists())
            assertTrue(repository.cleanupPageDirectory("page-one"))
        } finally {
            filesDirectory.deleteRecursively()
        }
    }

    @Test
    fun cleanupPageDirectoryRefusesReferencedPage() = runBlocking {
        val filesDirectory = newTestFilesDirectory()
        try {
            val repository = ScanDraftRepository(filesDirectory)
            repository.createNewDraft()
            val pageFiles = repository.reservePage(pageId = "page-one", replaceExisting = false)

            assertFalse(repository.cleanupPageDirectory("page-one"))
            assertTrue(File(pageFiles.directoryPath).isDirectory)
            assertEquals("page-one", repository.draft.value?.pages?.single()?.pageId)
        } finally {
            filesDirectory.deleteRecursively()
        }
    }

    @Test
    fun firstEnsureMarksCorruptOriginalFailedWithoutChangingPageOrder() = runBlocking {
        val filesDirectory = newTestFilesDirectory()
        try {
            val writer = ScanDraftRepository(filesDirectory)
            writer.createNewDraft()
            val first = writer.reservePage(pageId = "page-one", replaceExisting = false)
            val second = writer.reservePage(pageId = "page-two", replaceExisting = false)
            createJpeg(File(first.originalPath))
            File(second.originalPath).writeText("not a jpeg")

            val reloaded = ScanDraftRepository(filesDirectory)
            val recovered = reloaded.ensureDraft()

            assertEquals(listOf("page-one", "page-two"), recovered.pages.map { it.pageId })
            assertEquals(ScanPageState.READY, recovered.pages[0].state)
            assertEquals(ScanPageState.FAILED, recovered.pages[1].state)
            assertTrue(recovered.pages[1].errorMessage?.contains("原图缺失或损坏") == true)
            assertFalse(reloaded.hasValidOriginal("page-two"))
            assertNull(reloaded.errorMessage.value)
        } finally {
            filesDirectory.deleteRecursively()
        }
    }

    @Test
    fun recoverIncompletePagesWorksAfterRepositoryWasAlreadyInitialized() = runBlocking {
        val filesDirectory = newTestFilesDirectory()
        try {
            val repository = ScanDraftRepository(filesDirectory)
            repository.createNewDraft()
            val recoverable = repository.reservePage(
                pageId = "page-recoverable",
                replaceExisting = false,
            )
            repository.reservePage(pageId = "page-missing", replaceExisting = false)
            createJpeg(File(recoverable.originalPath))
            repository.markNormalizing("page-recoverable")

            val recovered = repository.recoverIncompletePages()

            assertEquals(
                listOf("page-recoverable", "page-missing"),
                recovered?.pages?.map { it.pageId },
            )
            assertEquals(ScanPageState.READY, recovered?.pages?.get(0)?.state)
            assertEquals(ScanPageState.FAILED, recovered?.pages?.get(1)?.state)
            assertTrue(isDecodable(File(recoverable.normalizedPath)))
        } finally {
            filesDirectory.deleteRecursively()
        }
    }

    @Test
    fun legacyRetakePlaceholderMigratesToFailureWithoutChangingOrder() = runBlocking {
        val filesDirectory = newTestFilesDirectory()
        try {
            val writer = ScanDraftRepository(filesDirectory)
            writer.createNewDraft()
            val first = writer.reservePage(pageId = "page-one", replaceExisting = false)
            val second = writer.reservePage(pageId = "page-two", replaceExisting = false)
            val third = writer.reservePage(pageId = "page-three", replaceExisting = false)
            createJpeg(File(first.originalPath))
            createJpeg(File(second.originalPath))
            createJpeg(File(third.originalPath))
            assertTrue(writer.movePage(pageId = "page-three", targetIndex = 0))
            // Construct an old-version fixture; production no longer exposes retake.
            val manifest = File(filesDirectory, "scan_draft/draft.json")
            val json = org.json.JSONObject(manifest.readText())
            val oldPages = json.getJSONArray("pages")
            repeat(oldPages.length()) { index ->
                val page = oldPages.getJSONObject(index)
                if (page.getString("pageId") == "page-two") page.put("state", "RETAKE_REQUIRED")
            }
            manifest.writeText(json.toString())
            File(second.directoryPath).deleteRecursively()

            val reloaded = ScanDraftRepository(filesDirectory)
            val recovered = reloaded.ensureDraft()

            assertEquals(
                listOf("page-three", "page-one", "page-two"),
                recovered.pages.map { it.pageId },
            )
            assertEquals(ScanPageState.FAILED, recovered.pages[2].state)
            assertFalse(File(second.directoryPath).exists())
        } finally {
            filesDirectory.deleteRecursively()
        }
    }

    @Test
    fun emptyDraftIsRemovedOnNextRepositoryInitialization() = runBlocking {
        val filesDirectory = newTestFilesDirectory()
        try {
            val writer = ScanDraftRepository(filesDirectory)
            writer.createNewDraft()
            writer.reservePage(pageId = "only-page", replaceExisting = false)
            assertTrue(writer.deletePage("only-page"))
            assertTrue(File(filesDirectory, "scan_draft/draft.json").isFile)

            val reloaded = ScanDraftRepository(filesDirectory)
            reloaded.initialize()

            assertNull(reloaded.draft.value)
            assertFalse(File(filesDirectory, "scan_draft/draft.json").exists())
            assertFalse(File(filesDirectory, "scan_draft/pages").exists())
        } finally {
            filesDirectory.deleteRecursively()
        }
    }

    @Test
    fun corruptManifestCleansOnlyDedicatedDraftScope() = runBlocking {
        val filesDirectory = newTestFilesDirectory()
        try {
            val orphan = File(filesDirectory, "scan_draft/pages/orphan/late.jpg").apply {
                assertTrue(parentFile?.mkdirs() == true)
                writeText("orphan")
            }
            File(filesDirectory, "scan_draft/draft.json").writeText("{not-json")
            val legacyCapture = File(filesDirectory, "captures/legacy.jpg").apply {
                assertTrue(parentFile?.mkdirs() == true)
                writeText("legacy")
            }

            val repository = ScanDraftRepository(filesDirectory)
            repository.initialize()

            assertNull(repository.draft.value)
            assertTrue(repository.errorMessage.value?.contains("无法读取本地扫描草稿") == true)
            assertFalse(orphan.exists())
            assertFalse(File(filesDirectory, "scan_draft/draft.json").exists())
            assertTrue(legacyCapture.isFile)
        } finally {
            filesDirectory.deleteRecursively()
        }
    }

    private fun newTestFilesDirectory(): File {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        return File(context.cacheDir, "scan-draft-test-${UUID.randomUUID()}").also {
            assertTrue(it.mkdirs())
        }
    }

    private fun createJpeg(file: File) {
        assertTrue(file.parentFile?.let { it.isDirectory || it.mkdirs() } == true)
        val bitmap = Bitmap.createBitmap(96, 64, Bitmap.Config.ARGB_8888)
        try {
            FileOutputStream(file).use { stream ->
                assertTrue(bitmap.compress(Bitmap.CompressFormat.JPEG, 95, stream))
                stream.fd.sync()
            }
        } finally {
            bitmap.recycle()
        }
    }

    private fun isDecodable(file: File): Boolean {
        val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, options)
        return options.outWidth > 0 && options.outHeight > 0
    }
}
