package io.github.liuzijiancs.capture2doc

import android.view.View
import android.view.ViewGroup
import android.webkit.WebView
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.test.platform.app.InstrumentationRegistry
import io.github.liuzijiancs.capture2doc.core.document.C2dParser
import io.github.liuzijiancs.capture2doc.data.document.DocumentContent
import io.github.liuzijiancs.capture2doc.data.document.PreviewBlock
import io.github.liuzijiancs.capture2doc.data.document.PreviewState
import io.github.liuzijiancs.capture2doc.ui.document.DocumentWebView
import io.github.liuzijiancs.capture2doc.ui.document.readerPayload
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class DocumentWebViewTest {
    @get:Rule val compose = createComposeRule()
    private lateinit var root: View
    private fun web(view: View): WebView? = if (view is WebView) view else if (view is ViewGroup) (0 until view.childCount).firstNotNullOfOrNull { web(view.getChildAt(it)) } else null
    private fun js(script: String): String {
        var value = ""
        val latch = CountDownLatch(1)
        InstrumentationRegistry.getInstrumentation().runOnMainSync {
            web(root.rootView)!!.evaluateJavascript(script) { value = it; latch.countDown() }
        }
        check(latch.await(5, TimeUnit.SECONDS)) { "WebView did not respond" }
        return value
    }
    @Test fun localKatexAllTagsAndSafeFallbackRenderWithoutNetwork() {
        val fixture = InstrumentationRegistry.getInstrumentation().context.assets.open("all-tags.xml").bufferedReader().use { it.readText() }
        val payload = readerPayload(DocumentContent(document = C2dParser.document(fixture)), null, false, false)
        compose.setContent { root = LocalView.current; DocumentWebView(payload, true, Modifier.fillMaxSize()) }
        compose.waitUntil(15_000) { js("document.querySelectorAll('.katex').length >= 2") == "true" }
        assertEquals("0", js("scrollY"))
        assertEquals("true", js("document.body.classList.contains('ready')"))
        assertEquals("true", js("document.querySelector('.fallback').textContent.includes('unsupportedMacro')"))
        assertEquals("true", js("document.querySelector('td').rowSpan === 2"))
        assertEquals("true", js("document.querySelector('.wide').scrollWidth >= document.querySelector('.wide').clientWidth"))
        assertEquals("\"rgb(216, 57, 49)\"", js("getComputedStyle(document.querySelector('span[style*=D83931]')).color"))
        assertEquals("true", js("document.querySelectorAll('script[src]').length === 2 && [...document.querySelectorAll('script[src]')].every(s => s.src.startsWith('https://appassets.androidplatform.net/assets/'))"))
    }
    @Test fun updatesPreserveExistingDomAndReadingPositionThenFollowLatest() {
        val blocks = (0..60).map { PreviewBlock("b$it", 1, "<p>第 $it 段，供滚动与增量更新验证。</p>") }
        fun payload(items: List<PreviewBlock>, motion: Boolean = false) = readerPayload(DocumentContent(preview = PreviewState("doc", items)), null, true, motion)
        val state = mutableStateOf(payload(blocks))
        compose.setContent { root = LocalView.current; DocumentWebView(state.value, true, Modifier.fillMaxSize()) }
        compose.waitUntil(15_000) { js("document.querySelectorAll('.block').length === 61") == "true" }
        js("window.kept = document.getElementById('block-b0'); scrollTo(0, 240); dispatchEvent(new Event('scroll'))")
        val before = js("scrollY").toInt()
        compose.runOnIdle { state.value = payload(blocks + PreviewBlock("new", 1, "<p>新内容</p>")) }
        compose.waitUntil(5_000) { js("document.getElementById('block-new') !== null") == "true" }
        assertEquals("true", js("window.kept === document.getElementById('block-b0')"))
        assertTrue(kotlin.math.abs(before - js("scrollY").toInt()) <= 2)
        assertEquals("false", js("document.getElementById('latest').hidden"))
        assertEquals("false", js("document.body.classList.contains('ready')"))
        assertEquals("\"none\"", js("getComputedStyle(document.querySelector('.skeleton')).animationName"))
        js("document.getElementById('latest').click()")
        assertEquals("true", js("document.documentElement.scrollHeight - innerHeight - scrollY < 90"))
    }
    @Test fun backgroundStopsWaitingAnimation() {
        val active = mutableStateOf(true)
        val payload = readerPayload(DocumentContent(), null, true, true)
        compose.setContent { root = LocalView.current; DocumentWebView(payload, active.value, Modifier.fillMaxSize()) }
        compose.waitUntil(15_000) { js("document.body.classList.contains('waiting')") == "true" }
        compose.runOnIdle { active.value = false }
        compose.waitUntil(5_000) { js("document.body.classList.contains('waiting')") == "false" }
        assertEquals("false", js("document.body.classList.contains('motion')"))
    }
}
