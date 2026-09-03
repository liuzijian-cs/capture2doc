package io.github.liuzijiancs.capture2doc

import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.activity.compose.BackHandler
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeRoute
import io.github.liuzijiancs.capture2doc.ui.home.HomeScreen

private enum class AppDestination {
    HOME,
    CAMERA_PROBE,
}

@Composable
fun Capture2DocApp() {
    val snackbarHostState = remember { SnackbarHostState() }
    var destinationName by rememberSaveable { mutableStateOf(AppDestination.HOME.name) }
    val destination = AppDestination.valueOf(destinationName)

    BackHandler(enabled = destination == AppDestination.CAMERA_PROBE) {
        destinationName = AppDestination.HOME.name
    }

    when (destination) {
        AppDestination.HOME -> HomeScreen(
            snackbarHostState = snackbarHostState,
            onStartScan = { destinationName = AppDestination.CAMERA_PROBE.name },
        )

        AppDestination.CAMERA_PROBE -> CameraProbeRoute(
            onBack = { destinationName = AppDestination.HOME.name },
        )
    }
}
