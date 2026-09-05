package io.github.liuzijiancs.capture2doc.ui.document

import android.content.ClipData
import android.content.ClipboardManager
import android.content.ClipDescription
import android.content.Context
import android.os.Parcel
import io.github.liuzijiancs.capture2doc.core.document.C2dDocument
import io.github.liuzijiancs.capture2doc.core.document.C2dRenderer

internal enum class CopyMode(val label: String) { TEXT("纯文本复制"), SIYUAN("复制到思源"), FEISHU("复制到飞书") }
internal object DocumentClipboard {
    const val MAX_BYTES = 512 * 1024
    fun clip(document: C2dDocument, mode: CopyMode): ClipData {
        val plain = C2dRenderer.plain(document)
        return if (mode == CopyMode.TEXT) ClipData.newPlainText("Capture2Doc", plain)
        else {
            val html = ClipData.newHtmlText("Capture2Doc", plain, C2dRenderer.html(document))
            // newHtmlText carries a fallback item but some Android versions advertise only HTML.
            ClipData("Capture2Doc", arrayOf(ClipDescription.MIMETYPE_TEXT_PLAIN, ClipDescription.MIMETYPE_TEXT_HTML), html.getItemAt(0))
        }
    }
    fun serializedBytes(clip: ClipData): Int {
        val parcel = Parcel.obtain()
        return try { clip.writeToParcel(parcel, 0); parcel.dataSize() } finally { parcel.recycle() }
    }
    fun write(context: Context, clip: ClipData) {
        require(serializedBytes(clip) <= MAX_BYTES) { "内容过长，超过剪贴板安全大小；未复制或截断内容" }
        context.getSystemService(ClipboardManager::class.java).setPrimaryClip(clip)
    }
}
