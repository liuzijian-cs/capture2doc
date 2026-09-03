package io.github.liuzijiancs.capture2doc.data.capture2doc.image

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ImageSizeCalculatorTest {
    @Test
    fun landscapeImageScalesToMaxEdge() {
        assertEquals(
            ImageSize(1280, 960),
            calculateTargetSize(width = 4032, height = 3024, maxEdgePx = 1280),
        )
    }

    @Test
    fun portraitImageScalesToMaxEdge() {
        assertEquals(
            ImageSize(960, 1280),
            calculateTargetSize(width = 3024, height = 4032, maxEdgePx = 1280),
        )
    }

    @Test
    fun smallerImageIsNotUpscaled() {
        assertEquals(
            ImageSize(800, 600),
            calculateTargetSize(width = 800, height = 600, maxEdgePx = 1280),
        )
    }

    @Test
    fun oddDimensionsKeepAspectRatioWithinOnePixel() {
        val sourceWidth = 4033
        val sourceHeight = 3017
        val result = calculateTargetSize(sourceWidth, sourceHeight, maxEdgePx = 1280)
        val idealHeight = sourceHeight * 1280.0 / sourceWidth

        assertEquals(1280, result.width)
        assertTrue(kotlin.math.abs(result.height - idealHeight) <= 0.5)
    }

    @Test
    fun exifQuarterTurnsSwapDimensions() {
        val source = ImageSize(4032, 3024)

        assertEquals(source, calculateOrientedSize(source, rotationDegrees = 0))
        assertEquals(ImageSize(3024, 4032), calculateOrientedSize(source, 90))
        assertEquals(source, calculateOrientedSize(source, rotationDegrees = 180))
        assertEquals(ImageSize(3024, 4032), calculateOrientedSize(source, 270))
    }
}
