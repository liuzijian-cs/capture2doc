# CameraX 相机探针文档入口

相机探针文档已经按职责拆分为：

- [Android 相机架构与状态管理](android_camera_architecture.md)：组件职责、状态机、并发、文件和生命周期边界；
- [Android 扫描草稿与恢复说明](android_scan_draft.md)：单草稿、页面状态、文件结构、排序和恢复规则；
- [Android 相机拍摄与图像处理](android_camera_capture.md)：CameraX 参数、4:3 取景、对焦、方向、低延迟和 1280 像素选型；
- [Android 相机测试与验收](android_camera_testing.md)：自动化测试、通用真机清单、性能指标和 OCR 输入对照。

保留本文件仅用于兼容旧链接；“相机探针”是既有技术基线名称，不是新的产品页面标题。2026-09-04 确定的稳定 viewport、overlay 候选/控件、候选大图、双击删除、长按排序和进入本地整理页等改造已经实现。最终本地构建检查已通过；当前没有连接设备，新 UI 真机验收待执行。实现与验证状态统一维护在上述 Android 文档中。
