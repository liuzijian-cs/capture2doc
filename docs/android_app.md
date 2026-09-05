# Android App 产品与架构

> 最近修订：2026-09-05。任务首页、多草稿隔离、相机导航和 HTTP/WorkManager 客户端已实现；2026-09-05 用户确认当前离线拍摄版本真机人工测试通过；服务端 API、流式 block 和恢复专项仍待联调验证。

## 产品定位

当前 App 是文档任务列表，而不是对话式助手外壳。普通页面蓝白，相机蓝黑。首页支持标题搜索、创建时间倒序、单项左滑确认、多选确认和蓝底白色加号。行内标题与草稿标签独立，第二行显示时间、有效图片数量和最终字数。

## 页面与采集流程

- 允许未连接服务器先建立本地任务并进入相机，持续显示未连接提示；后台关联真实文档 ID 后再上传，不生成假 ID。
- 多个草稿可以并存，一次只打开一个相机。任务和图片目录隔离。
- 相机候选单击查看、双击删除、长按排序；移除重拍与独立整理页。
- 返回时保存草稿或仅从本机列表移除；已有后台工作继续。真实原图未落盘时等待。
- 完成先冻结有序页面引用，立即返回首页。后台生成派生图、逐页上传；全部引用页确认后调用文档 finalize。
- 草稿点击续拍，提交后查看只读状态和最终文本，暂不编辑。
- 首页移除仅持久化 hidden，不删图片、不取消上传/远端任务。拍摄中删除候选照片是另一个文件清理操作。

完整行为见 [任务首页](android_task_home.md)，跨端时序见 [Pipeline](capture_pipeline.md)，字段见 [客户端协议提案](android_task_protocol.md)。

## 模块职责

- TaskHomeViewModel 管理首页导航、本地创建与后台关联、保存、提交及本机移除，SavedStateHandle 仅记录导航身份。
- TaskRepository 使用 AtomicFile + org.json 管理任务索引；每个任务复用独立 ScanDraftRepository 与私有图片目录。
- CameraProbeViewModel 按任务注入仓库，不在多个文档间共享页面事实。相机归一化可在返回首页后继续。
- HttpDocumentService 实现客户端提案的创建、JPEG PUT、页序提交和状态 GET，服务地址默认空。Runtime 不引入第三方 JSON 序列化库；JSON-java 仅用于 JVM 测试。
- TaskSynchronizer 独立于 UI，上传确认及提交意图落盘；TaskSyncWorker 通过 WorkManager 进行网络约束、任务串行和退避。
- 服务器仍负责 OCR 队列、模型调度、最终组装与生成物；本轮没有修改服务端实现。

## Android 视觉主题基线

Android App 整体继续采用当前简洁、克制的界面方向，参考 ChatGPT 的信息层级和留白方式，以黑色、白色、中性灰和蓝色作为主要视觉体系。蓝色用于主要操作、选中态、焦点和少量品牌强调；红、黄、绿只作为状态与反馈的辅助色，避免把页面变成多色卡片集合。

本轮确定的 Android 专用颜色如下，不改变 C2D-XML、Renderer 或其他输出端的颜色定义：

| 色系 | 深色值 | 浅色值 | 建议用途 |
|---|---|---|---|
| 蓝色 | `#5595D1` | `#A9C9FF` | 主要操作、选中态、焦点、进度 |
| 红色 | `#E97A7A` | `#FFBEC2` | 错误、删除和高风险提示 |
| 黄色 | `#E5C679` | `#FFEEBC` | 警告、待处理和质量提醒 |
| 绿色 | `#55967E` | `#B4DBCA` | 成功、完成和可用状态 |

这里的“深色”和“浅色”表示同一色系的两个色阶，不等同于只供系统深色模式或浅色模式使用。普通业务页面固定使用浅色蓝白方案，相机显式使用深色蓝黑方案，不跟随系统切换。使用固定 `ColorScheme` 并完全关闭动态配色；下表保留两套 token 定义：

