# Android 开发环境

> 状态：开发环境与既有 CameraX 真机基线已通过；2026-09-04 相机页 UI 已实现且最终本地构建检查通过，新 UI 真机验收待执行
> 流程与构建结果核对：2026-09-05；工具链环境沿用既有 macOS / Apple Silicon 记录

本文档集中记录 Capture2Doc Android 工程的工具链版本、本机环境、首次初始化、构建和真机调试步骤。具体依赖版本仍以 [`android/gradle/libs.versions.toml`](../android/gradle/libs.versions.toml) 和 [`android/app/build.gradle.kts`](../android/app/build.gradle.kts) 为准。

## 工程基线

| 项目 | 当前版本或配置 |
|---|---|
| Android Studio | 2025.2.2 或兼容版本 |
| Gradle | 8.13 |
| Android Gradle Plugin | 8.13.1 |
| Kotlin / Compose Compiler Plugin | 2.1.21 |
| Compose BOM | 2024.09.00 |
| CameraX | 1.6.2 |
| `compileSdk` | 36 |
| `targetSdk` | 36 |
| `minSdk` | 26 |
| Java/Kotlin 字节码目标 | 17 |
| Android Build Tools | 36.1.0 |

工程使用 Kotlin、Jetpack Compose 和 Material 3。版本统一由 Gradle Version Catalog 管理，不在各模块中重复声明。

## 开发机环境记录

以下为既有环境记录，本轮文档整理不重新检测全部安装项：

- Android Studio 2025.2.2：`/Applications/Android Studio.app`；
- Android Studio 内置 JBR 21.0.8；
- Android SDK：`/Users/zane/Library/Android/sdk`；
- Android Platform 36；
- Build Tools 36.1.0 和 37.0.0；
- Platform Tools，包含 `adb`；
- Android Emulator。

既有记录中的 Shell 配置如下；新终端如未生效，按实际环境设置，不能假定其他机器已配置：

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME="/Users/zane/Library/Android/sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
```

验证记录（工具链版本沿用既有记录；四项 Gradle 检查于 2026-09-05 合并后成功，未变化任务可复用结果）：

| 项目 | 结果 |
|---|---|
| `java -version` | OpenJDK 21.0.8 |
| `gradle -v` | Gradle 8.13，Launcher/Daemon 使用 JBR 21.0.8 |
| Gradle Wrapper | 已生成并进入工程目录 |
| `:app:assembleDebug` | 最终修复后通过，已生成 Debug APK |
| `:app:testDebugUnitTest` | 最终修复后通过，包含尺寸计算与完成门槛测试 |
| `:app:lintDebug` | 最终修复后通过，已生成 HTML 报告 |
| `:app:assembleDebugAndroidTest` | 最终修复后通过，已生成设备测试 APK |
| Android 真机 | 改造前的低延迟 Debug APK 已安装并冷启动成功；本轮未执行新 UI 真机验收 |
| CameraX 真机验证 | 改造前的功能、连续拍摄、方向、对焦、文件输出与性能验收均通过 |
| `:app:connectedDebugAndroidTest` | 改造前 8 个测试通过；截至本轮记录，新 UI 未执行 |

既有环境记录中的缺项与限制（不是实时安装状态）：

- 没有安装 Android command-line tools、system image 或 AVD；
- 暂未建立模拟器测试矩阵；
- CameraX 低延迟连续拍照探针已通过构建、自动化连接测试和完整真机验收。

稳定 viewport、overlay 候选条与底部控件已实现，四项本地检查成功。新 UI 连接测试与真机验收尚无本轮执行结果，后续按 [Android 相机测试与验收](android_camera_testing.md) 的新增清单验证。设备是否在线以当次 `adb devices -l` 为准。

项目当前优先使用 Android 真机验证，因此不要求先下载模拟器 system image。

## 首次初始化

### 1. 获取 Gradle

下载并解压 [Gradle 8.13 binary-only distribution](https://services.gradle.org/distributions/gradle-8.13-bin.zip)。不需要手动下载 AGP、Kotlin 或 Compose 的单独 JAR，Gradle 会从 Google Maven、Maven Central 和 Gradle Plugin Portal 自动解析它们。

### 2. 生成 Gradle Wrapper

进入 Android 工程目录，将 `/path/to/gradle-8.13` 替换为实际解压路径：

```bash
cd /Users/zane/workspace/project/capture2doc-android/android

JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
ANDROID_HOME="/Users/zane/Library/Android/sdk" \
/path/to/gradle-8.13/bin/gradle \
wrapper --gradle-version 8.13 --distribution-type bin
```

成功后，工程中应出现：

```text
android/gradlew
android/gradlew.bat
android/gradle/wrapper/gradle-wrapper.jar
android/gradle/wrapper/gradle-wrapper.properties
```

Gradle Wrapper 及其配置应提交到 Git；不要提交本机生成的 `local.properties`、`.gradle/` 或构建目录。

### 3. 首次构建

```bash
cd /Users/zane/workspace/project/capture2doc-android/android

JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home" \
ANDROID_HOME="/Users/zane/Library/Android/sdk" \
./gradlew :app:assembleDebug
```

构建成功时终端会显示 `BUILD SUCCESSFUL`，Debug APK 位于：

```text
android/app/build/outputs/apk/debug/app-debug.apk
```

首次成功后，Gradle 会复用本地依赖缓存。日常构建统一使用工程内的 `./gradlew`，不再直接调用下载的 Gradle 可执行文件。当前开发机已经完成本节初始化；本节主要供重装环境或新增开发机时使用。

## Android Studio 设置

用 Android Studio 打开仓库中的 `android/` 目录（当前 checkout 为 `/Users/zane/workspace/project/capture2doc-android/android`），不要只打开 `app` 子目录。

Gradle JDK 选择 Android Studio 的 Embedded JDK。若 IDE 没有自动识别 SDK，可在本机的 `local.properties` 中设置：

```properties
sdk.dir=/Users/zane/Library/Android/sdk
```

`local.properties` 只保存本机路径，不提交到 Git。

## Android Studio 原型预览

先用 Preview 讨论布局，再决定真机验收项目，不依赖 Gradio 或聊天模型。Compose Preview 支持查看不同尺寸、主题和示例状态，也有交互预览模式；具体能力见 [Android 官方 Preview 说明](https://developer.android.com/develop/ui/compose/tooling/previews)。

1. 打开仓库 `android/` 工程并等待 Gradle Sync 完成。
2. 打开 [CameraProbeScreen.kt](../android/app/src/main/kotlin/io/github/liuzijiancs/capture2doc/feature/capture2doc/camera/CameraProbeScreen.kt)，切换编辑器的 Split 或 Design；需要时点击 Build & Refresh。
3. 对照 `Camera empty / saving / pages / failure / dark / landscape` 示例，检查快门居中、候选位置、空态与多页状态下取景框不跳变。
4. 打开 [ScanDraftScreen.kt](../android/app/src/main/kotlin/io/github/liuzijiancs/capture2doc/feature/capture2doc/draft/ScanDraftScreen.kt)，查看 `01 网格 - 浅色` 至 `06 恢复占位` 六个 Preview，讨论网格、大图、删除确认、空草稿及失败占位。
5. 可用交互模式检查原型支持的点击和拖拽；记录不满意的布局、状态与预期交互，作为下一轮 UI 修改输入。

相机 Preview 使用示例状态和模拟取景背景，不会启动真实 CameraX；整理原型也不代表 Repository 实际写盘。Preview 构建成功不能证明真实快门、草稿恢复或网络链路成功。VS Code 可继续编辑源码，现有 Compose 原型在 Android Studio 中查看。

## 构建与检查命令

以下命令均在 `android/` 目录执行：

```bash
# 构建 Debug APK
./gradlew :app:assembleDebug

# 执行 Android Lint
./gradlew :app:lintDebug

# 执行 JVM 单元测试
./gradlew :app:testDebugUnitTest

# 编译真机测试 APK
./gradlew :app:assembleDebugAndroidTest

# 连接设备后执行 Compose 真机测试
./gradlew :app:connectedDebugAndroidTest
```

如果当前终端仍未配置 JDK 和 SDK，请沿用首次构建示例中的 `JAVA_HOME` 与 `ANDROID_HOME`。

## Android 真机调试

### 手机设置

1. 打开“设置 → 关于手机 → 软件信息”。
2. 连续点击“编译编号”七次，启用开发者选项。
3. 打开“设置 → 开发者选项 → USB 调试”。
4. 使用支持数据传输的 USB 线连接电脑。
5. 手机弹出授权提示时，允许这台电脑进行 USB 调试。

### 检查连接

```bash
/Users/zane/Library/Android/sdk/platform-tools/adb devices -l
```

设备状态应为 `device`，而不是 `unauthorized` 或 `offline`。

### 安装并启动

以下命令在 `android/` 目录执行：

```bash
/Users/zane/Library/Android/sdk/platform-tools/adb install -r \
app/build/outputs/apk/debug/app-debug.apk

