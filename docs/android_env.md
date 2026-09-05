# Android 开发环境

> 状态：开发环境与既有 CameraX 真机基线已通过；2026-09-04 相机页 UI 已实现且最终本地构建检查通过，新 UI 真机验收待执行
> 最近核对：2026-09-04，macOS / Apple Silicon

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

## 当前开发机环境

已经确认安装：

- Android Studio 2025.2.2：`/Applications/Android Studio.app`；
- Android Studio 内置 JBR 21.0.8；
- Android SDK：`/Users/zane/Library/Android/sdk`；
- Android Platform 36；
- Build Tools 36.1.0 和 37.0.0；
- Platform Tools，包含 `adb`；
- Android Emulator。

Shell 环境已通过 `~/.zshrc` 持久配置：

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME="/Users/zane/Library/Android/sdk"
export PATH="$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
```

当前验证结果：

| 项目 | 结果 |
|---|---|
| `java -version` | OpenJDK 21.0.8 |
| `gradle -v` | Gradle 8.13，Launcher/Daemon 使用 JBR 21.0.8 |
| Gradle Wrapper | 已生成并进入工程目录 |
| `:app:assembleDebug` | 最终修复后通过，已生成 Debug APK |
| `:app:testDebugUnitTest` | 最终修复后通过，包含尺寸计算与完成门槛测试 |
| `:app:lintDebug` | 最终修复后通过，已生成 HTML 报告 |
| `:app:assembleDebugAndroidTest` | 最终修复后通过，已生成设备测试 APK |
| Android 真机 | 改造前的低延迟 Debug APK 已安装并冷启动成功；当前没有连接设备 |
| CameraX 真机验证 | 改造前的功能、连续拍摄、方向、对焦、文件输出与性能验收均通过 |
| `:app:connectedDebugAndroidTest` | 改造前 8 个测试通过；新 UI 未执行，当前没有连接设备 |

当前不影响真机开发的缺项：

- 没有安装 Android command-line tools、system image 或 AVD；
- 暂未建立模拟器测试矩阵；
- CameraX 低延迟连续拍照探针已通过构建、自动化连接测试和完整真机验收。

稳定 viewport、overlay 候选条与底部控件已经完成代码实现，并通过最终修复后的 JVM、Debug、Lint 和设备测试 APK 构建。由于当前没有连接设备，新 UI 的连接测试与真机验收不计入“已通过”，后续按 [Android 相机测试与验收](android_camera_testing.md) 的新增清单验证。

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

```bash
/Users/zane/Library/Android/sdk/platform-tools/adb install -r \
app/build/outputs/apk/debug/app-debug.apk

/Users/zane/Library/Android/sdk/platform-tools/adb shell am start \
-n io.github.liuzijiancs.capture2doc/.MainActivity
```

首轮真机检查至少覆盖：冷启动、浅色/深色模式、横竖屏切换、返回手势和字体缩放。相机流程还需覆盖权限拒绝与重新授权、横竖屏拍照、切入后台、连续拍摄、结果尺寸，以及候选点按大图、双击删除、长按排序和“完成”进入本地整理页，详细清单见 [Android 相机测试与验收](android_camera_testing.md)。

改造前的 4:3 低延迟 CameraX Debug APK 已在 Android 真机上完成冷启动、权限说明、后置摄像头绑定、8 个自动化连接测试，以及真实快门、近远点对焦、横竖屏与反向方向、1280 像素 OCR 派生图核对和 20 张性能验收。实际拍摄分辨率和 postview 支持情况仍由运行时能力决定；2026-09-04 实现的新相机 UI 尚未进行这轮真机验收。

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

终端尚未配置全局工具链。使用本文命令中的 Android Studio 内置 `JAVA_HOME`，并在 Wrapper 生成后始终运行 `./gradlew`。

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
