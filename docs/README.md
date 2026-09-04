# Capture2Doc 文档

Capture2Doc 当前处于设计与原型验证阶段。这里记录项目的初步架构、模型选型过程和后续验证标准；其中描述的是计划能力，而不是已经交付的功能。

## 文档索引

- [总体方案](architecture.md)：目标、处理流程、组件职责、XML 中间层和 16GB 显存约束。
- [Android App 产品与架构草案](android_app.md)：对话式宿主 App、工具系统、Capture2Doc 功能边界和下一里程碑。
- [Android 开发环境](android_env.md)：工具链版本、首次初始化、构建、检查和通用真机调试。
- [Android 相机架构与状态管理](android_camera_architecture.md)：模块边界、状态机、并发、文件和生命周期。
- [Android 相机拍摄与图像处理](android_camera_capture.md)：CameraX 配置、4:3 取景、对焦、方向、低延迟和 1280 像素选型。
- [Android 相机测试与验收](android_camera_testing.md)：自动化覆盖、通用真机清单、性能指标和 OCR 输入对照。
- [C2D-XML 标签体系](c2d_xml.md)：v0.1 页面无关中间表示、标签约束，以及 Lark、Markdown、思源输出映射。
- [模型选型记录](model-selection.md)：评估集、候选模型、公开结果、选择结论和自建评测计划。
- [PaddleOCR-VL-1.6 输入 Token 与调优](model_paddle_ocr_v_1_6.md)：图像 token 计算、上下文预算、vLLM 参数和 WSL 问题记录。
- [Qwen3.5-9B FP8 输入 Token 与独立 Worker](model_qwen_3_5_9b.md)：图像 token、chat template、16K/8K 预算、量化和显存验证方法。

## 当前决策

- 系统边界：Android App 负责拍照/视频采集和页面预处理，本地服务端接收页面图片流并完成解析、组装和导出。
- 16GB 档位：PaddleOCR-VL-1.6 页面解析 + Qwen3.5 多页编排；NVIDIA 默认 9B FP8，Apple Silicon 默认 4B 4-bit。
- 中间表示：C2D-XML v0.1 标签基线已经冻结，只保留最终文档的逻辑结构、内容、顺序和有限行内样式；采集页码、坐标和来源追溯属于内部中间数据，不进入 C2D-XML。下一步编写对应 XSD。
- 输出目标：优先生成 Lark 文档，Markdown 作为通用回退格式，思源 `.sy` 由专用 Renderer 生成；当前不考虑 DOC/DOCX。

## 状态约定

文档使用以下词语区分成熟度：

- **已决定**：当前设计基线，除非评测结果否定，否则按此推进。
- **候选**：需要在真实样本上比较后才能确定。
- **待设计**：尚未冻结公共接口或数据结构。
- **待验证**：来自公开 Benchmark 或模型文档，尚未在 Capture2Doc 的硬件与数据上复现。

文档中的公开结果截至 **2026-09-02**。不同版本、提示词、推理框架和量化方式的成绩不能直接视为等价结果。
