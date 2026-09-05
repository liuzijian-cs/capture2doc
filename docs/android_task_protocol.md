# Android / Server 文档任务协议 v1（客户端提案）

日期：2026-09-05。状态：Android 侧定义的最小联调提案，服务端尚未实现或确认。HTTP 客户端按本提案实现不代表跨端验收通过。产品语义见 [任务首页](android_task_home.md)。

## 通用规则

### 与已合并服务端 CLI 的映射（HTTP 仍待评审）

本次提交整合 origin/main 的 `d881227`、`89cef78` 和 `d0cb5c5`，保留可恢复 CLI、WSL 验证记录及 Schema 错误位置/代码块修复说明；尚未接入 HTTP。其内部字段为 document_id、不可变 image_id、ordered_image_ids。HTTP 提案中的 documentId 对应 document_id，pageId 对应 image_id，pageIds 对应 ordered_image_ids；不得因此在服务端再引入一层可修订逻辑页。

本轮 Android 已移除重拍复用，每张新增照片获得新 pageId，首次上传时可直接将其作为该文档下不可变 image_id；同 ID 不同摘要仍拒绝。旧单草稿从未关联远端，原有重拍占位恢复为失败，不上传占位。此映射需服务端 HTTP 适配层评审确认。上游 /images/{image_id} 路由草案与本客户端 /pages/{pageId} 提案尚未统一，不能把 JSON 命名映射当成已经可调用的兼容 API。

CLI 的 complete、needs_review 和中断状态不能直接照搬为客户端 COMPLETED。只有协议定义的最终结果完整且服务端确认可交付时才返回 COMPLETED；needs_review 如何在任务页展示须补充产品决定，不能静默吞掉复核标记。本轮未新增复核 UI。

基础地址通过 Android Gradle 属性 `capture2doc.baseUrl` 配置；默认留空，禁止伪造文档创建成功。Release 仅 HTTPS；Debug 可连接开发 HTTP 服务。初轮仅供可信开发网络使用，无账号鉴权；公网部署前必须增加身份认证与资源授权，不能把随机 ID 当权限。所有 JSON UTF-8，时间为 Unix 毫秒。ID 是不透明字符串，客户端 URL 编码后作为路径段。服务器不接受客户端文件路径。

网络失败不删除本地照片，也不禁止拍摄、保存或本地完成。未连接提示独立于本地页面 READY 和服务端 flag；临时连接失败不是页面处理失败。连接/读取超时、408、429 与 5xx 重试；其他 4xx 显示阻断错误，由用户重试前排查。服务器错误响应为 `{"message":"可展示的说明"}`，不得返回堆栈或密钥。未知状态或字段缺失视为协议错误，不映射为成功。

## 创建草稿

`POST /v1/documents`，JSON `{"clientTaskId":"本地持久化 UUID"}`，请求头 `Idempotency-Key: 同一 clientTaskId`。

响应 200/201：`{"documentId":"opaque-id"}`。同一 key 必须复用同一文档，响应丢失后重试不得新建第二个任务。客户端先持久化独立本地任务身份即可打开相机，无需等待服务；未连接时持续提示，本地 taskId 不冒充 documentId。后台以 taskId 为幂等键创建文档，真实 documentId 原子保存后才逐页上传。每次新建均有独立 taskId；重试同一任务才复用该 key。

## 逐页上传

`PUT /v1/documents/{documentId}/pages/{pageId}`，`Content-Type: image/jpeg`，body 为已完成 EXIF 归一化、最长边不超过 1280、JPEG quality 90 的文件。不得上传 postview 或原图。请求头 `X-Content-SHA256` 是该载荷的十六进制摘要。

响应 200/201：`{"pageId":"same-page-id","sha256":"same-digest"}`。同文档/页面/摘要重复请求复用资产及 OCR 任务，不重复排队；相同页面 ID 不同摘要响应 409（首版没有重拍）。服务端须在可靠接收后确认，再立即排入 OCR；入队不承诺即时 GPU 推理。

拍摄期间删除已经上传的候选页，不要求立即远端删除；最终集合不引用它。相机候选删除与首页隐藏任务是两种操作。

## 提交最终页序

`POST /v1/documents/{documentId}/finalize`，JSON `{"pageIds":["C","A"]}`；`Idempotency-Key: clientTaskId-finalize`。

