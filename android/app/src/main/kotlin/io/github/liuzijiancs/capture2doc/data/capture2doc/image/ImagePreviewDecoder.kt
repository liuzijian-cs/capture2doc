package io.github.liuzijiancs.capture2doc.data.capture2doc.image

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import androidx.exifinterface.media.ExifInterface
import java.io.File
import kotlin.math.max

/** Decodes a bounded, display-oriented bitmap without loading a full camera original into memory. */
internal fun decodeSampledOrientedBitmap(file: File, maxEdgePx: Int): Bitmap? {
    if (!file.isFile || file.length() <= 0L) return null
    val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
    BitmapFactory.decodeFile(file.path, bounds)
    if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

    var sampleSize = 1
    val sourceMaxEdge = max(bounds.outWidth, bounds.outHeight)
    while (sourceMaxEdge / (sampleSize * 2) >= maxEdgePx) sampleSize *= 2
    val decoded = BitmapFactory.decodeFile(
        file.path,
        BitmapFactory.Options().apply {
            inSampleSize = sampleSize
            inPreferredConfig = Bitmap.Config.ARGB_8888
        },
    ) ?: return null

    val exif = runCatching { ExifInterface(file) }.getOrNull() ?: return decoded
    val rotation = exif.rotationDegrees
    val flipped = exif.isFlipped
    if (rotation == 0 && !flipped) return decoded
    val matrix = Matrix().apply {
        if (flipped) postScale(-1f, 1f)
        if (rotation != 0) postRotate(rotation.toFloat())
    }
    return runCatching {
        Bitmap.createBitmap(
            decoded,
            0,
            0,
            decoded.width,
            decoded.height,
            matrix,
            true,
        ).also { transformed ->
            if (transformed !== decoded && !decoded.isRecycled) decoded.recycle()
        }
    }.getOrElse { decoded }
}
