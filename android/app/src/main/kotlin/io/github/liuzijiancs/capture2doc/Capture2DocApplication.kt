package io.github.liuzijiancs.capture2doc

import android.app.Application
import io.github.liuzijiancs.capture2doc.data.capture2doc.draft.ScanDraftRepository

class Capture2DocApplication : Application() {
    internal val scanDraftRepository: ScanDraftRepository by lazy {
        ScanDraftRepository(filesDir)
    }
}
