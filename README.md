# Capture2Doc

Turn mobile document captures into validated, structured documents.

Capture2Doc 是一个处于设计与原型验证阶段的开源项目，目标是由 Android App 从拍照或视频中提取文档页面，将处理后的图片流交给本地推理服务，生成固定、可校验的 XML，并进一步转换为 DOCX、Markdown 或 LarkDoc。

当前计划采用客户端采集、本地服务端推理的两阶段架构：

- Android App 负责拍照、视频帧筛选、页面检测、矫正、去重和顺序确认。
- 使用 PaddleOCR-VL-1.6 完成页面级文字、表格、公式和阅读顺序解析。
- 使用量化后的 Qwen3.5 完成多页关联、无关内容过滤和固定 XML Schema 映射：16GB NVIDIA GPU 使用 9B 档位，16GB Apple Silicon 使用 4B 档位。
- 所有模型推理仅在用户自己的服务端设备上执行；Lark 发布如启用，则作为独立、显式授权的输出步骤。

上述方案是待验证的初始选型，不代表当前仓库已经提供可运行实现。详细设计和选型依据请参阅：

- [项目文档索引](docs/README.md)
- [总体方案](docs/architecture.md)
- [模型选型记录](docs/model-selection.md)

## License

Capture2Doc 源代码采用 [GNU Affero General Public License v3.0 only](LICENSE)（`AGPL-3.0-only`）。项目依赖的模型、数据集和第三方组件分别遵守其原始许可证。
