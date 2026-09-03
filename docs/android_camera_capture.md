# Android 相机拍摄与图像处理

> 状态：CameraX 低延迟连续拍照和 1280 像素 OCR 派生图已实现
> 最近核对：2026-09-03

本文说明当前拍摄链路的实现参数以及为什么采用这些方案。状态和组件关系见 [Android 相机架构与状态管理](android_camera_architecture.md)。

## 当前拍摄配置

| 项目 | 当前值 |
|---|---|
| CameraX | 1.6.2 |
| 摄像头 | 默认后置摄像头 |
| 画幅 | 4:3 |
| 拍摄模式 | `CAPTURE_MODE_MINIMIZE_LATENCY` |
| 请求分辨率 | `2560×1920`，约 5MP |
| 回退规则 | `FALLBACK_RULE_CLOSEST_LOWER_THEN_HIGHER` |
| 原始 JPEG 质量 | 90 |
| 闪光灯 | 关闭 |
| Preview 实现 | `PreviewView.ImplementationMode.PERFORMANCE` |
| Preview 缩放 | `PreviewView.ScaleType.FIT_CENTER` |
| postview 请求 | 支持时启用，目标 `640×480` |
| 未落盘拍摄上限 | 3 |

CameraX 最终输出分辨率由摄像头能力和 UseCase 组合决定。界面展示绑定后实际选择的分辨率，不把请求值误写成设备保证值。

## 为什么使用真实 JPEG

当前保留 `ImageCapture` 的真实 JPEG，不使用 `PreviewView` 截图或 `ImageAnalysis` 帧替代原图，原因是：

- Preview 帧主要为低延迟显示优化，分辨率、锐化和动态范围不等同于拍照结果；
- View 截图可能包含显示缩放、黑边或 UI 叠层；
- OCR 后续可能需要重新裁切、透视校正或采用不同输入规格，必须保留质量稳定的源文件；
- ImageCapture 会处理设备拍照管线和正确的方向元数据。

postview 仅用于即时缩略图反馈。设备不支持时显示非阻塞占位，不能让 postview 成为拍照成功的前置条件。

## 为什么选择低延迟模式和约 5MP

初版使用 `CAPTURE_MODE_MAXIMIZE_QUALITY` 并允许选择最高可用分辨率，会增加传感器处理、JPEG 编码、文件写入和后续解码耗时。文档连续拍摄更关注快速确认与无阻塞队列，因此改用 `MINIMIZE_LATENCY`。

请求 `2560×1920` 的考虑是：

- 约 5MP 对普通纸张和屏幕文档仍保留足够的小字细节；
- 相比高像素原图显著降低 JPEG 体积、写盘时间和归一化内存；
- 保持 4:3，便于 Preview、拍照和 OCR 派生图使用一致画幅；
- 通过“接近目标、优先较低”的回退策略避免重新落入最高分辨率。

这不是所有设备的固定输出。验收时必须记录实际分辨率、保存耗时和小字可读性，再决定是否需要设备能力档位。

## 4:3 取景交互

Preview 和 ImageCapture 都优先选择 4:3。UI 进一步提供明确的取景边界：

- 竖屏使用宽高比 `3:4` 的取景框，控制区位于下方；
- 横屏使用宽高比 `4:3` 的取景框，控制区位于右侧；
- `FIT_CENTER` 完整显示 Preview，不裁切、不拉伸；
- 黑色区域只承接取景框外的剩余空间；
- 点按坐标、对焦框和快门白闪均限制在实际取景框内。

这里选择“完整取景”而不是“预览铺满并裁切”，目的是让用户看到的构图尽量与保存结果一致。未来如果加入文档边缘检测，可以在同一取景框内叠加引导层，不需要改变原始画幅。

## 对焦策略

CameraX 默认持续执行自动对焦。按快门时不额外阻塞等待一次 AF，以免重新增加快门延迟。

用户点击 Preview 后：

1. 使用 `PreviewView.meteringPointFactory` 把界面坐标转换为测光点；
2. 通过 `CameraControl.startFocusAndMetering` 同时触发 AF、AE 和 AWB；
3. 3 秒后自动取消该点测光并恢复连续自动对焦；
4. 白色、绿色和黄色框分别表示进行中、成功和失败；
5. 对焦尚未完成时仍允许拍照。

## 屏幕旋转与照片方向

界面方向遵循 Android 系统的 Activity 旋转行为；照片方向独立监听设备物理方向：

- `OrientationEventListener` 获取物理角度；
- `ImageCapture.snapToSurfaceRotation()` 将角度归入四个 Surface 方向；
- 每次方向变化动态更新 `ImageCapture.targetRotation`；
- 原图由 CameraX 写入对应方向信息；
- OCR 派生图读取 EXIF，并把旋转或翻转真正应用到像素。

因此即使界面方向被系统锁定，照片仍可以按物理持机方向保存。最终方向仍需覆盖 0°、90°、180° 和 270° 的真机样本。

## OCR 派生图规则

`ImageNormalizer.normalize(source, destination, maxEdgePx, jpegQuality)` 当前默认：

```text
maxEdgePx = 1280
jpegQuality = 90
```

处理顺序：

1. 读取 JPEG 边界、文件大小以及 EXIF 旋转/翻转；
2. 根据目标长边计算 `inSampleSize`，避免完整解码高分辨率图片；
3. 解码后应用 EXIF 像素变换；
4. 长边超过 1280 时等比缩小，较小图片不放大；
5. 编码到同目录临时文件并同步写盘；
6. 优先原子移动到最终路径；失败时清理临时文件和不完整目标文件；
7. 回收过程中创建且不再使用的 Bitmap。

标准 4:3 图片的输出示例：

| 旋正后的原图 | OCR 派生图 |
|---|---|
| `2560×1920` | `1280×960` |
| `1920×2560` | `960×1280` |
| `800×600` | `800×600` |

归一化不裁剪、不拉伸、不补边，也不执行文档透视校正。

## 1280 像素与 1280 image tokens

两者是不同层级的参数：

- Android 的 1280 是上传友好的图片最长边；
- PaddleOCR-VL-1.6 的 1280 是默认 image-token 上限；
- 服务端 `max_pixels=1003520` 才控制模型最终视觉面积；
- processor 还会把宽高对齐到 28 像素网格。

`1280×960` 的面积大于默认 `max_pixels`，因此服务端仍会做一次最终缩放。Android 不复制 Paddle 的网格对齐规则，避免客户端文件契约与某个模型版本绑定。具体 token 计算见 [PaddleOCR-VL-1.6 输入 Token 与调优](model_paddle_ocr_v_1_6.md)。

## 性能观测

每张成功任务记录：

- 点击快门至 `onCaptureStarted`；
- 点击快门至 postview，可选；
- 点击快门至 `onImageSaved`；
- JPEG 落盘后的归一化耗时。

点击快门后立即提供触觉和短暂白闪，用户反馈不等待 JPEG 保存。原图回调完成后释放拍摄名额，归一化继续在单并发后台队列执行。

当前不启用实验性 ZSL。ZSL 受硬件、闪光灯和扩展模式影响，应作为独立能力开关评估，不与基础连续拍照链路绑定。
