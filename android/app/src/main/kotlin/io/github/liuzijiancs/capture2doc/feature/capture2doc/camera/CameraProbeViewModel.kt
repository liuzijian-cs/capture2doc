package io.github.liuzijiancs.capture2doc.feature.capture2doc.camera

import android.app.Application
import android.graphics.Bitmap
import android.os.SystemClock
import android.util.Size
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.core.content.ContextCompat
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.liuzijiancs.capture2doc.Capture2DocApplication
import io.github.liuzijiancs.capture2doc.R
import io.github.liuzijiancs.capture2doc.core.model.CaptureArtifact
import io.github.liuzijiancs.capture2doc.core.model.CaptureTimings
import io.github.liuzijiancs.capture2doc.core.model.SCAN_PAGE_INVALID_ORIGINAL_MESSAGE
import io.github.liuzijiancs.capture2doc.core.model.ScanPage
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import io.github.liuzijiancs.capture2doc.data.capture2doc.image.ImageSize
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

internal sealed interface CameraGateState {
    data object PermissionRequired : CameraGateState
    data object RequestingPermission : CameraGateState
    data object PermissionDenied : CameraGateState
    data object StartingCamera : CameraGateState
    data object Ready : CameraGateState
    data class Error(val message: String) : CameraGateState
    data object CameraUnavailable : CameraGateState
}

internal enum class FocusStatus {
    FOCUSING,
    SUCCESS,
    FAILED,
}

internal data class FocusIndicatorState(
    val requestId: Long,
    val x: Float,
    val y: Float,
    val status: FocusStatus,
)

internal data class CaptureJob(
    val captureId: String,
    val captureDirectoryPath: String,
    val originalPath: String,
    val normalizedPath: String,
    val stage: CaptureStage,
    val requestedAtElapsedMillis: Long,
    val captureStartedAtElapsedMillis: Long? = null,
    val postviewAtElapsedMillis: Long? = null,
    val imageSavedAtElapsedMillis: Long? = null,
    val postviewBitmap: Bitmap? = null,
    val artifact: CaptureArtifact? = null,
    val errorMessage: String? = null,
)

internal data class CameraCandidateUi(
    val pageId: String,
    val pageNumber: Int,
    val originalPath: String,
    val normalizedPath: String,
    val state: ScanPageState,
    val postviewBitmap: Bitmap? = null,
    val hasSafeOriginal: Boolean = false,
    val canDelete: Boolean = false,
    val errorMessage: String? = null,
)

internal data class CameraProbeUiState(
    val gate: CameraGateState = CameraGateState.PermissionRequired,
    internal val captureJobs: List<CaptureJob> = emptyList(),
    internal val candidates: List<CameraCandidateUi> = emptyList(),
    internal val focusIndicator: FocusIndicatorState? = null,
    val captureResolution: ImageSize? = null,
    val postviewSupported: Boolean = false,
    val draftReady: Boolean = false,
    val draftMutationInProgress: Boolean = false,
    val retakePreparationInProgress: Boolean = false,
) {
    internal val canCapture: Boolean
        get() = gate is CameraGateState.Ready &&
            draftReady &&
            !retakePreparationInProgress &&
            !draftMutationInProgress &&
            canAcceptCapture(captureJobs.map(CaptureJob::stage))

    internal val canFinish: Boolean
        get() = draftReady &&
            !retakePreparationInProgress &&
            canFinishCandidates(candidates, draftMutationInProgress) &&
            captureJobs.none { it.stage.countsAgainstCaptureLimit } &&
            captureJobs.filter { it.stage == CaptureStage.NORMALIZING }.all { job ->
                candidates.any { candidate ->
                    candidate.pageId == job.captureId && candidate.hasSafeOriginal
                }
            }

    internal val hasAcceptedCaptureOrPage: Boolean
        get() = candidates.isNotEmpty() || captureJobs.any { !it.stage.isTerminal }
}

internal fun canFinishCandidates(
    candidates: List<CameraCandidateUi>,
    mutationInProgress: Boolean = false,
): Boolean = !mutationInProgress &&
    candidates.isNotEmpty() &&
    candidates.all { candidate ->
        candidate.hasSafeOriginal &&
            (candidate.state == ScanPageState.NORMALIZING ||
                candidate.state == ScanPageState.READY ||
                candidate.state == ScanPageState.FAILED)
    }

