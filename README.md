# Capture2Doc

Turn mobile document captures into validated, structured documents.

Capture2Doc 是一个处于设计与原型验证阶段的开源项目，目标是由 Android App 从拍照或视频中提取文档页面，将处理后的图片流交给本地推理服务，生成固定、可校验且与原始分页无关的 C2D-XML，再由确定性输出层转换为 Lark 文档、Markdown 或思源笔记 `.sy`。

C2D-XML 只描述最终文档的逻辑内容，不保存采集页码、页面坐标或来源追溯信息。这些信息如处理过程需要，只存在于服务端内部中间结果中。

当前计划采用客户端采集、本地服务端推理的两阶段架构：

- Android App 负责拍照、视频帧筛选、页面检测、矫正、去重和顺序确认。
- 使用 PaddleOCR-VL-1.6 完成页面级文字、表格、公式和阅读顺序解析。
- 使用量化后的 Qwen3.5 完成多页关联、无关内容过滤和 C2D-XML v0.1 结构映射：16GB NVIDIA GPU 使用 9B FP8，16GB Apple Silicon 使用 4B 4-bit。
- 所有模型推理仅在用户自己的服务端设备上执行；Lark 发布如启用，则作为独立、显式授权的输出步骤，Markdown 和思源 `.sy` 由本地 Renderer 生成。

## C2D-XML

C2D-XML 是所有输出格式共享的唯一内容基线。v0.1 使用单一 `<document>` 根节点，保留标题、段落、六级标题、列表、引用、表格、代码、LaTeX 公式、链接和有限行内样式，并支持七种受限的文字颜色与背景色。

C2D-XML 不包含原始分页、坐标、来源追溯、图片、画板、分栏或平台专属资源。Lark 是首要输出目标；Markdown 作为通用回退格式，思源 `.sy` 由专用 Renderer 生成节点和目标格式元数据。输出层只做确定性转换，不调用模型补写内容。

上述完整处理方案仍是待验证的初始选型；仓库目前提供可运行的 Android 骨架、CameraX 4:3 拍照/1280 像素 OCR 派生图链路、本地多草稿任务首页、HTTP 客户端适配器，以及服务端文档转换 CLI，尚未形成端到端文档转换产品。这里的 1280 是派生图长边，不是 PaddleOCR 的 image-token 上限。

2026-09-05 Android 已实现任务首页、多草稿、离线采集及最终页序保存，拍照页完成后直接回首页；移除重拍和独立整理网格。当前离线版本已获用户真机人工测试通过，设备自动化和跨端联调另行验收。HTTP 客户端和 WorkManager 已实现，Android 尚未接入新服务端业务 API；当前交互与协议边界见 [文档索引](docs/README.md) 和 [任务首页](docs/android_task_home.md)。

2026-09-05 [服务端文档转换 CLI V2](docs/server_pipeline.md) 以文档 JSON 为主要结果：Paddle OCR → Qwen 多 block 草稿 → 最多五轮局部修复 → OCR/纯文本兜底，保留缺失位置并继续后续图片。支持完整 XML 与逐块报告、单并发模型轮换、只读历史工具及检查点恢复。当前冻结为 [pipeline-cli-v0.1.0 首版基线](docs/server_pipeline_baseline_v0_1.md)，保持第五轮代码与默认配置。五轮真实八图结果、质量限制和后续方向均已归档；新增 [FastAPI 后端与 SSE 逐块预览](docs/server_service.md)，复用冻结 pipeline；Android SSE 消费及跨端联调留待下一轮。

详细设计、实现状态和选型依据请参阅：

- [项目文档索引](docs/README.md)
- [总体方案](docs/architecture.md)
- [Android App 产品与架构草案](docs/android_app.md)
- [Android 开发环境](docs/android_env.md)
- [Android 相机架构与状态管理](docs/android_camera_architecture.md)
- [Android 扫描草稿与恢复说明](docs/android_scan_draft.md)
- [Android 相机拍摄与图像处理](docs/android_camera_capture.md)
- [Android 相机测试与验收](docs/android_camera_testing.md)
- [C2D-XML 标签体系](docs/c2d_xml.md)
- [模型选型记录](docs/model-selection.md)
- [Qwen3.5-9B FP8 输入 Token 与独立 Worker](docs/model_qwen_3_5_9b.md)

## License

Capture2Doc 源代码采用 [GNU Affero General Public License v3.0 only](LICENSE)（`AGPL-3.0-only`）。项目依赖的模型、数据集和第三方组件分别遵守其原始许可证。
