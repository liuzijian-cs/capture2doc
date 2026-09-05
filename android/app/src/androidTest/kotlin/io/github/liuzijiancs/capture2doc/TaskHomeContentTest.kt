package io.github.liuzijiancs.capture2doc

import androidx.compose.runtime.*
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import io.github.liuzijiancs.capture2doc.ui.home.*
import io.github.liuzijiancs.capture2doc.ui.theme.Capture2DocTheme
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

class TaskHomeContentTest {
    @get:Rule val composeRule = createComposeRule()

    @Test fun disconnectedHomeStillAllowsNewCapture() {
        var starts = 0
        composeRule.setContent { Capture2DocTheme {
            HomeScreen(emptyList(), { starts++ }, {}, {}, disconnected = true)
        } }
        composeRule.onNodeWithTag("home_connection_warning").assertIsDisplayed()
        composeRule.onNodeWithTag(HomeScreenTags.START_SCAN_BUTTON).performClick()
        composeRule.runOnIdle { assertEquals(1, starts) }
    }

    @Test fun searchFiltersTitlesAndClearRestoresRows() {
        composeRule.setContent { Capture2DocTheme { HomeScreen(previewDocumentTasks(), {}, {}, {}) } }
        composeRule.onNodeWithTag(HomeScreenTags.SEARCH).performTextInput("多模态")
        composeRule.onNodeWithTag(HomeScreenTags.task("one")).assertIsDisplayed()
        composeRule.onNodeWithTag(HomeScreenTags.task("two")).assertDoesNotExist()
        composeRule.onNodeWithText("清除").performClick()
        composeRule.onNodeWithTag(HomeScreenTags.task("two")).assertIsDisplayed()
    }

    @Test fun swipeOnlyRevealsDeleteAndCancelPreservesTask() {
        var hidden = emptySet<String>()
        composeRule.setContent { Capture2DocTheme { HomeScreen(previewDocumentTasks(), {}, {}, { hidden = it }) } }
        composeRule.onNodeWithTag(HomeScreenTags.task("one")).performTouchInput { swipeLeft() }
        composeRule.runOnIdle { assertTrue(hidden.isEmpty()) }
        composeRule.onNodeWithText("删除").performClick()
        composeRule.onNodeWithText("仅从本机列表移除，后台上传和处理仍会继续。").assertIsDisplayed()
        composeRule.onNodeWithText("取消").performClick()
        composeRule.runOnIdle { assertTrue(hidden.isEmpty()) }
    }

    @Test fun multiSelectionRequiresConfirmationAndHidesOnlySelected() {
        var hidden = emptySet<String>()
        composeRule.setContent {
            var tasks by remember { mutableStateOf(previewDocumentTasks()) }
            Capture2DocTheme { HomeScreen(tasks, {}, {}, { ids ->
                hidden = ids; tasks = tasks.map { if (it.taskId in ids) it.copy(hidden = true) else it }
            }) }
        }
        composeRule.onNodeWithTag(HomeScreenTags.task("one")).performTouchInput { longClick() }
        composeRule.onNodeWithTag(HomeScreenTags.task("two")).performClick()
        composeRule.onNodeWithText("已选 2 项").assertIsDisplayed()
        composeRule.onNodeWithText("删除").performClick()
        composeRule.runOnIdle { assertTrue(hidden.isEmpty()) }
        composeRule.onNodeWithTag(HomeScreenTags.DELETE_CONFIRM).performClick()
        composeRule.runOnIdle { assertEquals(setOf("one", "two"), hidden) }
        composeRule.onNodeWithTag(HomeScreenTags.task("one")).assertDoesNotExist()
        composeRule.onNodeWithTag(HomeScreenTags.task("three")).assertIsDisplayed()
    }

    @Test fun normalClickOpensTaskButDoesNotEditIt() {
        var opened: String? = null
        composeRule.setContent { Capture2DocTheme { HomeScreen(previewDocumentTasks(), {}, { opened = it }, {}) } }
        composeRule.onNodeWithTag(HomeScreenTags.task("three")).performClick()
        composeRule.runOnIdle { assertEquals("three", opened) }
    }
}
