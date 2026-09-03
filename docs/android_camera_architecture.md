# Android 相机架构与状态管理

> 状态：相机能力探针已实现；尚未接入正式 Capture2Doc 页面、持久化会话和上传流程
> 最近核对：2026-09-03

本文记录 Android 相机模块当前已经实现的职责、数据流和状态管理方式。具体拍摄参数与选型理由见 [Android 相机拍摄与图像处理](android_camera_capture.md)，自动化测试和真机验收方法见 [Android 相机测试与验收](android_camera_testing.md)。

## 功能边界

当前相机模块只负责验证以下本地链路：

```text
运行时权限 → CameraX 会话 → 4:3 预览 → 连续拍照
          → 原始 JPEG 落盘 → EXIF 方向校正
          → 生成最长边 1280 像素的 OCR 派生图 → 批次结果
```

已经实现：

- 仅在用户进入相机功能后请求 `CAMERA` 权限；
- 后置摄像头预览和真实 JPEG 拍摄；
- 竖屏 `3:4`、横屏 `4:3` 的明确取景框；
- 连续拍照、单张任务状态、队列上限和批次完成；
- 点按对焦、物理方向监听和 EXIF 像素旋正；
- 原图与 OCR 派生图的 App 私有目录保存；
- 尺寸、文件大小、方向和分阶段耗时展示。

尚未实现：

- 文档边缘检测、裁切、透视校正、质量判断和去重；
- 相册导入、闪光灯、变焦、镜头切换、视频取帧和 ZSL；
- Room 或其他持久化任务模型、进程被系统回收后的恢复；
- 图片上传、OCR 请求、重试调度和最终文档组装。

因此当前页面仍是技术探针，不代表最终产品导航或扫描交互。

下一里程碑由扫描草稿与页面整理功能消费本模块生成的 `CaptureArtifact`：拍照完成后进入页面整理页，支持缩略图、大图查看、删除、继续拍摄、重拍和拖拽排序，并在本地生成扫描草稿。相机模块不负责草稿 UI、页序持久化或上传状态。

## 组件职责

| 组件 | 当前职责 |
|---|---|
| `CameraProbeRoute` | 连接生命周期、权限请求、ViewModel 和 CameraX 会话 |
| `CameraProbeScreen` | 渲染权限、取景、连续拍摄和结果状态 |
| `CameraPreview` | 创建 `PreviewView`，绑定 Preview 与 ImageCapture，监听物理方向 |
| `CameraSessionHandle` | 向上层暴露拍照能力、实际分辨率、postview 能力和点按对焦 |
| `CameraCaptureProfile` | 集中保存画幅、目标分辨率、JPEG 质量和队列上限 |
| `CameraProbeViewModel` | 管理相机门禁、图片任务、完成等待和后台归一化 |
| `ImageNormalizer` | 采样解码、EXIF 变换、等比缩放和原子写入 |
| `CaptureArtifact` | 保存文件路径、尺寸、字节数、方向和耗时指标 |

这些类型目前都属于 App 内部接口，不是稳定公共 API。

## 相机门禁状态

相机整体可用性由 `CameraGateState` 表示：

```text
PermissionRequired → RequestingPermission
                  ├→ PermissionDenied
                  └→ StartingCamera → Ready
                                      ├→ CameraUnavailable
                                      └→ Error
```

- 权限状态在首次进入以及 Activity `ON_RESUME` 时重新读取，用户从系统设置返回后可以恢复；
- 无摄像头设备进入 `CameraUnavailable`，不会阻止宿主 App 启动；
- CameraX 绑定失败进入可重试错误状态；
- 离开或重建预览时释放绑定，重新进入前台后重新建立会话。

## 单张任务状态

每次按下快门立即创建唯一 `CaptureJob`：

```text
Queued → Capturing → Saving → Normalizing → Ready
                                      └────→ Failed
```

任务保存捕获 ID、文件路径、阶段时间点、临时 postview、最终 `CaptureArtifact` 和可读错误信息。状态推进只允许向前；已经完成或失败的任务不会被迟到回调覆盖。

相机状态和图片任务状态相互独立。某张图片保存或归一化失败时，其他任务以及预览仍可继续运行。

## 并发和背压

- 最多接受 3 个尚未完成原图落盘的任务；
- `Queued`、`Capturing` 和 `Saving` 计入拍照上限；
- 原图进入 `Normalizing` 后立即释放一个拍摄名额；
- 所有归一化任务通过单个 `Mutex` 串行处理，避免同时解码多张照片造成内存和 CPU 峰值；
- 队列满时只禁用快门，不遮挡或停止 Preview；
- 点击“完成”后不再接受新任务，等待所有任务进入 `Ready` 或 `Failed` 后展示结果。

这里的上限是进程内背压，不是持久化任务队列。未来接入正式扫描会话时，应把页面元数据和上传状态持久化，并由可恢复后台任务负责网络阶段。

## 文件和数据边界

每张新照片写入：

```text
files/captures/<captureId>/
├── original.jpg
└── normalized_1280.jpg
```

- `original.jpg` 是 CameraX 保存的真实拍照结果，供后续重新处理；
- `normalized_1280.jpg` 是方向已旋正、最长边不超过 1280 像素的 OCR 派生图；
- 文件位于 App 私有持久目录，不进入系统相册，不需要存储权限；
- App 卸载时文件会被删除；当前探针尚未提供长期清理策略；
- `CaptureArtifact.normalizedPath` 保持通用命名，没有暴露具体 OCR 模型。

Android 只生成通用图片产物。OCR 模型的视觉 token、网格对齐和推理参数属于服务端边界，不进入相机状态模型。

## 生命周期限制

当前 `AndroidViewModel` 可以承受普通旋转和 Activity 配置变化，已经完成的任务不会因预览重新绑定而再次归一化。但任务列表尚未写入数据库或 `SavedStateHandle`，因此进程被系统终止后不能保证恢复。

正式功能接入前需要补充：

1. 扫描会话和页面元数据的持久化；
2. 文件存在性与任务状态的启动时校验；
3. 未完成归一化及上传任务的幂等恢复；
4. 用户删除页面、取消扫描和定期清理文件的明确规则。

其中下一里程碑先覆盖本地扫描草稿、页面顺序和旋转/后台切换后的恢复；上传恢复及相机回调进行中的进程死亡恢复留到网络阶段处理。
