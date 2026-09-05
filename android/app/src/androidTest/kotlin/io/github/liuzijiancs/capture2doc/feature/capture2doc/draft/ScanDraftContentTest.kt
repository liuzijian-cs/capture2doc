package io.github.liuzijiancs.capture2doc.feature.capture2doc.draft

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.click
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeLeft
import io.github.liuzijiancs.capture2doc.core.model.ScanDraft
import io.github.liuzijiancs.capture2doc.core.model.ScanPage
import io.github.liuzijiancs.capture2doc.core.model.ScanPageState
import io.github.liuzijiancs.capture2doc.ui.theme.Capture2DocTheme
import org.junit.Rule
import org.junit.Test

class ScanDraftContentTest {
    @get:Rule
    val composeRule = createComposeRule()

    @Test
    fun deleteRequiresTwoClicksOnTheSameControl() {
        setDraftContent(initialPages = listOf(page("page-one")))

        composeRule.onNodeWithContentDescription("删除").performClick()
        composeRule.onNodeWithText("再次点击以删除").assertIsDisplayed()
        composeRule.onNodeWithTag(ScanDraftTags.PAGE_PREFIX + "page-one").assertIsDisplayed()

        composeRule.onNodeWithContentDescription("确认删除").performClick()

        composeRule.onNodeWithTag(ScanDraftTags.EMPTY).assertIsDisplayed()
        composeRule.onNodeWithText("保存并退出").assertIsNotEnabled()
    }

    @Test
    fun tappingThePageCancelsAnArmedDelete() {
        setDraftContent(initialPages = listOf(page("page-one")))

        composeRule.onNodeWithContentDescription("删除").performClick()
        composeRule.onNodeWithTag(ScanDraftTags.PAGE_PREFIX + "page-one")
            .performTouchInput { click() }

        composeRule.onNodeWithText("再次点击以删除").assertDoesNotExist()
        composeRule.onNodeWithContentDescription("删除").assertIsDisplayed()
    }

    @Test
    fun previewSupportsHorizontalPaging() {
        setDraftContent(
            initialPages = listOf(page("page-one"), page("page-two")),
            initialPreviewPageId = "page-one",
        )

        composeRule.onNodeWithTag(ScanDraftTags.PREVIEW_PAGER).assertIsDisplayed()
        composeRule.onNodeWithTag(ScanDraftTags.PREVIEW_PAGER)
            .performTouchInput { swipeLeft() }

        composeRule.waitForIdle()
        composeRule.onNodeWithText("页面 2").assertIsDisplayed()
    }

    @Test
    fun previewBackCancelsDeleteBeforeReturningToGrid() {
        setDraftContent(
            initialPages = listOf(page("page-one")),
            initialPreviewPageId = "page-one",
        )

        composeRule.onNodeWithText("删除").performClick()
        composeRule.onNodeWithText("确认删除").assertIsDisplayed()

        composeRule.onNodeWithText("返回").performClick()
        composeRule.onNodeWithText("确认删除").assertDoesNotExist()
        composeRule.onNodeWithTag(ScanDraftTags.PREVIEW_PAGER).assertIsDisplayed()

        composeRule.onNodeWithText("返回").performClick()
        composeRule.onNodeWithTag(ScanDraftTags.GRID).assertIsDisplayed()
    }

    @Test
    fun capturingPageCannotBeDeletedOrRetakenUntilCameraCallbackFinishes() {
        setDraftContent(
            initialPages = listOf(page("capturing").copy(state = ScanPageState.CAPTURING)),
        )

        composeRule.onNodeWithContentDescription("删除").assertIsNotEnabled()
        composeRule.onNodeWithText("重拍").assertIsNotEnabled()
    }

    @Test
    fun draftMutationDisablesNavigationAndPageActions() {
        setDraftContent(
            initialPages = listOf(page("page-one")),
            isMutating = true,
        )

        composeRule.onNodeWithContentDescription("返回").assertIsNotEnabled()
        composeRule.onNodeWithText("保存并退出").assertIsNotEnabled()
        composeRule.onNodeWithContentDescription("删除").assertIsNotEnabled()
        composeRule.onNodeWithText("重拍").assertIsNotEnabled()
    }

    private fun setDraftContent(
        initialPages: List<ScanPage>,
        initialPreviewPageId: String? = null,
        isMutating: Boolean = false,
    ) {
        composeRule.setContent {
            var pages by remember { mutableStateOf(initialPages) }
            var previewPageId by remember { mutableStateOf(initialPreviewPageId) }
            var armedDeletePageId by remember { mutableStateOf<String?>(null) }

            Capture2DocTheme {
                ScanDraftScreen(
                    uiState = ScanDraftUiState(
                        isLoading = false,
                        draft = ScanDraft(
                            draftId = "test-draft",
                            createdAtEpochMillis = 1L,
                            updatedAtEpochMillis = 1L,
                            pages = pages,
                        ),
                        previewPageId = previewPageId,
                        armedDeletePageId = armedDeletePageId,
                        isMutating = isMutating,
                    ),
                    onNavigateBack = {
                        when {
                            armedDeletePageId != null -> armedDeletePageId = null
                            previewPageId != null -> previewPageId = null
                        }
                    },
                    onSaveAndExit = {},
                    onContinueCapture = {},
                    onOpenPreview = { selectedId ->
                        previewPageId = selectedId
                        armedDeletePageId = null
                    },
                    onClosePreview = {
                        previewPageId = null
                        armedDeletePageId = null
                    },
                    onToggleDelete = { selectedId ->
                        armedDeletePageId = if (armedDeletePageId == selectedId) {
                            null
                        } else {
                            selectedId
                        }
                    },
                    onDelete = { selectedId ->
                        pages = pages.filterNot { it.pageId == selectedId }
                        armedDeletePageId = null
                        if (previewPageId == selectedId) previewPageId = null
                    },
                    onReorder = { selectedId, targetIndex ->
                        val sourceIndex = pages.indexOfFirst { it.pageId == selectedId }
                        if (sourceIndex >= 0 && targetIndex in pages.indices) {
                            pages = pages.toMutableList().apply {
                                add(targetIndex, removeAt(sourceIndex))
                            }
                        }
                    },
                    onRetake = { selectedId ->
                        pages = pages.map { current ->
                            if (current.pageId == selectedId) {
                                current.copy(state = ScanPageState.RETAKE_REQUIRED)
                            } else {
                                current
                            }
                        }
                    },
                    onPreviewPageChanged = { previewPageId = it },
                    imagePathForPage = { null },
                )
            }
        }
    }

    private fun page(pageId: String) = ScanPage(
        pageId = pageId,
        originalRelativePath = "pages/$pageId/original.jpg",
        normalizedRelativePath = "pages/$pageId/normalized_1280.jpg",
        state = ScanPageState.READY,
    )
}