客户端“完成”先持久化不可变页序并回首页，后台等引用图片上传确认后调用本接口。响应 200/202：`{"accepted":true}`。服务端第一次接受后冻结集合与顺序；相同提交重试复用，不同集合/顺序返回 409；缺失图片返回 409，不把缺失当文档完成。服务器等待 C、A 的有效 OCR 结果后按 C、A 组装，不使用已排除 B 的结果。此接口不是 XML 内存组装器的 `C2DAssembler.finalize()`。

## 查询状态

`GET /v1/documents/{documentId}` 响应：

```json
{
  "documentId": "opaque-id",
  "flag": "OCR",
  "title": null,
  "wordCount": null,
  "plainText": null,
  "message": null,
  "pages": [{"pageId": "A", "flag": "QUEUED"}]
}
```

文档 flag：`IDLE / OCR / ASSEMBLING / VALIDATING / COMPLETED / FAILED`。页面 flag：`QUEUED / PROCESSING / COMPLETED / FAILED`。文档 flag 只描述执行阶段，草稿/提交由客户端持久化的提交意图及接受确认区分。页面失败必须在展示层阻断正常进行中提示。首页过滤已从本任务移除的页面错误，但文档级 FAILED 总是展示。

COMPLETED 必须返回非空最终 title、非负 wordCount 和 plainText（可空字符串），客户端不自行统计 OCR 或 XML 标签字数。plainText 仅作为首版只读内容，不替代公共 C2D-XML/Renderer 契约。其他阶段标题可提前更新，但字数仍显示“—”。服务端文档 ID 必须与请求匹配。

首版客户端使用查询适配器，后台 WorkManager 负责网络约束、重试及重启后继续。正常等待安排最低 5 秒延迟的后继查询，已有后继工作时不重复增加链；网络失败才指数退避。系统可能延迟调度，不承诺 5 秒实时刷新。首页前台可触发重新同步。相机拍摄、页面派生图成功及完成提交均触发独立任务同步。隐藏任务不取消调度。

## 流式 block 扩展（设计保留，本轮不接入）

后续 `GET /v1/documents/{id}/events` 可采用 SSE，携带递增事件 ID；客户端重连通过 Last-Event-ID 去重续传。事件类型为状态快照、block 更新及最终文档。block 更新应携带稳定 blockId 和单调 revision，用替换/更新语义而非直接 append，避免重连重复文本。事件保留期限、尾块修订与 XML 更新映射仍需与组装器联调确定；未确定前以状态查询和最终 plainText 为唯一生产读取路径，不实现一个假流式接口。

## 数据保留与兼容

没有远端删除/取消 API：用户选择的首页“删除”仅本机隐藏，后台继续。远端存储配额、孤立页面回收期限和账户隔离需服务端补充设计。任务固定首次绑定的非空服务地址，切换构建配置不能把已有照片发往另一服务。旧本地单草稿也可离线恢复采集。尚未配置端点的任务可在后续配置服务后首次绑定；已有非空端点不得随构建配置改写。离线点击完成仅持久化最终页序，待关联 ID 和上传确认后再调用 finalize。

## 验收

2026-09-05 用户确认当前离线拍摄版本真机人工测试通过。本地任务建立、未连接提示与真实拍摄不依赖本文 HTTP 服务；此反馈不表示创建、上传、finalize、flag 查询或流式协议已经完成服务端联调。新版设备自动化仍未执行，不能把测试 APK 构建成功写成设备用例通过。

覆盖创建响应丢失后幂等重试；重复上传与摘要冲突；C、A 页序冻结；上传失败保留本地照片；隐藏仍执行；非法/未知 flag；最终结果缺字段；服务重启及客户端进程恢复。本轮 9 项 JVM HTTP 契约测试通过，检查创建幂等键、JPEG 上传及摘要确认、最终 C/A 页序、最终结果字段、未知 flag、错误文档 ID、HTTP 重试分类和空配置失败。真实服务端、流式、鉴权与端到端模型质量尚未验收。

WorkManager 使用依据：[官方发布记录](https://developer.android.com/jetpack/androidx/releases/work)、[唯一任务策略](https://developer.android.com/reference/androidx/work/ExistingWorkPolicy)。