internal class CameraProbeViewModel(
    application: Application,
) : AndroidViewModel(application) {
    private val draftRepository = getApplication<Capture2DocApplication>().scanDraftRepository
    private val _uiState = MutableStateFlow(CameraProbeUiState())
    private val normalizationJobs = mutableMapOf<String, Job>()
    private val tombstonedPageIds = mutableSetOf<String>()
    private val pendingDeletedPageIds = mutableSetOf<String>()
    private val reservationInFlightPageIds = mutableSetOf<String>()
    private var retakeInFlightPageId: String? = null
    private var draftPages: List<ScanPage> = emptyList()
    private var repositoryInitialized = false
    private var draftMutationCount = 0
    private var nextFocusRequestId = 0L
    private var requestedRetakePageId: String? = null
    private var retakePageId: String? = null

    val uiState = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            draftRepository.draft.collect { draft ->
                draftPages = draft?.pages.orEmpty()
                resolveRetakePage()
                publishCandidates()
                if (repositoryInitialized) reconcileTransientJobs()
            }
        }
        viewModelScope.launch {
            try {
                draftRepository.initialize()
                draftRepository.recoverIncompletePages()
                repositoryInitialized = true
                draftPages = draftRepository.draft.value?.pages.orEmpty()
                resolveRetakePage()
                _uiState.update { it.copy(draftReady = true) }
                publishCandidates()
                reconcileTransientJobs()
            } catch (error: Exception) {
                onCameraError(error)
            }
        }
    }

    fun initializeSession(requestedRetakePageId: String?) {
        this.requestedRetakePageId = requestedRetakePageId
        resolveRetakePage()
        publishCandidates()
    }

    fun synchronizePermission(granted: Boolean) {
        val current = _uiState.value
        if (!granted) {
            val gate = if (current.gate is CameraGateState.PermissionDenied) {
                current.gate
            } else {
                CameraGateState.PermissionRequired
            }
            _uiState.value = current.copy(gate = gate)
            return
        }

        if (
            current.gate is CameraGateState.PermissionRequired ||
            current.gate is CameraGateState.RequestingPermission ||
            current.gate is CameraGateState.PermissionDenied
        ) {
            _uiState.value = current.copy(gate = CameraGateState.StartingCamera)
        }
    }

    fun onPermissionRequestStarted() {
        _uiState.update { it.copy(gate = CameraGateState.RequestingPermission) }
    }

    fun onPermissionResult(granted: Boolean) {
        _uiState.update {
            it.copy(
                gate = if (granted) {
                    CameraGateState.StartingCamera
                } else {
                    CameraGateState.PermissionDenied
                },
            )
        }
    }

    fun onCameraReady(
        resolution: Size?,
        postviewSupported: Boolean,
    ) {
        _uiState.update {
            it.copy(
                gate = CameraGateState.Ready,
                captureResolution = resolution?.let { size ->
                    ImageSize(size.width, size.height)
                },
                postviewSupported = postviewSupported,
            )
        }
    }

    fun onCameraStopped() {
        _uiState.update { current ->
            if (current.gate is CameraGateState.Ready) {
                current.copy(gate = CameraGateState.StartingCamera)
            } else {
                current
            }
        }
    }

    fun onCameraUnavailable() {
        _uiState.update { it.copy(gate = CameraGateState.CameraUnavailable) }
    }

    fun onCameraError(error: Throwable) {
        _uiState.update {
            it.copy(
                gate = CameraGateState.Error(
                    error.message ?: getApplication<Application>()
                        .getString(R.string.camera_error_unknown),
                ),
            )
        }
    }

    internal fun capture(session: CameraSessionHandle) {
        if (!_uiState.value.canCapture) return

        val shouldReplacePage = retakePageId != null
        val captureId = retakePageId ?: createCaptureId()
        retakePageId = null
        val pageFiles = draftRepository.pageFiles(captureId)
        val requestedAt = SystemClock.elapsedRealtime()
        reservationInFlightPageIds += captureId
        addOrReplaceJob(
            CaptureJob(
                captureId = captureId,
                captureDirectoryPath = pageFiles.directoryPath,
                originalPath = pageFiles.originalPath,
                normalizedPath = pageFiles.normalizedPath,
                stage = CaptureStage.QUEUED,
                requestedAtElapsedMillis = requestedAt,
            ),
        )

        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) {
                    draftRepository.reservePage(
                        pageId = captureId,
                        replaceExisting = shouldReplacePage,
                    )
                }
            } catch (error: Exception) {
                reservationInFlightPageIds -= captureId
                if (shouldReplacePage) retakePageId = captureId
                failJob(
                    captureId = captureId,
                    message = error.message ?: getApplication<Application>()
                        .getString(R.string.camera_error_unknown),
                )
                return@launch
            }
            reservationInFlightPageIds -= captureId

            if (captureId in tombstonedPageIds) {
                runCatching { cleanupTombstonedPage(captureId) }
                return@launch
            }

            val outputOptions = ImageCapture.OutputFileOptions.Builder(
                File(pageFiles.originalPath),
            ).build()
            session.imageCapture.takePicture(
                outputOptions,
                ContextCompat.getMainExecutor(getApplication()),
                object : ImageCapture.OnImageSavedCallback {
                    override fun onCaptureStarted() {
                        updateJob(captureId) { job ->
                            job.copy(
                                stage = job.stage.advanceTo(CaptureStage.CAPTURING),
                                captureStartedAtElapsedMillis =
                                    job.captureStartedAtElapsedMillis
                                        ?: SystemClock.elapsedRealtime(),
                            )
                        }
                    }

                    override fun onCaptureProcessProgressed(progress: Int) {
                        if (progress > 0) {
                            updateJob(captureId) { job ->
                                job.copy(stage = job.stage.advanceTo(CaptureStage.SAVING))
                            }
                        }
                    }

                    override fun onPostviewBitmapAvailable(bitmap: Bitmap) {
                        updateJob(captureId) { job ->
                            job.copy(
                                stage = job.stage.advanceTo(CaptureStage.SAVING),
                                postviewAtElapsedMillis = job.postviewAtElapsedMillis
                                    ?: SystemClock.elapsedRealtime(),
                                postviewBitmap = bitmap,
                            )
                        }
                    }

                    override fun onImageSaved(
                        outputFileResults: ImageCapture.OutputFileResults,
                    ) {
                        if (captureId in tombstonedPageIds) {
                            viewModelScope.launch {
                                runCatching { cleanupTombstonedPage(captureId) }
                            }
                            return
                        }
                        val savedAt = SystemClock.elapsedRealtime()
                        updateJob(captureId) { job ->
                            job.copy(
                                stage = job.stage.advanceTo(CaptureStage.SAVING),
                                imageSavedAtElapsedMillis =
                                    job.imageSavedAtElapsedMillis ?: savedAt,
                            )
                        }
                        startNormalization(captureId)
                    }

                    override fun onError(exception: ImageCaptureException) {
                        onCaptureError(
                            captureId = captureId,
                            message = exception.message?.takeIf(String::isNotBlank)
                                ?: getApplication<Application>()
                                    .getString(R.string.camera_error_unknown),
                            clearDirectory = true,
                        )
                    }
                },
            )
        }
    }

    fun deleteCandidate(pageId: String) {
        val candidate = _uiState.value.candidates.firstOrNull { it.pageId == pageId }
            ?: return
        if (
            !candidate.canDelete ||
            _uiState.value.draftMutationInProgress ||
            pageId in reservationInFlightPageIds ||
            _uiState.value.captureJobs.any {
                it.captureId == pageId && it.stage.countsAgainstCaptureLimit
            }
        ) {
            return
        }

        tombstonedPageIds += pageId
        pendingDeletedPageIds += pageId
        beginDraftMutation()
        publishCandidates()
        viewModelScope.launch {
            var deleted = false
            try {
                deleted = runCatching { draftRepository.deletePage(pageId) }
                    .getOrDefault(false)
                if (deleted) {
                    normalizationJobs.remove(pageId)?.cancelAndJoin()
                }
            } finally {
                if (deleted) {
                    runCatching { cleanupTombstonedPage(pageId) }
                    removeJob(pageId)
                } else {
                    tombstonedPageIds -= pageId
                }
                pendingDeletedPageIds -= pageId
                endDraftMutation()
                publishCandidates()
            }
        }
    }

    fun moveCandidate(pageId: String, targetIndex: Int) {
        val candidates = _uiState.value.candidates
        if (
            _uiState.value.draftMutationInProgress ||
            candidates.none { it.pageId == pageId } ||
            targetIndex !in candidates.indices
        ) {
            return
        }
        beginDraftMutation()
        viewModelScope.launch {
            try {
                runCatching { draftRepository.movePage(pageId, targetIndex) }
            } finally {
                endDraftMutation()
            }
        }
    }

    fun prepareRetake(pageId: String, onPrepared: (Boolean) -> Unit) {
        val page = draftPages.firstOrNull { it.pageId == pageId }
        if (
            page == null ||
            page.state == ScanPageState.CAPTURING ||
            _uiState.value.captureJobs.any {
                it.captureId == pageId && it.stage.countsAgainstCaptureLimit
            } ||
            retakeInFlightPageId != null
        ) {
            onPrepared(false)
            return
        }
        retakeInFlightPageId = pageId
        _uiState.update { it.copy(retakePreparationInProgress = true) }
        viewModelScope.launch {
            try {
                val prepared = runCatching {
                    normalizationJobs.remove(pageId)?.cancelAndJoin()
                    val result = draftRepository.prepareRetake(pageId)
                    if (result) removeJob(pageId)
                    result
                }.getOrElse { false }
                onPrepared(prepared)
            } finally {
                if (retakeInFlightPageId == pageId) retakeInFlightPageId = null
                _uiState.update { it.copy(retakePreparationInProgress = false) }
            }
        }
    }

    fun retryCamera() {
        _uiState.update { it.copy(gate = CameraGateState.StartingCamera) }
    }

    fun canFinishNow(): Boolean = _uiState.value.canFinish

    fun onFocusStarted(x: Float, y: Float): Long {
        val requestId = ++nextFocusRequestId
        _uiState.update {
            it.copy(
                focusIndicator = FocusIndicatorState(
                    requestId = requestId,
                    x = x,
                    y = y,
                    status = FocusStatus.FOCUSING,
                ),
            )
        }
        return requestId
    }

    fun onFocusResult(requestId: Long, successful: Boolean) {
        _uiState.update { current ->
            val focus = current.focusIndicator
            if (focus?.requestId != requestId) {
                current
            } else {
                current.copy(
                    focusIndicator = focus.copy(
                        status = if (successful) FocusStatus.SUCCESS else FocusStatus.FAILED,
                    ),
                )
            }
        }
    }

    fun clearFocusIndicator(requestId: Long) {
        _uiState.update { current ->
            if (current.focusIndicator?.requestId == requestId) {
                current.copy(focusIndicator = null)
            } else {
                current
            }
        }
    }

    private fun startNormalization(captureId: String) {
        normalizationJobs.remove(captureId)?.cancel()
        val task = viewModelScope.launch(start = CoroutineStart.LAZY) {
            val captureJob = _uiState.value.captureJobs
                .firstOrNull { it.captureId == captureId }
                ?: return@launch
            try {
                if (!draftRepository.hasValidOriginal(captureId)) {
                    markCaptureFailed(
                        captureId = captureId,
                        message = SCAN_PAGE_INVALID_ORIGINAL_MESSAGE,
                        clearDirectory = false,
                    )
                    return@launch
                }
                draftRepository.markNormalizing(captureId)
                updateJob(captureId) { job ->
                    job.copy(stage = job.stage.advanceTo(CaptureStage.NORMALIZING))
                }
                val result = draftRepository.normalizePage(captureId)
                if (captureId in tombstonedPageIds) {
                    runCatching { cleanupTombstonedPage(captureId) }
                    return@launch
                }

                val latestJob = _uiState.value.captureJobs
                    .firstOrNull { it.captureId == captureId }
                    ?: return@launch
                val artifact = CaptureArtifact(
                    captureId = captureId,
                    originalPath = latestJob.originalPath,
                    normalizedPath = latestJob.normalizedPath,
                    originalWidth = result.sourceSize.width,
                    originalHeight = result.sourceSize.height,
                    originalBytes = result.sourceBytes,
                    normalizedWidth = result.outputSize.width,
                    normalizedHeight = result.outputSize.height,
                    normalizedBytes = result.outputBytes,
                    sourceRotationDegrees = result.sourceRotationDegrees,
                    timings = CaptureTimings(
                        requestToCaptureStartedMillis = latestJob
                            .captureStartedAtElapsedMillis
                            ?.minus(latestJob.requestedAtElapsedMillis),
                        requestToPostviewMillis = latestJob.postviewAtElapsedMillis
                            ?.minus(latestJob.requestedAtElapsedMillis),
                        requestToImageSavedMillis = (
                            latestJob.imageSavedAtElapsedMillis
                                ?: SystemClock.elapsedRealtime()
                            ) - latestJob.requestedAtElapsedMillis,
                        normalizationMillis = result.processingDurationMillis,
                    ),
                )
                draftRepository.markReady(captureId)
                updateJob(captureId) { job ->
                    job.copy(
                        stage = CaptureStage.READY,
                        postviewBitmap = null,
                        artifact = artifact,
                        errorMessage = null,
                    )
                }
            } catch (cancelled: CancellationException) {
                if (captureId in tombstonedPageIds) {
                    runCatching { cleanupTombstonedPage(captureId) }
                } else {
                    throw cancelled
                }
            } catch (error: Exception) {
                if (captureId in tombstonedPageIds) {
                    runCatching { cleanupTombstonedPage(captureId) }
                } else {
                    File(captureJob.normalizedPath).delete()
                    markCaptureFailed(
                        captureId = captureId,
                        message = error.message
                            ?: getApplication<Application>()
                                .getString(R.string.camera_error_unknown),
                        clearDirectory = false,
                    )
                }
            } finally {
                if (normalizationJobs[captureId] === coroutineContext[Job]) {
                    normalizationJobs.remove(captureId)
                }
            }
        }
        normalizationJobs[captureId] = task
        task.start()
    }

    private fun onCaptureError(
        captureId: String,
        message: String,
        clearDirectory: Boolean,
    ) {
        if (captureId in tombstonedPageIds) {
            viewModelScope.launch {
                runCatching { cleanupTombstonedPage(captureId) }
            }
            return
        }
        updateJob(captureId) { job ->
            job.copy(
                stage = CaptureStage.FAILED,
                postviewBitmap = null,
                errorMessage = message,
            )
        }
        viewModelScope.launch {
            markCaptureFailed(captureId, message, clearDirectory)
        }
    }

    private suspend fun markCaptureFailed(
        captureId: String,
        message: String,
        clearDirectory: Boolean,
    ) {
        val originalIsValid = !clearDirectory &&
            runCatching { draftRepository.hasValidOriginal(captureId) }.getOrDefault(false)
        val effectiveMessage = if (!clearDirectory && !originalIsValid) {
            SCAN_PAGE_INVALID_ORIGINAL_MESSAGE
        } else {
            message
        }
        _uiState.value.captureJobs
            .firstOrNull { it.captureId == captureId }
            ?.let { job ->
                withContext(Dispatchers.IO) {
                    File(job.normalizedPath).delete()
                    if (clearDirectory || !originalIsValid) File(job.originalPath).delete()
                }
            }
        updateJob(captureId) { job ->
            job.copy(
                stage = CaptureStage.FAILED,
                postviewBitmap = null,
                errorMessage = effectiveMessage,
            )
        }
        runCatching { draftRepository.markFailed(captureId, effectiveMessage) }
    }

    private fun addOrReplaceJob(job: CaptureJob) {
        _uiState.update { current ->
            current.copy(
                captureJobs = current.captureJobs.filterNot {
                    it.captureId == job.captureId
                } + job,
            )
        }
        publishCandidates()
    }

    private fun failJob(captureId: String, message: String) {
        updateJob(captureId) { job ->
            job.copy(stage = CaptureStage.FAILED, errorMessage = message)
        }
        viewModelScope.launch { draftRepository.markFailed(captureId, message) }
    }

    private fun updateJob(
        captureId: String,
        transform: (CaptureJob) -> CaptureJob,
    ) {
        if (captureId in tombstonedPageIds) return
        _uiState.update { current ->
            val index = current.captureJobs.indexOfFirst { it.captureId == captureId }
            if (index < 0) return@update current
            val jobs = current.captureJobs.toMutableList()
            jobs[index] = transform(jobs[index])
            current.copy(captureJobs = jobs)
        }
        publishCandidates()
    }

    private fun removeJob(captureId: String) {
        _uiState.update { current ->
            current.copy(
                captureJobs = current.captureJobs.filterNot { it.captureId == captureId },
            )
        }
    }

    private fun publishCandidates() {
        _uiState.update { current ->
            val jobsByPageId = current.captureJobs.associateBy(CaptureJob::captureId)
            val candidates = draftPages
                .filterNot { it.pageId in pendingDeletedPageIds }
                .mapIndexed { index, page ->
                    val job = jobsByPageId[page.pageId]
                    val effectiveState = page.effectiveState(job)
                    val original = draftRepository.resolve(page.originalRelativePath)
                    val originalKnownValid =
                        draftRepository.isOriginalKnownValid(page.pageId)
                    val hasSafeOriginal = original.isFile && original.length() > 0L &&
                        originalKnownValid &&
                        (effectiveState == ScanPageState.NORMALIZING ||
                            effectiveState == ScanPageState.READY ||
                            effectiveState == ScanPageState.FAILED)
                    CameraCandidateUi(
                        pageId = page.pageId,
                        pageNumber = index + 1,
                        originalPath = original.path,
                        normalizedPath = draftRepository.resolve(page.normalizedRelativePath).path,
                        state = effectiveState,
                        postviewBitmap = job?.postviewBitmap,
                        hasSafeOriginal = hasSafeOriginal,
                        canDelete = effectiveState != ScanPageState.CAPTURING &&
                            !current.draftMutationInProgress,
                        errorMessage = job?.errorMessage ?: page.errorMessage,
                    )
                }
            current.copy(candidates = candidates)
        }
    }

    private fun resolveRetakePage() {
        retakePageId = requestedRetakePageId?.takeIf { pageId ->
            draftPages.firstOrNull { it.pageId == pageId }?.state ==
                ScanPageState.RETAKE_REQUIRED
        }
    }

    private fun ScanPage.effectiveState(job: CaptureJob?): ScanPageState = when {
        job?.stage == CaptureStage.QUEUED ||
            job?.stage == CaptureStage.CAPTURING ||
            job?.stage == CaptureStage.SAVING -> ScanPageState.CAPTURING
        job?.stage == CaptureStage.FAILED -> ScanPageState.FAILED
        job?.stage == CaptureStage.READY -> ScanPageState.READY
        job?.stage == CaptureStage.NORMALIZING -> ScanPageState.NORMALIZING
        state == ScanPageState.RETAKE_REQUIRED -> ScanPageState.RETAKE_REQUIRED
        else -> state
    }

    private fun beginDraftMutation() {
        draftMutationCount += 1
        _uiState.update { it.copy(draftMutationInProgress = true) }
        publishCandidates()
    }

    private fun endDraftMutation() {
        draftMutationCount = (draftMutationCount - 1).coerceAtLeast(0)
        _uiState.update {
            it.copy(draftMutationInProgress = draftMutationCount > 0)
        }
        publishCandidates()
    }

    private fun reconcileTransientJobs() {
        val referencedPageIds = draftPages.mapTo(mutableSetOf(), ScanPage::pageId)
        val orphanedIds = _uiState.value.captureJobs
            .map(CaptureJob::captureId)
            .filter { pageId ->
                pageId !in referencedPageIds &&
                    pageId !in reservationInFlightPageIds &&
                    pageId !in tombstonedPageIds
            }
        orphanedIds.forEach { pageId ->
            tombstonedPageIds += pageId
            viewModelScope.launch {
                runCatching { normalizationJobs.remove(pageId)?.cancelAndJoin() }
                runCatching { cleanupTombstonedPage(pageId) }
                removeJob(pageId)
            }
        }
    }

    private suspend fun cleanupTombstonedPage(pageId: String) {
        draftRepository.cleanupPageDirectory(pageId)
    }

    private fun createCaptureId(): String {
        val timestamp = LocalDateTime.now().format(CAPTURE_ID_TIME_FORMAT)
        val suffix = UUID.randomUUID().toString().take(8)
        return "${timestamp}_$suffix"
    }

    private companion object {
        val CAPTURE_ID_TIME_FORMAT: DateTimeFormatter =
            DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss_SSS")
    }
}
