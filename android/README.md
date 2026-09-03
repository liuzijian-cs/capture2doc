# Capture2Doc Android

Capture2Doc Android 当前提供一个可启动的 Kotlin + Jetpack Compose 应用骨架，以及一个边界明确的 CameraX 拍照探针。现有首屏和相机页用于验证工程及硬件能力；产品方向仍是对话式宿主 App，拍照/视频转文档作为独立工具接入。

- 产品定位、功能边界和下一里程碑见[Android App 产品与架构草案](../docs/android_app.md)。
- 开发环境、构建命令和三星 S24 Ultra 真机调试见[Android 开发环境](../docs/android_env.md)。
- 相机、图像归一化和真机验收见[CameraX 拍照探针](../docs/camera_probe.md)。

## 当前范围

- 单 Activity Compose 应用
- 浅色、深色及 Android 动态配色
- 从扫描入口进入 CameraX 后置摄像头预览
- 保存原图并生成最长边 1024 像素的等比压缩图
- 权限、相机状态、结果指标和 Compose 冒烟测试

当前仅申请摄像头权限，不申请存储或网络权限，也没有定义尚未冻结的服务端 DTO。相机探针不是最终产品导航或正式文档扫描 UI。