| Token | 浅色模式 | 深色模式 |
|---|---|---|
| `primary` | `#5595D1` | `#A9C9FF` |
| `onPrimary` | `#111318` | `#111318` |
| `primaryContainer` | `#A9C9FF` | `#5595D1` |
| `onPrimaryContainer` | `#111318` | `#111318` |
| `secondary` | `#6A7283` | `#6A7283` |
| `onSecondary` | `#111318` | `#111318` |
| `secondaryContainer` | `#313A4A` | `#313A4A` |
| `onSecondaryContainer` | `#111318` | `#111318` |
| `tertiary` | `#8B90A1` | `#8B90A1` |
| `onTertiary` | `#111318` | `#111318` |
| `tertiaryContainer` | `#2F3545` | `#2F3545` |
| `onTertiaryContainer` | `#111318` | `#111318` |
| `background` | `#F7F7F8` | `#0F0F10` |
| `onBackground` | `#202123` | `#F4F4F5` |
| `surface` | `#FFFFFF` | `#171719` |
| `onSurface` | `#202123` | `#F4F4F5` |
| `surfaceVariant` | `#E5E5E8` | `#35363A` |
| `onSurfaceVariant` | `#5F6368` | `#C5C7CA` |
| `error` | `#E97A7A` | `#FFBEC2` |
| `onError` | `#111318` | `#111318` |
| `errorContainer` | `#FFBEC2` | `#E97A7A` |
| `onErrorContainer` | `#111318` | `#111318` |
| `outline` | `#5F6368` | `#C5C7CA` |
| `outlineVariant` | `#E5E5E8` | `#202124` |
| `inverseSurface` | `#171719` | `#FFFFFF` |
| `inverseOnSurface` | `#F4F4F5` | `#202123` |
| `inversePrimary` | `#A9C9FF` | `#5595D1` |
| `surfaceTint` | `#5595D1` | `#A9C9FF` |
| `scrim` | `#000000` | `#000000` |
| `surfaceBright` | `#FFFFFF` | `#35363A` |
| `surfaceDim` | `#E5E5E8` | `#0A0A0B` |
| `surfaceContainerLowest` | `#FFFFFF` | `#0A0A0B` |
| `surfaceContainerLow` | `#F8F8F9` | `#171719` |
| `surfaceContainer` | `#F2F2F4` | `#202124` |
| `surfaceContainerHigh` | `#ECECEE` | `#2A2B2E` |
| `surfaceContainerHighest` | `#E5E5E8` | `#35363A` |

成功与警告通过 Android 内部状态色扩展提供：浅色模式的 `success / successContainer` 为 `#55967E / #B4DBCA`，`warning / warningContainer` 为 `#E5C679 / #FFEEBC`；深色模式交换同一色系的深浅角色。状态色前景统一使用近黑色 `#111318`。

上表已按当前主题代码核对，属于实现记录，不是全部颜色组合的对比度验收结论。中性蓝灰深色容器与近黑前景仍需在原型评审时检查可读性；不得由“固定 ColorScheme 已实现”推断所有组件已通过视觉验收。


## 已实现与验证边界

本轮完成任务首页、蓝白主题、草稿恢复入口、只读详情、相机直接返回、多任务文件隔离、旧草稿原地登记、隐藏标记、最终页序快照、HTTP 适配器与 WorkManager 调度。协议由 Android 侧提议，服务器尚未实现；当前修订允许未连接先拍摄，持续显示未连接提示，不把 Preview 样例或本地 ID 当服务端任务。

自动检查结果和待验收清单统一记录在 [本轮任务实现记录](android_task_home.md#本轮实现记录)。本次离线版本人工通过与既有 CameraX 基线分别记录，不等于新版设备自动化或网络链路验收。

## 已被替代的方案

原对话首页、单草稿选择弹窗、“完成进入网格”、整理页重拍、跟随系统主题均被本轮设计替代。历史原因和提交证据见 [开发记录](android_progress_and_plan.md)。视频取帧、自动裁切、透视校正、聊天 Provider、账号、远端删除和已提交文档编辑不属于本轮。

## 后续联调

首要是服务端评审并实现 v1 提案，配置可访问服务地址后验证逐页上传与最终任务。后续再接入流式 block、完整 Renderer 和输出文件。部署到不可信网络前必须补充鉴权、资源授权、配额与清理策略；当前 HTTP Debug 配置只供开发。
