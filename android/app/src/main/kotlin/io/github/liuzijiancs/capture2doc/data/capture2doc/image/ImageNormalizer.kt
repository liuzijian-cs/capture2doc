package io.github.liuzijiancs.capture2doc.data.capture2doc.image

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.os.SystemClock
import androidx.core.graphics.scale
import androidx.exifinterface.media.ExifInterface
import java.io.File
import java.io.FileOutputStream
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID
import kotlin.math.max
import kotlin.math.roundToInt

data class ImageSize(
    val width: Int,
    val height: Int,
)

data class NormalizeResult(
    val sourceSize: ImageSize,
    val sourceBytes: Long,
    val sourceRotationDegrees: Int,
    val outputSize: ImageSize,
    val outputBytes: Long,
    val processingDurationMillis: Long,
)

class ImageNormalizer {
    fun normalize(
        source: File,
        destination: File,
        maxEdgePx: Int = DEFAULT_MAX_EDGE_PX,
        jpegQuality: Int = DEFAULT_JPEG_QUALITY,
    ): NormalizeResult {
        require(source.isFile) { "Source image does not exist: ${source.path}" }
        require(maxEdgePx > 0) { "maxEdgePx must be positive" }
        require(jpegQuality in 0..100) { "jpegQuality must be between 0 and 100" }
        require(!destination.exists()) { "Destination already exists: ${destination.path}" }

        val startedAt = SystemClock.elapsedRealtime()
        val exif = ExifInterface(source)
        val rotationDegrees = exif.rotationDegrees
        val isFlipped = exif.isFlipped
        val sourceBounds = decodeBounds(source)
        val orientedSourceSize = calculateOrientedSize(sourceBounds, rotationDegrees)
        val requestedOutputSize = calculateTargetSize(
            width = orientedSourceSize.width,
            height = orientedSourceSize.height,
            maxEdgePx = maxEdgePx,
        )

        val decodeOptions = BitmapFactory.Options().apply {
            inSampleSize = calculateInSampleSize(sourceBounds, maxEdgePx)
            inPreferredConfig = Bitmap.Config.ARGB_8888
        }

        var decoded: Bitmap? = null
        var oriented: Bitmap? = null
        var output: Bitmap? = null
        var tempFile: File? = null

        try {
            decoded = checkNotNull(BitmapFactory.decodeFile(source.path, decodeOptions)) {
                "Unable to decode source image: ${source.path}"
            }
            oriented = applyExifTransform(decoded, rotationDegrees, isFlipped)

            output = if (
                oriented.width == requestedOutputSize.width &&
                oriented.height == requestedOutputSize.height
            ) {
                oriented
            } else {
                oriented.scale(
                    width = requestedOutputSize.width,
                    height = requestedOutputSize.height,
                )
            }

            val parentDirectory = checkNotNull(destination.parentFile) {
                "Destination must have a parent directory"
            }
            check(parentDirectory.exists() || parentDirectory.mkdirs()) {
                "Unable to create destination directory: ${parentDirectory.path}"
            }

            tempFile = File(
                parentDirectory,
                ".${destination.name}.${UUID.randomUUID()}.tmp",
            )
            FileOutputStream(tempFile).use { stream ->
                check(output.compress(Bitmap.CompressFormat.JPEG, jpegQuality, stream)) {
                    "Unable to encode normalized JPEG"
                }
                stream.fd.sync()
            }
            moveIntoPlace(tempFile, destination)
            tempFile = null

            return NormalizeResult(
                sourceSize = orientedSourceSize,
                sourceBytes = source.length(),
                sourceRotationDegrees = rotationDegrees,
                outputSize = requestedOutputSize,
                outputBytes = destination.length(),
                processingDurationMillis = SystemClock.elapsedRealtime() - startedAt,
            )
        } catch (error: Throwable) {
            tempFile?.delete()
            destination.delete()
            throw error
        } finally {
            recycleDistinct(output, oriented, decoded)
        }
    }

    private fun decodeBounds(source: File): ImageSize {
        val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(source.path, options)
        check(options.outWidth > 0 && options.outHeight > 0) {
            "Unable to read source dimensions: ${source.path}"
        }
        return ImageSize(options.outWidth, options.outHeight)
    }

    private fun applyExifTransform(
        source: Bitmap,
        rotationDegrees: Int,
        isFlipped: Boolean,
    ): Bitmap {
        if (rotationDegrees == 0 && !isFlipped) return source

        val matrix = Matrix().apply {
            if (isFlipped) {
                postScale(-1f, 1f)
            }
            if (rotationDegrees != 0) {
                postRotate(rotationDegrees.toFloat())
            }
        }
        return Bitmap.createBitmap(
            source,
            0,
            0,
            source.width,
            source.height,
            matrix,
            true,
        )
    }

    private fun moveIntoPlace(source: File, destination: File) {
        try {
            Files.move(
                source.toPath(),
                destination.toPath(),
                StandardCopyOption.ATOMIC_MOVE,
            )
        } catch (_: AtomicMoveNotSupportedException) {
            Files.move(source.toPath(), destination.toPath())
        }
    }

    private fun recycleDistinct(vararg bitmaps: Bitmap?) {
        val recycled = mutableListOf<Bitmap>()
        bitmaps.filterNotNull().forEach { bitmap ->
            if (recycled.none { it === bitmap }) {
                if (!bitmap.isRecycled) bitmap.recycle()
                recycled += bitmap
            }
        }
    }

    private fun calculateInSampleSize(source: ImageSize, maxEdgePx: Int): Int {
        var sampleSize = 1
        val sourceMaxEdge = max(source.width, source.height)
        while (sourceMaxEdge / (sampleSize * 2) >= maxEdgePx) {
            sampleSize *= 2
        }
        return sampleSize
    }

    companion object {
        const val DEFAULT_MAX_EDGE_PX = 1280
        const val DEFAULT_JPEG_QUALITY = 90
    }
}

internal fun calculateTargetSize(
    width: Int,
    height: Int,
    maxEdgePx: Int,
): ImageSize {
    require(width > 0 && height > 0) { "Image dimensions must be positive" }
    require(maxEdgePx > 0) { "maxEdgePx must be positive" }

    if (max(width, height) <= maxEdgePx) {
        return ImageSize(width, height)
    }

    return if (width >= height) {
        ImageSize(
            width = maxEdgePx,
            height = max(1, (height * maxEdgePx.toDouble() / width).roundToInt()),
        )
    } else {
        ImageSize(
            width = max(1, (width * maxEdgePx.toDouble() / height).roundToInt()),
            height = maxEdgePx,
        )
    }
}

internal fun calculateOrientedSize(
    source: ImageSize,
    rotationDegrees: Int,
): ImageSize {
    require(rotationDegrees in setOf(0, 90, 180, 270)) {
        "Rotation must be 0, 90, 180, or 270 degrees"
    }
    return if (rotationDegrees == 90 || rotationDegrees == 270) {
        ImageSize(source.height, source.width)
    } else {
        source
    }
}
