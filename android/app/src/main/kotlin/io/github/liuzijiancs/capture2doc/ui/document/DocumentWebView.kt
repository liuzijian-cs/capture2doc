package io.github.liuzijiancs.capture2doc.ui.document

import android.annotation.SuppressLint
import android.content.Intent
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.viewinterop.AndroidView
import androidx.webkit.WebViewAssetLoader
import io.github.liuzijiancs.capture2doc.core.document.C2dRenderer
import java.io.ByteArrayInputStream

@SuppressLint("SetJavaScriptEnabled")
@Composable
internal fun DocumentWebView(payload: String, active: Boolean, modifier: Modifier = Modifier) {
    var webView by remember { mutableStateOf<WebView?>(null) }
    val latestPayload by rememberUpdatedState(payload)
    var ready by remember { mutableStateOf(false) }
    AndroidView(modifier = modifier, factory = { context ->
        val assets = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(context)).build()
        WebView(context).apply {
            webView = this
            setBackgroundColor(android.graphics.Color.WHITE)
            settings.apply {
                javaScriptEnabled = true
                allowFileAccess = false
                allowContentAccess = false
                domStorageEnabled = false
                mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
                setSupportMultipleWindows(false)
                javaScriptCanOpenWindowsAutomatically = false
                blockNetworkLoads = true
            }
            // No JavascriptInterface, cookies, file URLs or arbitrary navigations.
            webViewClient = object : WebViewClient() {
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse =
                    assets.shouldInterceptRequest(request.url)
                        ?: WebResourceResponse("text/plain", "UTF-8", 403, "Blocked", emptyMap(), ByteArrayInputStream(ByteArray(0)))
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    if (request.isForMainFrame && request.hasGesture() && C2dRenderer.safeLink(request.url.toString())) {
                        runCatching { context.startActivity(Intent(Intent.ACTION_VIEW, request.url)) }
                    }
                    return true
                }
                override fun onPageFinished(view: WebView, url: String) {
                    if (url == READER_URL) { ready = true; view.evaluateJavascript("window.updateDocument($latestPayload)", null) }
                }
            }
            loadUrl(READER_URL)
        }
    }, update = { view ->
        if (ready) view.evaluateJavascript("window.updateDocument($payload)", null)
        if (active) view.onResume() else {
            view.evaluateJavascript("document.body.classList.remove('motion','waiting')", null)
            view.onPause()
        }
    })
    DisposableEffect(Unit) { onDispose { webView?.apply { stopLoading(); destroy() }; webView = null } }
}

internal const val READER_URL = "https://appassets.androidplatform.net/assets/reader/index.html"
