# Capture2Doc Android

原生 Kotlin / Compose / CameraX 客户端：本地拍摄与页序整理 → 后台上传 → 流式文档预览 → 最终 C2D 校验 → 纯文本或富文本复制。

## 使用

1. 首页进入“连接设置”，填写服务地址和设备令牌。“测试连接”仅调用鉴权后的 `GET /openapi.json`，不会创建文档。
2. 拍摄后点击“完成”。应用先保存最终页序，再打开文档阅读页；图片派生、补传和最终页序提交继续执行。
3. 返回首页不会取消任务。重新进入阅读页先显示缓存，再续传事件；离开前台后停止 SSE，由 WorkManager 继续同步。
4. 完整结果通过原始 UTF-8 SHA-256 和 C2D XML 校验后，右上角 `⋯ → 复制` 提供纯文本、思源、飞书三个入口。未完成、空结果或校验失败不能复制。

未配置或离线仍可拍摄。默认地址按“已保存配置 → 构建配置”选择；设置页无默认值时预填 `https://c2d.liu.zj.cn`。令牌不进入构建常量，只在应用私有目录中通过 Android Keystore 加密保存。已绑定任务不随默认地址迁移。

## 实现与验收

- [流式解析、阅读与复制实现记录](docs/android_streaming_document.md)
- [本轮验证记录与待验收项](docs/android_streaming_validation.md)
- [服务端接口规范](../docs/server_service.md)（本轮只读参考；以主分支当前版本为准）

## 构建

保持现有 Kotlin 2.1.21、Compose BOM 2024.09.00、AGP 8.13.1、Gradle 8.13 工具链。新增 OkHttp / okhttp-sse 4.12.0、WebKit 1.12.1；KaTeX 0.18.5 的 JS、CSS、字体与许可证随 APK 打包，无 CDN 运行时依赖。

```sh
cd android
./gradlew :app:testDebugUnitTest :app:assembleDebug :app:lintDebug
./gradlew :app:connectedDebugAndroidTest
```

使用 JDK 17 或兼容的 Android Studio JBR。真机测试需要设备解锁、保持空闲，不切换前台应用。构建产物位于 `app/build/outputs/apk/debug/app-debug.apk`。

本轮不提供文件导出，不生成飞书或思源私有剪贴板记录，不改动服务端接口或模型流程。
