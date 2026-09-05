# Android 流式文档解析与复制

## 协议边界

实现依据主分支 `docs/server_service.md`、`docs/server_openapi.json` 及 `docs/c2d_xml.md`。Android 不定义新服务端接口；旧网络模型 `flag / plainText / pages` 已替换为 `documentId / schemaVersion / status / title / wordCount / needsReview / c2dXml / sha256 / message`。旧任务清单和纯文本缓存保持可读，但不作为经过校验的 C2D 最终结果开放富文本复制。

所有请求使用同一 OkHttp 客户端与 Bearer 鉴权，不跟随跨地址重定向。创建、JPEG 上传摘要确认与 finalize 页序提交保留原有幂等键。上传计数来自本地已确认页面集合；识别计数来自服务端事件，不生成模拟百分比。

连接设置通过鉴权 `GET /openapi.json` 检查 `DocumentResult` schema。设备令牌按规范化服务地址关联，用 Android Keystore AES-GCM 加密后原子写入私有文件 `connection-settings.enc`，不进入任务清单或日志。默认地址只影响新任务和尚未绑定的任务。正式构建只接受 HTTPS；Debug 允许 HTTP 以支持本地测试。

## 生命周期与持久化

- 相机完成：冻结并原子保存最终页序，立即进入阅读页。读取与上传不依赖该页面继续存在。
- 阅读页 RESUMED 且在线、有服务端 ID 与令牌：维持一个 SSE 连接。离开前台取消请求，WorkManager 继续补传和 GET 状态查询。
- `document.snapshot` 替换预览；`blocks.patch` 按锚点后的连续区间删除与插入，校验 revision、blockId、version。重复事件不重复应用。
- `document.progress` 更新阶段与真实计数。`document.completed`、`document.failed`、终态 snapshot，以及流关闭/HTTP 204 均触发普通 GET；终态不接受旧进度回退。
- 重连使用 `Last-Event-ID`。409、事件序列或版本不匹配清除续传游标，重新请求 snapshot；已有预览可继续显示。网络/可重试响应指数退避，最高 30 秒；401 引导连接设置。
- 事件通道不丢弃更新。每任务独立 Mutex 串行保存，session generation 拒绝旧连接的迟到事件。

私有目录布局：

```text
files/
  connection-settings.enc          # Keystore 密文与随机 IV
  document_tasks/
    tasks.json                     # 任务摘要、绑定地址、上传确认及最终页序
    <taskId>/
      scan_draft/...               # 原图与派生图，沿用原实现
      preview.json                # blocks + revision + cursor + progress，同一 AtomicFile
      result.json                 # 校验通过的完整公共 JSON，独立 AtomicFile
```

最终 XML 在 JSON 解码后，直接对原始 UTF-8 字节计算 SHA-256，不先规范化换行或重序列化 XML。通过摘要与安全解析后才保存最终 JSON、恢复标题和字数并开放复制。摘要或解析失败保留预览，允许重新获取。空结果显示“解析完成，未识别到可用内容”，不把旧预览当作最终正文。`needsReview=false` 按协议读取，不代表质量保证。

## 渲染

共享 Kotlin C2D 模型覆盖 v0.1 全部标签：标题、六级标题、段落、嵌套引用/列表、带合并单元格的表格、代码、分隔线、固定顺序的行内样式、链接、换行、七种前景/背景色与 LaTeX。解析检查命名空间、版本、属性、文本及表格网格；拒绝 DTD、外部实体、处理指令、注释、未知标签和不支持的结构。另设输入长度、节点数、嵌套深度和表格尺寸边界，避免不受控资源消耗。

Compose 负责顶栏、阶段信息与复制菜单。单个 WebView 通过 [WebViewAssetLoader](https://developer.android.com/develop/ui/views/layout/webapps/load-local-content) 加载本地资源，无 JS 原生桥接，不允许文件/内容 URI，不加载外部资源；受限 HTTP/HTTPS 链接交给系统浏览器。

`reader.js` 依据块身份修改 DOM。已有节点保留；正在底部时跟随更新，向上阅读时保留可见块偏移，被撤回时寻找下一个仍存在的锚点，并提供“查看最新内容”。宽表格、代码、公式独立横向滚动。浅蓝骨架与变更高亮与正文颜色分开；完成、失败、离线、后台与系统关闭动画时停止等待动画。预览阶段禁用正文选择/复制。

KaTeX 配置 `trust=false / maxExpand=1000 / maxSize=30 / strict=error`，不可渲染的公式显示原始源码。参见 [KaTeX options](https://katex.org/docs/options)。本地资源来自官方 npm `katex-0.18.5.tgz`，包 SHA-256：`3a37c3713a2cdda03b135f717dd8dcf908d4fddac688b38128c38afa21119ec5`。许可证随资源保存在 `app/src/main/assets/katex/LICENSE`。

## 剪贴板

复制范围仅包含最终文档正文（包括文档自身标题），不包含状态提示、菜单或等待装饰。

| 入口 | 系统剪贴板 |
|---|---|
| 纯文本 | 保留段落、列表层级、制表分隔、代码换行与公式源码 |
| 思源 | 标准 HTML + 纯文本回退，保留受限样式 |
| 飞书 | 与思源共享同一个 HTML 生成器 |

使用 [ClipData.newHtmlText](https://developer.android.com/reference/android/content/ClipData#newHtmlText(java.lang.CharSequence,%20java.lang.CharSequence,%20java.lang.String)) 创建富文本 item，同时明确声明 `text/html` 与 `text/plain`。真机检查发现平台的 `newHtmlText` 本身仅声明 HTML MIME，因此显式补齐双类型。公式保留可读源码，不生成目标 App 原生公式或平台私有记录。

复制前以 `ClipData.writeToParcel` 测量实际序列化载荷，超过 512 KiB 拒绝；不截断，不静默改用其他格式。只有 `ClipboardManager.setPrimaryClip` 成功返回才提示“已复制”。目标软件的实际粘贴保真度需独立于载荷单元测试进行验收。