/Users/zane/Library/Android/sdk/platform-tools/adb shell am start \
-n io.github.liuzijiancs.capture2doc/.MainActivity
```

首轮真机检查至少覆盖：冷启动、浅色/深色模式、横竖屏切换、返回手势和字体缩放。相机流程还需覆盖权限拒绝与重新授权、横竖屏拍照、切入后台、连续拍摄、结果尺寸，以及候选点按大图、双击删除、长按排序和“完成”进入本地整理页，详细清单见 [Android 相机测试与验收](android_camera_testing.md)。

改造前的 4:3 低延迟 CameraX Debug APK 已在 Android 真机上完成冷启动、权限说明、后置摄像头绑定、8 个自动化连接测试，以及真实快门、近远点对焦、横竖屏与反向方向、1280 像素 OCR 派生图核对和 20 张性能验收。实际拍摄分辨率和 postview 支持情况仍由运行时能力决定；2026-09-04 实现的新相机 UI 尚未进行这轮真机验收。

上述人工基线按用户确认记录通过，本轮没有补录原始性能统计。新 UI 和未来 [联网 Pipeline](capture_pipeline.md) 分阶段验收，不能继承旧基线的通过结论。

### 通过 Android Studio 部署

USB 调试授权完成后，在顶部运行配置选择 `app`，设备列表选择已连接真机，点击主工具栏绿色 Run 按钮。Android Studio 会构建、安装并启动 App；不要将 Preview 自身的 Run Preview 当作完整 App 导航验证入口。手机保持解锁，首次进入扫描工具时按提示授权相机。流程依据见 [Android 官方真机运行说明](https://developer.android.com/studio/run/device)。

若构建失败，先在 Build 输出查看首个编译错误；若安装失败，核对设备授权和连接。首次打开仍是当前临时宿主首页，从扫描入口进入相机；现有草稿会弹出继续/放弃选项。当前点击相机“完成”进入本地整理页，未部署聊天服务或 Gradio 不影响该流程。

### 相机权限复测

可在测试前撤销摄像头权限，验证首次进入与拒绝流程。该命令可能结束正在运行的 App：

```bash
/Users/zane/Library/Android/sdk/platform-tools/adb shell pm revoke \
io.github.liuzijiancs.capture2doc android.permission.CAMERA
```

调试构建可以通过 `run-as` 检查 App 私有扫描草稿清单及页面目录：

```bash
/Users/zane/Library/Android/sdk/platform-tools/adb shell run-as \
io.github.liuzijiancs.capture2doc ls -lh files/scan_draft

/Users/zane/Library/Android/sdk/platform-tools/adb shell run-as \
io.github.liuzijiancs.capture2doc ls -lh files/scan_draft/pages
```

## 常见问题

### `java` 或 `gradle` 找不到

若当前终端未加载工具链配置，使用本文命令中的 Android Studio 内置 `JAVA_HOME`，日常构建始终运行工程内 `./gradlew`，无需依赖全局 `gradle` 命令。

### 找不到 Android SDK

确认 `ANDROID_HOME=/Users/zane/Library/Android/sdk`，或者在未提交的 `local.properties` 中设置 `sdk.dir`。

### Google Maven TLS 或依赖下载失败

首次初始化曾在访问 Maven Central 时出现 TLS 握手中断，直接重试后依赖下载及 Lint 均成功。这不属于应用源码错误。再次出现时，先重试原命令；持续失败再确认以下地址可以从当前网络访问：

- `https://dl.google.com/dl/android/maven2/master-index.xml`
- `https://repo.maven.apache.org/maven2/`
- `https://plugins.gradle.org/`

如使用代理，在 Android Studio 的 HTTP Proxy 设置中选择系统代理或填入有效代理，然后重新执行构建。只有缓存疑似不完整且普通重试无效时，才运行：

```bash
./gradlew :app:assembleDebug --refresh-dependencies
```

### `adb` 显示 `unauthorized`

解锁手机并确认 USB 调试授权。如果授权提示没有出现，可在开发者选项中撤销 USB 调试授权，重新插拔数据线后再次确认。

## 环境变更约定

- 升级 AGP、Gradle、Kotlin、Compose 或 SDK 时，应同时更新版本目录、构建文件和本文档。
- 工程以 Gradle Wrapper 作为构建入口，避免不同开发机使用不同的系统 Gradle。
- 单台真机测试不能替代 API 26 以上不同厂商、摄像头能力和屏幕尺寸的兼容性验证。
