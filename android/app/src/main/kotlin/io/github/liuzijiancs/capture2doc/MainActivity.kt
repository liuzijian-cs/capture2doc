package io.github.liuzijiancs.capture2doc

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import io.github.liuzijiancs.capture2doc.ui.theme.Capture2DocTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            Capture2DocTheme {
                Capture2DocApp()
            }
        }
    }
}
