package io.github.liuzijiancs.capture2doc

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.exifinterface.media.ExifInterface
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.github.liuzijiancs.capture2doc.data.capture2doc.image.ImageNormalizer
import io.github.liuzijiancs.capture2doc.data.capture2doc.image.ImageSize
import java.io.File
import java.io.FileOutputStream
import java.util.UUID
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ImageNormalizerInstrumentedTest {
    @Test
    fun normalizesExifRotationsIntoPixelDimensions() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val testDirectory = File(
            context.cacheDir,
            "normalizer-test-${UUID.randomUUID()}",
        )
        assertTrue(testDirectory.mkdirs())

        try {
            val cases = listOf(
                ExifInterface.ORIENTATION_NORMAL to ImageSize(80, 40),
                ExifInterface.ORIENTATION_ROTATE_90 to ImageSize(40, 80),
                ExifInterface.ORIENTATION_ROTATE_180 to ImageSize(80, 40),
                ExifInterface.ORIENTATION_ROTATE_270 to ImageSize(40, 80),
            )

            cases.forEachIndexed { index, (orientation, expectedSize) ->
                val source = File(testDirectory, "source-$index.jpg")
                val destination = File(testDirectory, "normalized-$index.jpg")
                createSourceJpeg(source, orientation)

                val result = ImageNormalizer().normalize(
                    source = source,
                    destination = destination,
                    maxEdgePx = 1280,
                )
                val decoded = checkNotNull(BitmapFactory.decodeFile(destination.path))

                try {
                    assertEquals(expectedSize, result.sourceSize)
                    assertEquals(expectedSize, result.outputSize)
                    assertEquals(expectedSize.width, decoded.width)
                    assertEquals(expectedSize.height, decoded.height)
                    assertEquals(0, ExifInterface(destination).rotationDegrees)
                } finally {
                    decoded.recycle()
                }
            }
        } finally {
            testDirectory.deleteRecursively()
        }
    }

    private fun createSourceJpeg(file: File, orientation: Int) {
        val bitmap = Bitmap.createBitmap(80, 40, Bitmap.Config.ARGB_8888)
        try {
            FileOutputStream(file).use { stream ->
                assertTrue(bitmap.compress(Bitmap.CompressFormat.JPEG, 100, stream))
            }
            ExifInterface(file).apply {
                setAttribute(ExifInterface.TAG_ORIENTATION, orientation.toString())
                saveAttributes()
            }
        } finally {
            bitmap.recycle()
        }
    }
}
