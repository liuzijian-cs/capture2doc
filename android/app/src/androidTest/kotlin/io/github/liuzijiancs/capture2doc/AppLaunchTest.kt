package io.github.liuzijiancs.capture2doc

import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import io.github.liuzijiancs.capture2doc.feature.capture2doc.camera.CameraProbeTags
import io.github.liuzijiancs.capture2doc.ui.home.HomeScreenTags
import org.junit.Assume.assumeTrue
import org.junit.Rule
import org.junit.Test

class AppLaunchTest {
    @get:Rule val composeRule = createAndroidComposeRule<MainActivity>()

    @Test fun homeShowsTaskSearchAndNewAction() {
        composeRule.onNodeWithTag(HomeScreenTags.SEARCH).assertIsDisplayed()
        composeRule.onNodeWithTag(HomeScreenTags.START_SCAN_BUTTON).assertIsDisplayed()
    }

    @Test fun noConfiguredServerEntersCameraWithPersistentWarningAndNoFakeId() {
        assumeTrue(BuildConfig.SERVICE_BASE_URL.isBlank())
        composeRule.waitUntil(10_000) {
            composeRule.onAllNodesWithTag("home_busy").fetchSemanticsNodes().isEmpty()
        }
        composeRule.onNodeWithTag(HomeScreenTags.START_SCAN_BUTTON).performClick()
        composeRule.waitUntil(5_000) {
            composeRule.onAllNodesWithTag(CameraProbeTags.SCREEN).fetchSemanticsNodes().isNotEmpty()
        }
        composeRule.onNodeWithTag(CameraProbeTags.SCREEN).assertIsDisplayed()
        composeRule.onNodeWithTag(CameraProbeTags.CONNECTION_WARNING).assertIsDisplayed()
        composeRule.runOnIdle {
            val app = composeRule.activity.application as Capture2DocApplication
            org.junit.Assert.assertTrue(app.taskRepository.tasks.value.any { it.isVisible && it.documentId == null })
        }
    }
}
