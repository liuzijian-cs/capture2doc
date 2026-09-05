package io.github.liuzijiancs.capture2doc.ui.theme

import android.app.Activity
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.SideEffect
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.core.view.WindowCompat

private val LightColorScheme = lightColorScheme(
    primary = PaletteDeepBlue,
    onPrimary = PaletteNearBlack,
    primaryContainer = PaletteLightBlue,
    onPrimaryContainer = PaletteNearBlack,
    secondary = PaletteBlueGraySecondary,
    onSecondary = PaletteNearBlack,
    secondaryContainer = PaletteBlueGraySecondaryContainer,
    onSecondaryContainer = PaletteNearBlack,
    tertiary = PaletteBlueGrayTertiary,
    onTertiary = PaletteNearBlack,
    tertiaryContainer = PaletteBlueGrayTertiaryContainer,
    onTertiaryContainer = PaletteNearBlack,
    background = PaletteBackgroundLight,
    onBackground = PaletteOnSurfaceLight,
    surface = PaletteSurfaceLight,
    onSurface = PaletteOnSurfaceLight,
    surfaceVariant = PaletteSurfaceLevel4,
    onSurfaceVariant = PaletteOnSurfaceVariantLight,
    inverseSurface = PaletteSurfaceDark,
    inverseOnSurface = PaletteOnSurfaceDark,
    inversePrimary = PaletteLightBlue,
    error = PaletteDeepRed,
    onError = PaletteNearBlack,
    errorContainer = PaletteLightRed,
    onErrorContainer = PaletteNearBlack,
    outline = PaletteOnSurfaceVariantLight,
    outlineVariant = PaletteSurfaceLevel5,
    scrim = Color.Black,
    surfaceTint = PaletteDeepBlue,
    surfaceBright = PaletteSurfaceLight,
    surfaceDim = PaletteSurfaceLevel4,
    surfaceContainer = PaletteSurfaceLevel2,
    surfaceContainerHigh = PaletteSurfaceLevel3,
    surfaceContainerHighest = PaletteSurfaceLevel4,
    surfaceContainerLow = PaletteSurfaceLevel1,
    surfaceContainerLowest = PaletteSurfaceLight,
)

private val DarkColorScheme = darkColorScheme(
    primary = PaletteLightBlue,
    onPrimary = PaletteNearBlack,
    primaryContainer = PaletteDeepBlue,
    onPrimaryContainer = PaletteNearBlack,
    secondary = PaletteBlueGraySecondary,
    onSecondary = PaletteNearBlack,
    secondaryContainer = PaletteBlueGraySecondaryContainer,
    onSecondaryContainer = PaletteNearBlack,
    tertiary = PaletteBlueGrayTertiary,
    onTertiary = PaletteNearBlack,
    tertiaryContainer = PaletteBlueGrayTertiaryContainer,
    onTertiaryContainer = PaletteNearBlack,
    background = PaletteBackgroundDark,
    onBackground = PaletteOnSurfaceDark,
    surface = PaletteSurfaceDark,
    onSurface = PaletteOnSurfaceDark,
    surfaceVariant = PaletteSurfaceLevelDark5,
    onSurfaceVariant = PaletteOnSurfaceVariantDark,
    inverseSurface = PaletteSurfaceLight,
    inverseOnSurface = PaletteOnSurfaceLight,
    inversePrimary = PaletteDeepBlue,
    error = PaletteLightRed,
    onError = PaletteNearBlack,
    errorContainer = PaletteDeepRed,
    onErrorContainer = PaletteNearBlack,
    outline = PaletteOnSurfaceVariantDark,
    outlineVariant = PaletteSurfaceLevelDark3,
    scrim = Color.Black,
    surfaceTint = PaletteLightBlue,
    surfaceBright = PaletteSurfaceLevelDark5,
    surfaceDim = PaletteSurfaceLevelDark1,
    surfaceContainer = PaletteSurfaceLevelDark3,
    surfaceContainerHigh = PaletteSurfaceLevelDark4,
    surfaceContainerHighest = PaletteSurfaceLevelDark5,
    surfaceContainerLow = PaletteSurfaceLevelDark2,
    surfaceContainerLowest = PaletteSurfaceLevelDark1,
)

@Immutable
class Capture2DocStatusColors(
    val success: Color,
    val successContainer: Color,
    val warning: Color,
    val warningContainer: Color,
    val error: Color,
    val errorContainer: Color,
    val onSuccess: Color,
    val onWarning: Color,
    val onError: Color,
)

private val LightStatusColors = Capture2DocStatusColors(
    success = PaletteDeepGreen,
    successContainer = PaletteLightGreen,
    warning = PaletteDeepYellow,
    warningContainer = PaletteLightYellow,
    error = PaletteDeepRed,
    errorContainer = PaletteLightRed,
    onSuccess = PaletteNearBlack,
    onWarning = PaletteNearBlack,
    onError = PaletteNearBlack,
)

private val DarkStatusColors = Capture2DocStatusColors(
    success = PaletteLightGreen,
    successContainer = PaletteDeepGreen,
    warning = PaletteLightYellow,
    warningContainer = PaletteDeepYellow,
    error = PaletteLightRed,
    errorContainer = PaletteDeepRed,
    onSuccess = PaletteNearBlack,
    onWarning = PaletteNearBlack,
    onError = PaletteNearBlack,
)

private val LocalStatusColors = staticCompositionLocalOf { LightStatusColors }

val MaterialTheme.statusColors: Capture2DocStatusColors
    @Composable
    get() = LocalStatusColors.current

@Composable
fun Capture2DocTheme(
    darkTheme: Boolean = false,
    content: @Composable () -> Unit,
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme
    val statusColors = if (darkTheme) DarkStatusColors else LightStatusColors

    val view = LocalView.current
    if (!view.isInEditMode) {
        val window = (view.context as Activity).window
        SideEffect {
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
            WindowCompat.getInsetsController(window, view).isAppearanceLightNavigationBars = !darkTheme
        }
    }

    CompositionLocalProvider(LocalStatusColors provides statusColors) {
        MaterialTheme(colorScheme = colorScheme, content = content)
    }
}
