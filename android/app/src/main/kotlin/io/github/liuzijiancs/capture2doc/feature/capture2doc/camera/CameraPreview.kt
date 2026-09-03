package io.github.liuzijiancs.capture2doc.feature.capture2doc.camera

import android.view.OrientationEventListener
import android.view.Surface
import androidx.camera.core.CameraControl
import androidx.camera.core.CameraSelector
import androidx.camera.core.FocusMeteringAction
import androidx.camera.core.ImageCapture
import androidx.camera.core.MeteringPointFactory
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

internal class CameraSessionHandle(
    val imageCapture: ImageCapture,
    val captureResolution: android.util.Size?,
    val postviewSupported: Boolean,
    private val cameraControl: CameraControl,
    private val meteringPointFactory: MeteringPointFactory,
    private val callbackExecutor: Executor,
) {
    fun focusAt(
        x: Float,
        y: Float,
        onResult: (Boolean) -> Unit,
    ) {
        val point = meteringPointFactory.createPoint(x, y)
        val action = FocusMeteringAction.Builder(point)
            .setAutoCancelDuration(3, TimeUnit.SECONDS)
            .build()
        val result = cameraControl.startFocusAndMetering(action)
        result.addListener(
            {
                onResult(
                    runCatching { result.get().isFocusSuccessful }
                        .getOrDefault(false),
                )
            },
            callbackExecutor,
        )
    }
}

@Composable
internal fun CameraPreview(
    onSessionReady: (CameraSessionHandle?) -> Unit,
    onError: (Throwable) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    val lifecycleOwner = LocalLifecycleOwner.current
    val currentOnSessionReady by rememberUpdatedState(onSessionReady)
    val currentOnError by rememberUpdatedState(onError)
    val boundImageCapture = remember { AtomicReference<ImageCapture?>(null) }
    val previewView = remember {
        PreviewView(context).apply {
            implementationMode = PreviewView.ImplementationMode.PERFORMANCE
            scaleType = PreviewView.ScaleType.FIT_CENTER
        }
    }

    AndroidView(
        factory = { previewView },
        modifier = modifier,
    )

    DisposableEffect(lifecycleOwner, previewView) {
        val mainExecutor = ContextCompat.getMainExecutor(context)
        val providerFuture = ProcessCameraProvider.getInstance(context)
        var cameraProvider: ProcessCameraProvider? = null
        var preview: Preview? = null
        var imageCapture: ImageCapture? = null
        var disposed = false

        providerFuture.addListener(
            {
                if (disposed) return@addListener
                try {
                    cameraProvider = providerFuture.get()
                    val cameraInfo = cameraProvider?.getCameraInfo(
                        CameraSelector.DEFAULT_BACK_CAMERA,
                    ) ?: error("Unable to query back camera information")
                    val capabilities = ImageCapture.getImageCaptureCapabilities(cameraInfo)
                    val postviewSupported = capabilities.isPostviewSupported
                    val newPreview = Preview.Builder()
                        .setResolutionSelector(
                            CameraCaptureProfile.previewResolutionSelector(),
                        )
                        .build()
                    val imageCaptureBuilder = ImageCapture.Builder()
                        .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                        .setJpegQuality(CameraCaptureProfile.JPEG_QUALITY)
                        .setFlashMode(ImageCapture.FLASH_MODE_OFF)
                        .setResolutionSelector(
                            CameraCaptureProfile.captureResolutionSelector(),
                        )
                        .setTargetRotation(previewView.display?.rotation ?: Surface.ROTATION_0)
                    if (postviewSupported) {
                        imageCaptureBuilder
                            .setPostviewEnabled(true)
                            .setPostviewResolutionSelector(
                                CameraCaptureProfile.postviewResolutionSelector(),
                            )
                    }
                    val newImageCapture = imageCaptureBuilder.build()

                    preview = newPreview
                    imageCapture = newImageCapture
                    newPreview.surfaceProvider = previewView.surfaceProvider
                    cameraProvider?.unbindAll()
                    val camera = cameraProvider?.bindToLifecycle(
                        lifecycleOwner,
                        CameraSelector.DEFAULT_BACK_CAMERA,
                        newPreview,
                        newImageCapture,
                    ) ?: error("Unable to bind back camera")
                    boundImageCapture.set(newImageCapture)
                    currentOnSessionReady(
                        CameraSessionHandle(
                            imageCapture = newImageCapture,
                            captureResolution = newImageCapture.resolutionInfo?.resolution,
                            postviewSupported = postviewSupported,
                            cameraControl = camera.cameraControl,
                            meteringPointFactory = previewView.meteringPointFactory,
                            callbackExecutor = mainExecutor,
                        ),
                    )
                } catch (error: Throwable) {
                    boundImageCapture.set(null)
                    currentOnSessionReady(null)
                    currentOnError(error)
                }
            },
            mainExecutor,
        )

        onDispose {
            disposed = true
            boundImageCapture.set(null)
            currentOnSessionReady(null)
            val useCases = listOfNotNull(preview, imageCapture).toTypedArray()
            if (useCases.isNotEmpty()) {
                cameraProvider?.unbind(*useCases)
            }
        }
    }

    DisposableEffect(context, previewView) {
        val orientationListener = object : OrientationEventListener(context) {
            override fun onOrientationChanged(orientation: Int) {
                snapOrientationToSurfaceRotation(orientation)?.let { rotation ->
                    boundImageCapture.get()?.targetRotation = rotation
                }
            }
        }
        orientationListener.enable()
        onDispose { orientationListener.disable() }
    }
}
