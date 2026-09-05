package io.github.liuzijiancs.capture2doc

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithTag
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeTags
import org.junit.Rule
import org.junit.Test

class AppLaunchTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun homeScreenShowsReadyStateAndStartAction() {
        composeRule.onNodeWithText("把手机拍摄变成结构化文档").assertIsDisplayed()
        composeRule.onNodeWithText("App 已就绪").assertIsDisplayed()
        composeRule.onNodeWithText("开始拍照扫描")
            .assertIsDisplayed()
            .performClick()
        composeRule.waitForIdle()
        if (
            composeRule.onAllNodesWithText("放弃并新建")
                .fetchSemanticsNodes().isNotEmpty()
        ) {
            composeRule.onNodeWithText("放弃并新建").performClick()
        }
        composeRule.waitUntil(timeoutMillis = 5_000) {
            composeRule.onAllNodesWithTag(CameraProbeTags.SCREEN)
                .fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithTag(CameraProbeTags.SCREEN).assertIsDisplayed()
    }
}
