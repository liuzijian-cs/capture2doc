package io.github.liuzijiancs.capture2doc.feature.capture2doc.draft

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import io.github.liuzijiancs.capture2doc.Capture2DocApplication
import io.github.liuzijiancs.capture2doc.core.model.ScanDraft
import io.github.liuzijiancs.capture2doc.core.model.ScanPageFiles
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import io.github.liuzijiancs.capture2doc.data.capture2doc.draft.ScanDraftRepository
import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

internal data class ScanDraftUiState(
    val isLoading: Boolean = true,
    val draft: ScanDraft? = null,
    val previewPageId: String? = null,
    val armedDeletePageId: String? = null,
    val errorMessage: String? = null,
    val isMutating: Boolean = false,
)

internal class ScanDraftViewModel(
    application: Application,
    private val savedStateHandle: SavedStateHandle,
) : AndroidViewModel(application) {
    private val repository = getApplication<Capture2DocApplication>().scanDraftRepository
    private val _uiState = MutableStateFlow(
        ScanDraftUiState(
            previewPageId = savedStateHandle[PREVIEW_PAGE_ID_KEY],
        ),
    )
    private var repositoryInitialized = false

    val uiState = _uiState.asStateFlow()

    init {
        viewModelScope.launch {
            repository.initialize()
            repositoryInitialized = true
            publishDraft(repository.draft.value)
        }
        viewModelScope.launch {
            repository.draft.collect { draft ->
                publishDraft(draft)
            }
        }
        viewModelScope.launch {
            repository.errorMessage.collect { error ->
                if (error == null) return@collect
                _uiState.update { current -> current.copy(errorMessage = error) }
            }
        }
    }

    fun refreshDraft() {
        viewModelScope.launch {
            repository.initialize()
            repositoryInitialized = true
            publishDraft(repository.draft.value)
        }
    }

    fun createNewDraft() {
        viewModelScope.launch {
            repository.createNewDraft()
        }
    }

    fun ensureDraft() {
        viewModelScope.launch {
            repository.ensureDraft()
        }
    }

    fun discardDraft(onDiscarded: () -> Unit = {}) {
        if (!beginMutation()) return
        viewModelScope.launch {
            try {
                runCatching { repository.discardDraft() }
                    .onSuccess { onDiscarded() }
                    .onFailure(::publishMutationError)
            } finally {
                endMutation()
            }
        }
    }

    fun closeDialogIfNeeded() {
        _uiState.update { it.copy(armedDeletePageId = null) }
    }

    fun openPreview(pageId: String) {
        if (_uiState.value.isMutating) return
        savedStateHandle[PREVIEW_PAGE_ID_KEY] = pageId
        _uiState.update { current ->
            current.copy(previewPageId = pageId, armedDeletePageId = null)
        }
    }

    fun closePreview() {
        savedStateHandle[PREVIEW_PAGE_ID_KEY] = null
        _uiState.update { it.copy(previewPageId = null, armedDeletePageId = null) }
    }

    fun toggleDelete(pageId: String) {
        if (_uiState.value.isMutating) return
        _uiState.update { current ->
            current.copy(
                armedDeletePageId = if (current.armedDeletePageId == pageId) null else pageId,
            )
        }
    }

    fun cancelDelete() {
        _uiState.update { it.copy(armedDeletePageId = null) }
    }

    fun deletePage(pageId: String) {
        if (!beginMutation()) return
        _uiState.update { it.copy(armedDeletePageId = null) }
        viewModelScope.launch {
            try {
                val deleted = runCatching { repository.deletePage(pageId) }
                    .onFailure(::publishMutationError)
                    .getOrDefault(false)
                if (deleted && _uiState.value.previewPageId == pageId) {
                    closePreview()
                } else if (!deleted) {
                    publishMutationError(IllegalStateException("无法删除该页面，请重试"))
                }
            } finally {
                endMutation()
            }
        }
    }

    fun reorder(pageId: String, targetIndex: Int) {
        if (!beginMutation()) return
        viewModelScope.launch {
            try {
                val moved = runCatching { repository.movePage(pageId, targetIndex) }
                    .onFailure(::publishMutationError)
                    .getOrDefault(false)
                if (!moved) {
                    publishMutationError(IllegalStateException("无法保存页面顺序，请重试"))
                }
            } finally {
                endMutation()
            }
        }
    }

    fun pageFiles(pageId: String): ScanPageFiles {
        return repository.pageFiles(pageId)
    }

    /** Returns the best image already on disk without waiting for normalization to finish. */
    fun previewImagePath(pageId: String): String? {
        val page = _uiState.value.draft?.pages?.firstOrNull { it.pageId == pageId }
            ?: return null
        val files = repository.pageFiles(pageId)
        val candidates = if (page.state == ScanPageState.READY) {
            listOf(files.normalizedPath, files.originalPath)
        } else {
            listOf(files.originalPath, files.normalizedPath)
        }
        return candidates.firstOrNull { path ->
            File(path).let { it.isFile && it.length() > 0L }
        }
    }

    fun clearErrorMessage() {
        _uiState.update { it.copy(errorMessage = null) }
    }

    private fun beginMutation(): Boolean {
        if (_uiState.value.isMutating) return false
        _uiState.update {
            it.copy(
                isMutating = true,
                armedDeletePageId = null,
                errorMessage = null,
            )
        }
        return true
    }

    private fun endMutation() {
        _uiState.update { it.copy(isMutating = false) }
    }

    private fun publishMutationError(error: Throwable) {
        _uiState.update {
            it.copy(errorMessage = error.message ?: "本地草稿操作失败，请重试")
        }
    }

    private fun publishDraft(draft: ScanDraft?) {
        val current = _uiState.value
        val previewPageId = if (repositoryInitialized) {
            current.previewPageId?.takeIf { selectedId ->
                draft?.pages?.any { it.pageId == selectedId } == true
            }
        } else {
            current.previewPageId
        }
        if (previewPageId != current.previewPageId) {
            savedStateHandle[PREVIEW_PAGE_ID_KEY] = null
        }
        _uiState.update {
            it.copy(
                isLoading = if (repositoryInitialized) false else it.isLoading,
                draft = draft,
                previewPageId = previewPageId,
                armedDeletePageId = it.armedDeletePageId?.takeIf { selectedId ->
                    draft?.pages?.any { page -> page.pageId == selectedId } == true
                },
            )
        }
    }

    private companion object {
        const val PREVIEW_PAGE_ID_KEY = "scan_draft_preview_page_id"
    }
}
