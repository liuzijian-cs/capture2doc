package io.github.liuzijiancs.capture2doc.data.capture2doc.draft

import android.graphics.BitmapFactory
import android.util.AtomicFile
import io.github.liuzijiancs.capture2doc.core.model.SCAN_DRAFT_SCHEMA_VERSION
import io.github.liuzijiancs.capture2doc.core.model.SCAN_PAGE_INVALID_ORIGINAL_MESSAGE
import io.github.liuzijiancs.capture2doc.core.model.ScanDraft
import io.github.liuzijiancs.capture2doc.core.model.ScanPage
import io.github.liuzijiancs.capture2doc.core.model.ScanPageFiles
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import io.github.liuzijiancs.capture2doc.data.capture2doc.image.ImageNormalizer
import io.github.liuzijiancs.capture2doc.data.capture2doc.image.NormalizeResult
import java.io.File
import java.io.FileOutputStream
import java.nio.charset.StandardCharsets
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

internal class ScanDraftRepository(
    filesDirectory: File,
    private val imageNormalizer: ImageNormalizer = ImageNormalizer(),
    private val now: () -> Long = System::currentTimeMillis,
    private val keepEmptyDraft: Boolean = false,
    private val preserveUnreadable: Boolean = false,
) {
    private val rootDirectory = File(filesDirectory, ROOT_DIRECTORY_NAME)
    private val pagesDirectory = File(rootDirectory, PAGES_DIRECTORY_NAME)
    private val manifestFile = AtomicFile(File(rootDirectory, MANIFEST_FILE_NAME))
    private val mutex = Mutex()
    private val normalizationMutex = Mutex()
    private val validOriginalPageIds = ConcurrentHashMap.newKeySet<String>()
    private val _draft = MutableStateFlow<ScanDraft?>(null)
    private val _errorMessage = MutableStateFlow<String?>(null)
    private var initialized = false

    val draft = _draft.asStateFlow()
    val errorMessage = _errorMessage.asStateFlow()

    suspend fun initialize() = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
        }
    }

    suspend fun createNewDraft(): ScanDraft = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
            normalizationMutex.withLock {
                clearManifestAndPages()
                val timestamp = now()
                val draft = ScanDraft(
                    draftId = UUID.randomUUID().toString(),
                    createdAtEpochMillis = timestamp,
                    updatedAtEpochMillis = timestamp,
                    pages = emptyList(),
                )
                writeManifest(draft)
                _draft.value = draft
                _errorMessage.value = null
                draft
            }
        }
    }

    suspend fun ensureDraft(): ScanDraft = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
            _draft.value ?: createDraftLocked()
        }
    }

    /**
     * Reconciles capture work that was interrupted after this repository was initialized.
     *
     * The Application owns the repository, so an Activity/ViewModel recreation does not run the
     * cold-start recovery in [ensureInitializedLocked] again. A newly created camera ViewModel can
     * call this once before starting new capture work. It must not be called while an older camera
     * job is still writing the same files.
     */
    suspend fun recoverIncompletePages(): ScanDraft? = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
            val current = _draft.value ?: return@withLock null
            if (current.pages.none { it.state.isIncompleteCapture }) {
                cleanupUnreferencedDirectories(current)
                return@withLock current
            }

            val recoveredPages = current.pages.map { page ->
                if (page.state.isIncompleteCapture) recoverPage(page) else page
            }
            val recovered = current.copy(
                updatedAtEpochMillis = now(),
                pages = recoveredPages,
            )
            writeManifest(recovered)
            _draft.value = recovered
            cleanupUnreferencedDirectories(recovered)
            recovered
        }
    }

    suspend fun reservePage(
        pageId: String,
        replaceExisting: Boolean,
    ): ScanPageFiles = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
            val current = _draft.value ?: createDraftLocked()
            val files = pageFiles(pageId)
            validOriginalPageIds -= pageId
            check(File(files.directoryPath).exists() || File(files.directoryPath).mkdirs()) {
                "无法创建页面目录"
            }
            val page = ScanPage(
                pageId = pageId,
                originalRelativePath = relativeOriginalPath(pageId),
                normalizedRelativePath = relativeNormalizedPath(pageId),
                state = ScanPageState.CAPTURING,
            )
            val existingIndex = current.pages.indexOfFirst { it.pageId == pageId }
            val pages = when {
                replaceExisting && existingIndex >= 0 -> current.pages.toMutableList().apply {
                    this[existingIndex] = page
                }
                existingIndex >= 0 -> error("页面已经存在")
                else -> current.pages + page
            }
            updateDraftLocked(current, pages)
            files
        }
    }

    suspend fun markNormalizing(pageId: String) {
        updatePage(
            pageId = pageId,
            canUpdate = { it.state == ScanPageState.CAPTURING },
        ) { it.copy(state = ScanPageState.NORMALIZING, errorMessage = null) }
    }

    suspend fun markReady(pageId: String) {
        updatePage(
            pageId = pageId,
            canUpdate = { it.state == ScanPageState.NORMALIZING },
        ) { it.copy(state = ScanPageState.READY, errorMessage = null) }
        if (_draft.value?.pages?.any {
                it.pageId == pageId && it.state == ScanPageState.READY
            } == true
        ) {
            validOriginalPageIds += pageId
        }
    }

    suspend fun markFailed(pageId: String, message: String) {
        updatePage(
            pageId = pageId,
            canUpdate = { it.state.isIncompleteCapture },
        ) { it.copy(state = ScanPageState.FAILED, errorMessage = message) }
    }

    /**
     * Normalizes one page under the same process-wide lock used by recovery.
     *
     * State transitions remain explicit at the call site so a late result for a deleted page is
     * harmless: repository page updates already ignore IDs absent from the manifest.
     */
    suspend fun normalizePage(pageId: String): NormalizeResult = withContext(Dispatchers.IO) {
        val files = pageFiles(pageId)
        normalizationMutex.withLock {
            check(
                _draft.value?.pages?.any {
                    it.pageId == pageId && it.state.isIncompleteCapture
                } == true,
            ) { "页面已不再需要归一化" }
            normalizeFiles(
                source = File(files.originalPath),
                destination = File(files.normalizedPath),
            )
        }
    }

    suspend fun hasValidOriginal(pageId: String): Boolean = withContext(Dispatchers.IO) {
        val valid = isDecodable(File(pageFiles(pageId).originalPath))
        if (valid) validOriginalPageIds += pageId else validOriginalPageIds -= pageId
        valid
    }

    fun isOriginalKnownValid(pageId: String): Boolean = pageId in validOriginalPageIds

    suspend fun deletePage(pageId: String): Boolean = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
            val current = _draft.value ?: return@withLock false
            if (current.pages.none { it.pageId == pageId }) return@withLock false
            normalizationMutex.withLock {
                val remaining = current.pages.filterNot { it.pageId == pageId }
                if (remaining.isEmpty()) {
                    val empty = current.copy(updatedAtEpochMillis = now(), pages = emptyList())
                    writeManifest(empty)
                    _draft.value = empty
                } else {
                    updateDraftLocked(current, remaining)
                }
                validOriginalPageIds -= pageId
                File(pageFiles(pageId).directoryPath).deleteRecursively()
                true
            }
        }
    }

    /**
     * Removes files recreated by late camera or normalization I/O after a page was deleted.
     *
     * The operation is idempotent and refuses to remove a directory while the page is still
     * referenced by the current manifest. Callers should invoke it after the corresponding I/O
     * job has reached a safe completion point.
     */
    suspend fun cleanupPageDirectory(pageId: String): Boolean = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
            if (_draft.value?.pages?.any { it.pageId == pageId } == true) {
                return@withLock false
            }

            normalizationMutex.withLock {
                validOriginalPageIds -= pageId
                val directory = File(pageFiles(pageId).directoryPath)
                !directory.exists() || (directory.deleteRecursively() && !directory.exists())
            }
        }
    }

    suspend fun movePage(pageId: String, targetIndex: Int): Boolean = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
            val current = _draft.value ?: return@withLock false
            val fromIndex = current.pages.indexOfFirst { it.pageId == pageId }
            if (fromIndex < 0 || targetIndex !in current.pages.indices) return@withLock false
            if (fromIndex == targetIndex) return@withLock true
            val pages = current.pages.toMutableList()
            val page = pages.removeAt(fromIndex)
            pages.add(targetIndex, page)
            updateDraftLocked(current, pages)
            true
        }
    }

    suspend fun discardDraft() = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
            normalizationMutex.withLock {
                clearManifestAndPages()
                _draft.value = null
                _errorMessage.value = null
            }
        }
    }

    fun pageFiles(pageId: String): ScanPageFiles {
        validatePageId(pageId)
        val directory = File(pagesDirectory, pageId)
        return ScanPageFiles(
            pageId = pageId,
            directoryPath = directory.path,
            originalPath = File(directory, ORIGINAL_FILE_NAME).path,
            normalizedPath = File(directory, NORMALIZED_FILE_NAME).path,
        )
    }

    fun resolve(relativePath: String): File {
        require(!File(relativePath).isAbsolute && ".." !in relativePath.split('/')) {
            "草稿路径必须是安全的相对路径"
        }
        return File(rootDirectory, relativePath)
    }

    private suspend fun updatePage(
        pageId: String,
        canUpdate: (ScanPage) -> Boolean = { true },
        transform: (ScanPage) -> ScanPage,
    ) = withContext(Dispatchers.IO) {
        mutex.withLock {
            ensureInitializedLocked()
            val current = _draft.value ?: return@withLock
            val index = current.pages.indexOfFirst { it.pageId == pageId }
            if (index < 0) return@withLock
            if (!canUpdate(current.pages[index])) return@withLock
            val pages = current.pages.toMutableList().apply {
                this[index] = transform(this[index])
            }
            updateDraftLocked(current, pages)
        }
    }

    private suspend fun ensureInitializedLocked() {
        if (initialized) return
        val loaded = try {
            readManifest()
        } catch (error: Exception) {
            if (preserveUnreadable) throw error
            _errorMessage.value = "无法读取本地扫描草稿：${error.message ?: "文件已损坏"}"
            null
        }

        if (loaded == null && preserveUnreadable && pagesDirectory.listFiles().orEmpty().isNotEmpty()) {
            error("页面清单缺失，保留图片等待恢复")
        }
        if (loaded == null || (loaded.pages.isEmpty() && !keepEmptyDraft)) {
            // Without a usable manifest no page directory can be proven to belong to a draft.
            // Clearing only the dedicated scan_draft scope also removes files left by a crash
            // without touching legacy files/captures content.
            clearManifestAndPages()
            _draft.value = null
            initialized = true
            return
        }

        val recovered = recoverPages(loaded)
        writeManifest(recovered)
        _draft.value = recovered
        cleanupUnreferencedDirectories(recovered)
        initialized = true
    }

    private fun createDraftLocked(): ScanDraft {
        val timestamp = now()
        val draft = ScanDraft(
            draftId = UUID.randomUUID().toString(),
            createdAtEpochMillis = timestamp,
            updatedAtEpochMillis = timestamp,
            pages = emptyList(),
        )
        writeManifest(draft)
        _draft.value = draft
        return draft
    }

    private fun updateDraftLocked(current: ScanDraft, pages: List<ScanPage>) {
        val updated = current.copy(updatedAtEpochMillis = now(), pages = pages)
        writeManifest(updated)
        _draft.value = updated
    }

    private suspend fun recoverPages(draft: ScanDraft): ScanDraft {
        val recoveredPages = draft.pages.map { page -> recoverPage(page) }
        return draft.copy(updatedAtEpochMillis = now(), pages = recoveredPages)
    }

    private suspend fun recoverPage(page: ScanPage): ScanPage {
        if (page.state == ScanPageState.RETAKE_REQUIRED) return page.copy(
            state = ScanPageState.FAILED,
            errorMessage = "旧重拍已中断，请删除此页后重新拍摄",
        )
        val original = resolve(page.originalRelativePath)
        val normalized = resolve(page.normalizedRelativePath)
        val originalValid = isDecodable(original)
        if (originalValid) {
            validOriginalPageIds += page.pageId
        } else {
            validOriginalPageIds -= page.pageId
        }
        return when {
            !originalValid -> page.copy(
                state = ScanPageState.FAILED,
                errorMessage = SCAN_PAGE_INVALID_ORIGINAL_MESSAGE,
            )
            isDecodable(normalized) -> page.copy(
                state = ScanPageState.READY,
                errorMessage = null,
            )
            else -> try {
                normalizationMutex.withLock {
                    normalizeFiles(original, normalized)
                }
                page.copy(state = ScanPageState.READY, errorMessage = null)
            } catch (error: Exception) {
                normalized.delete()
                val sourceStillValid = isDecodable(original)
                if (sourceStillValid) {
                    validOriginalPageIds += page.pageId
                } else {
                    validOriginalPageIds -= page.pageId
                }
                page.copy(
                    state = ScanPageState.FAILED,
                    errorMessage = if (sourceStillValid) {
                        error.message ?: "页面恢复失败，请重拍"
                    } else {
                        SCAN_PAGE_INVALID_ORIGINAL_MESSAGE
                    },
                )
            }
        }
    }

    private fun normalizeFiles(source: File, destination: File): NormalizeResult {
        destination.delete()
        return imageNormalizer.normalize(source, destination)
    }

    private fun cleanupUnreferencedDirectories(draft: ScanDraft) {
        val referenced = draft.pages.mapTo(mutableSetOf(), ScanPage::pageId)
        validOriginalPageIds.retainAll(referenced)
        pagesDirectory.listFiles()?.filter { it.isDirectory && it.name !in referenced }
            ?.forEach(File::deleteRecursively)
    }

    private fun isDecodable(file: File): Boolean {
        if (!file.isFile || file.length() <= 0L) return false
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return false

        var sampleSize = 1
        val sourceMaxEdge = maxOf(bounds.outWidth, bounds.outHeight)
        while (sourceMaxEdge / (sampleSize * 2) >= VALIDATION_MAX_EDGE_PX) {
            sampleSize *= 2
        }
        val decoded = runCatching {
            BitmapFactory.decodeFile(
                file.path,
                BitmapFactory.Options().apply {
                    inSampleSize = sampleSize
                    inPreferredConfig = android.graphics.Bitmap.Config.RGB_565
                },
            )
        }.getOrNull() ?: return false
        return try {
            decoded.width > 0 && decoded.height > 0
        } finally {
            if (!decoded.isRecycled) decoded.recycle()
        }
    }

    private fun clearManifestAndPages() {
        validOriginalPageIds.clear()
        manifestFile.delete()
        pagesDirectory.deleteRecursively()
        if (!rootDirectory.exists()) rootDirectory.mkdirs()
    }

    private fun readManifest(): ScanDraft? {
        if (!manifestFile.baseFile.isFile && !File(manifestFile.baseFile.path + ".bak").isFile) return null
        val json = manifestFile.openRead().use { input ->
            input.readBytes().toString(StandardCharsets.UTF_8)
        }
        return decodeDraft(JSONObject(json))
    }

    private fun writeManifest(draft: ScanDraft) {
        check(rootDirectory.exists() || rootDirectory.mkdirs()) { "无法创建草稿目录" }
        var stream: FileOutputStream? = null
        try {
            stream = manifestFile.startWrite()
            stream.write(encodeDraft(draft).toString().toByteArray(StandardCharsets.UTF_8))
            stream.fd.sync()
            manifestFile.finishWrite(stream)
        } catch (error: Throwable) {
            stream?.let(manifestFile::failWrite)
            throw error
        }
    }

    private fun encodeDraft(draft: ScanDraft): JSONObject = JSONObject().apply {
        put("schemaVersion", draft.schemaVersion)
        put("draftId", draft.draftId)
        put("createdAtEpochMillis", draft.createdAtEpochMillis)
        put("updatedAtEpochMillis", draft.updatedAtEpochMillis)
        put("pages", JSONArray().apply {
            draft.pages.forEach { page ->
                put(JSONObject().apply {
                    put("pageId", page.pageId)
                    put("originalRelativePath", page.originalRelativePath)
                    put("normalizedRelativePath", page.normalizedRelativePath)
                    put("state", page.state.name)
                    put("errorMessage", page.errorMessage ?: JSONObject.NULL)
                })
            }
        })
    }

    private fun decodeDraft(json: JSONObject): ScanDraft {
        val schemaVersion = json.getInt("schemaVersion")
        require(schemaVersion == SCAN_DRAFT_SCHEMA_VERSION) {
            "不支持的草稿版本：$schemaVersion"
        }
        val pagesJson = json.getJSONArray("pages")
        val pages = buildList {
            repeat(pagesJson.length()) { index ->
                val page = pagesJson.getJSONObject(index)
                val pageId = page.getString("pageId").also(::validatePageId)
                add(
                    ScanPage(
                        pageId = pageId,
                        originalRelativePath = page.getString("originalRelativePath"),
                        normalizedRelativePath = page.getString("normalizedRelativePath"),
                        state = ScanPageState.valueOf(page.getString("state")),
                        errorMessage = page.optString("errorMessage")
                            .takeUnless { page.isNull("errorMessage") || it.isBlank() },
                    ),
                )
            }
        }
        require(pages.map(ScanPage::pageId).distinct().size == pages.size) {
            "草稿中存在重复页面"
        }
        pages.forEach {
            resolve(it.originalRelativePath)
            resolve(it.normalizedRelativePath)
        }
        return ScanDraft(
            schemaVersion = schemaVersion,
            draftId = json.getString("draftId"),
            createdAtEpochMillis = json.getLong("createdAtEpochMillis"),
            updatedAtEpochMillis = json.getLong("updatedAtEpochMillis"),
            pages = pages,
        )
    }

    private fun relativeOriginalPath(pageId: String) =
        "$PAGES_DIRECTORY_NAME/$pageId/$ORIGINAL_FILE_NAME"

    private fun relativeNormalizedPath(pageId: String) =
        "$PAGES_DIRECTORY_NAME/$pageId/$NORMALIZED_FILE_NAME"

    private fun validatePageId(pageId: String) {
        require(pageId.isNotBlank() && pageId != "." && pageId != ".." && '/' !in pageId && '\\' !in pageId) {
            "无效的页面 ID"
        }
    }

    private val ScanPageState.isIncompleteCapture: Boolean
        get() = this == ScanPageState.CAPTURING || this == ScanPageState.NORMALIZING

    private companion object {
        const val ROOT_DIRECTORY_NAME = "scan_draft"
        const val PAGES_DIRECTORY_NAME = "pages"
        const val MANIFEST_FILE_NAME = "draft.json"
        const val ORIGINAL_FILE_NAME = "original.jpg"
        const val NORMALIZED_FILE_NAME = "normalized_1280.jpg"
        const val VALIDATION_MAX_EDGE_PX = 1280
    }
}
