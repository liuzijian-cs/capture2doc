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
import io.github.liuzijiancs.capture2doc.R
import io.github.liuzijiancs.capture2doc.core.model.CaptureArtifact
import io.github.liuzijiancs.capture2doc.core.model.CaptureTimings
import io.github.liuzijiancs.capture2doc.data.capture2doc.image.ImageNormalizer
import io.github.liuzijiancs.capture2doc.data.capture2doc.image.ImageSize
import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
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

internal data class CameraProbeUiState(
    val gate: CameraGateState = CameraGateState.PermissionRequired,
    internal val captureJobs: List<CaptureJob> = emptyList(),
    internal val focusIndicator: FocusIndicatorState? = null,
    val captureResolution: ImageSize? = null,
    val postviewSupported: Boolean = false,
    val finishRequested: Boolean = false,
    val showResults: Boolean = false,
) {
    internal val canCapture: Boolean
        get() = gate is CameraGateState.Ready &&
            !finishRequested &&
            canAcceptCapture(captureJobs.map(CaptureJob::stage))

    internal val pendingSaveCount: Int
        get() = captureJobs.count { it.stage.countsAgainstCaptureLimit }

    internal val pendingWorkCount: Int
        get() = captureJobs.count { !it.stage.isTerminal }
}

internal class CameraProbeViewModel(
    application: Application,
) : AndroidViewModel(application) {
    private val imageNormalizer = ImageNormalizer()
    private val normalizationMutex = Mutex()
    private val _uiState = MutableStateFlow(CameraProbeUiState())
    private var nextFocusRequestId = 0L

    val uiState = _uiState.asStateFlow()

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
        _uiState.value = _uiState.value.copy(
            gate = CameraGateState.RequestingPermission,
        )
    }

    fun onPermissionResult(granted: Boolean) {
        _uiState.value = _uiState.value.copy(
            gate = if (granted) {
                CameraGateState.StartingCamera
            } else {
                CameraGateState.PermissionDenied
            },
        )
    }

    fun onCameraReady(
        resolution: Size?,
        postviewSupported: Boolean,
    ) {
        _uiState.value = _uiState.value.copy(
            gate = CameraGateState.Ready,
            captureResolution = resolution?.let { ImageSize(it.width, it.height) },
            postviewSupported = postviewSupported,
        )
    }

    fun onCameraStopped() {
        val current = _uiState.value
        if (!current.showResults && current.gate is CameraGateState.Ready) {
            _uiState.value = current.copy(gate = CameraGateState.StartingCamera)
        }
    }

    fun onCameraUnavailable() {
        _uiState.value = _uiState.value.copy(gate = CameraGateState.CameraUnavailable)
    }

    fun onCameraError(error: Throwable) {
        _uiState.value = _uiState.value.copy(
            gate = CameraGateState.Error(
                error.message ?: getApplication<Application>()
                    .getString(R.string.camera_error_unknown),
            ),
        )
    }

    internal fun capture(session: CameraSessionHandle): Boolean {
        val current = _uiState.value
        if (!current.canCapture) return false

        val captureId = createCaptureId()
        val captureDirectory = File(
            getApplication<Application>().filesDir,
            "captures/$captureId",
        )
        val originalFile = File(captureDirectory, ORIGINAL_FILE_NAME)
        val normalizedFile = File(captureDirectory, NORMALIZED_FILE_NAME)

        if (!captureDirectory.mkdirs()) {
            onCameraError(IllegalStateException("无法创建拍照目录：${captureDirectory.path}"))
            return false
        }

        val requestedAt = SystemClock.elapsedRealtime()
        val job = CaptureJob(
            captureId = captureId,
            captureDirectoryPath = captureDirectory.path,
            originalPath = originalFile.path,
            normalizedPath = normalizedFile.path,
            stage = CaptureStage.QUEUED,
            requestedAtElapsedMillis = requestedAt,
        )
        _uiState.value = current.copy(captureJobs = current.captureJobs + job)

        val outputOptions = ImageCapture.OutputFileOptions.Builder(originalFile).build()
        session.imageCapture.takePicture(
            outputOptions,
            ContextCompat.getMainExecutor(getApplication()),
            object : ImageCapture.OnImageSavedCallback {
                override fun onCaptureStarted() {
                    updateJob(captureId) {
                        if (it.stage.isTerminal) {
                            it
                        } else {
                            it.copy(
                                stage = it.stage.advanceTo(CaptureStage.CAPTURING),
                                captureStartedAtElapsedMillis =
                                    it.captureStartedAtElapsedMillis
                                        ?: SystemClock.elapsedRealtime(),
                            )
                        }
                    }
                }

                override fun onCaptureProcessProgressed(progress: Int) {
                    updateJob(captureId) {
                        if (progress > 0 && !it.stage.isTerminal) {
                            it.copy(stage = it.stage.advanceTo(CaptureStage.SAVING))
                        } else {
                            it
                        }
                    }
                }

                override fun onPostviewBitmapAvailable(bitmap: Bitmap) {
                    updateJob(captureId) {
                        if (it.stage.isTerminal) {
                            it
                        } else {
                            it.copy(
                                stage = it.stage.advanceTo(CaptureStage.SAVING),
                                postviewAtElapsedMillis = it.postviewAtElapsedMillis
                                    ?: SystemClock.elapsedRealtime(),
                                postviewBitmap = bitmap,
                            )
                        }
                    }
                }

                override fun onImageSaved(
                    outputFileResults: ImageCapture.OutputFileResults,
                ) {
                    val savedAt = SystemClock.elapsedRealtime()
                    updateJob(captureId) {
                        if (it.stage.isTerminal) {
                            it
                        } else {
                            it.copy(
                                stage = it.stage.advanceTo(CaptureStage.NORMALIZING),
                                imageSavedAtElapsedMillis = it.imageSavedAtElapsedMillis
                                    ?: savedAt,
                            )
                        }
                    }
                    val shouldNormalize = _uiState.value.captureJobs
                        .firstOrNull { it.captureId == captureId }
                        ?.stage == CaptureStage.NORMALIZING
                    if (shouldNormalize) {
                        normalizeCapture(captureId)
                    }
                }

                override fun onError(exception: ImageCaptureException) {
                    originalFile.delete()
                    normalizedFile.delete()
                    captureDirectory.delete()
                    failJob(
                        captureId = captureId,
                        message = exception.message?.takeIf(String::isNotBlank)
                            ?: getApplication<Application>()
                                .getString(R.string.camera_error_unknown),
                    )
                }
            },
        )
        return true
    }

    fun onFinishRequested() {
        val current = _uiState.value
        if (current.captureJobs.isEmpty()) return
        _uiState.value = current.copy(finishRequested = true)
        completeFinishIfReady()
    }

    fun continueCapturing() {
        _uiState.value = _uiState.value.copy(
            gate = CameraGateState.StartingCamera,
            finishRequested = false,
            showResults = false,
        )
    }

    fun retryJob(captureId: String) {
        val job = _uiState.value.captureJobs.firstOrNull { it.captureId == captureId }
            ?: return
        if (job.stage != CaptureStage.FAILED) return

        val originalFile = File(job.originalPath)
        if (!originalFile.isFile) {
            removeJob(captureId)
            continueCapturing()
            return
        }

        File(job.normalizedPath).delete()
        updateJob(captureId) {
            it.copy(
                stage = CaptureStage.NORMALIZING,
                errorMessage = null,
                artifact = null,
            )
        }
        normalizeCapture(captureId)
    }

    fun removeJob(captureId: String) {
        val current = _uiState.value
        val job = current.captureJobs.firstOrNull { it.captureId == captureId } ?: return
        val remainingJobs = current.captureJobs.filterNot { it.captureId == captureId }
        _uiState.value = current.copy(
            captureJobs = remainingJobs,
            showResults = current.showResults && remainingJobs.isNotEmpty(),
            finishRequested = current.finishRequested && remainingJobs.isNotEmpty(),
        )
        viewModelScope.launch(Dispatchers.IO) {
            File(job.captureDirectoryPath).deleteRecursively()
        }
    }

    fun retryCamera() {
        _uiState.value = _uiState.value.copy(
            gate = CameraGateState.StartingCamera,
            showResults = false,
            finishRequested = false,
        )
    }

    fun onFocusStarted(x: Float, y: Float): Long {
        val requestId = ++nextFocusRequestId
        _uiState.value = _uiState.value.copy(
            focusIndicator = FocusIndicatorState(
                requestId = requestId,
                x = x,
                y = y,
                status = FocusStatus.FOCUSING,
            ),
        )
        return requestId
    }

    fun onFocusResult(requestId: Long, successful: Boolean) {
        val current = _uiState.value
        val focus = current.focusIndicator
        if (focus?.requestId != requestId) return
        _uiState.value = current.copy(
            focusIndicator = focus.copy(
                status = if (successful) FocusStatus.SUCCESS else FocusStatus.FAILED,
            ),
        )
    }

    fun clearFocusIndicator(requestId: Long) {
        val current = _uiState.value
        if (current.focusIndicator?.requestId == requestId) {
            _uiState.value = current.copy(focusIndicator = null)
        }
    }

    private fun normalizeCapture(captureId: String) {
        viewModelScope.launch {
            val job = _uiState.value.captureJobs.firstOrNull { it.captureId == captureId }
                ?: return@launch
            try {
                val result = withContext(Dispatchers.IO) {
                    normalizationMutex.withLock {
                        imageNormalizer.normalize(
                            source = File(job.originalPath),
                            destination = File(job.normalizedPath),
                        )
                    }
                }
                val latestJob = _uiState.value.captureJobs
                    .firstOrNull { it.captureId == captureId }
                    ?: return@launch
                val imageSavedAt = latestJob.imageSavedAtElapsedMillis
                    ?: SystemClock.elapsedRealtime()
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
                        requestToImageSavedMillis = imageSavedAt -
                            latestJob.requestedAtElapsedMillis,
                        normalizationMillis = result.processingDurationMillis,
                    ),
                )
                updateJob(captureId) {
                    it.copy(
                        stage = CaptureStage.READY,
                        postviewBitmap = null,
                        artifact = artifact,
                        errorMessage = null,
                    )
                }
                completeFinishIfReady()
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                File(job.normalizedPath).delete()
                failJob(
                    captureId = captureId,
                    message = error.message ?: getApplication<Application>()
                        .getString(R.string.camera_error_unknown),
                )
            }
        }
    }

    private fun failJob(captureId: String, message: String) {
        updateJob(captureId) {
            if (it.stage == CaptureStage.READY) {
                it
            } else {
                it.copy(
                    stage = CaptureStage.FAILED,
                    postviewBitmap = null,
                    errorMessage = message,
                )
            }
        }
        completeFinishIfReady()
    }

    private fun updateJob(
        captureId: String,
        transform: (CaptureJob) -> CaptureJob,
    ) {
        val current = _uiState.value
        val index = current.captureJobs.indexOfFirst { it.captureId == captureId }
        if (index < 0) return
        val jobs = current.captureJobs.toMutableList()
        jobs[index] = transform(jobs[index])
        _uiState.value = current.copy(captureJobs = jobs)
    }

    private fun completeFinishIfReady() {
        val current = _uiState.value
        if (
            current.finishRequested &&
            current.captureJobs.isNotEmpty() &&
            current.captureJobs.all { it.stage.isTerminal }
        ) {
            _uiState.value = current.copy(showResults = true)
        }
    }

    private fun createCaptureId(): String {
        val timestamp = LocalDateTime.now().format(CAPTURE_ID_TIME_FORMAT)
        val suffix = UUID.randomUUID().toString().take(8)
        return "${timestamp}_$suffix"
    }

    private companion object {
        const val ORIGINAL_FILE_NAME = "original.jpg"
        const val NORMALIZED_FILE_NAME = "normalized_1280.jpg"
        val CAPTURE_ID_TIME_FORMAT: DateTimeFormatter =
            DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss_SSS")
    }
}
