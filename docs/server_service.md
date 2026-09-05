# Capture2Doc 后端首版与逐块预览

本页定义已实现的服务端协议；Android 现有 HTTP 适配器仍需下一轮更新并联调。CLI 冻结基线、模型参数、提示词及历史八图产物保持原样。部署和真实模型验收记录见 [服务验收](server_service_validation.md)。

## 运行链路

Android HTTPS + 每设备 Bearer token → volcano OpenResty → 现有 SD-WAN `100.64.250.1:11209` → FastAPI。API 只管理上传、状态和 SSE；独立 worker 负责 Paddle 与 Qwen。SQLite 是业务、预览及事件日志的持久化源，文件检查点是模型草稿、预算和正式正文的持久化源。

创建文档后，每张 JPEG 上传完成即排 OCR。图片 ID 不可变，不再引入另一层可修订逻辑页。finalize 的 `pageIds` 冻结最终集合与顺序，所需 OCR 任务已有终态后才按此顺序调用 Qwen。未引用的图片不进入正文。OCR 截断、空结果和三次请求失败均保留实际状态，并作为终态参考；图像仍是 Qwen 的内容和样式依据。

worker 优先最早 finalize 文档所需 OCR，无已提交文档时按上传顺序识别。Paddle 可连续处理队列，空闲默认 30 秒后卸载。开始 Qwen 前停止 Paddle 并核验进程组和显存；Qwen 处理整个文档，期间上传继续接收，OCR 等待。所有历史模型运行目录均检查遗留清理状态；GPU 锁被占用时等待，不能将其误报为文档失败。

每图 Qwen 返回完整候选 JSON，逐块及组合校验后立即发布合格块；失败块单请求串行修复，其他合格块不等待。最多五次修复、拆分/合并预算继承、来源冲突和局部兜底沿用 CLI。每图正式提交仍是原子操作。兜底合法段落可预览；缺失块没有正文，继续下一图。执行结束且最终结果保存成功即 COMPLETED，无质量门槛，`needsReview=false`；存储损坏、身份冲突或 GPU 清理失败为执行故障。

## HTTP 契约

业务请求均需 `Authorization: Bearer <设备令牌>`。这是同一用户的个人多设备服务：有效设备令牌可访问全部文档，不提供多租户隔离。令牌只在本地创建时显示一次，数据库存 SHA-256；撤销即时影响新请求，现有 SSE 每次轮询检查撤销。URL 中禁止携带令牌。公开 `/docs`、`/redoc` 关闭，`GET /openapi.json` 同样鉴权。

| 方法与路径 | 请求 | 成功响应 |
| --- | --- | --- |
| POST `/v1/documents` | JSON `{"clientTaskId":"本地稳定 UUID"}`；Idempotency-Key 与其相同 | 201 创建 / 200 重试，`{"documentId":"opaque-id"}` |
| PUT `/v1/documents/{id}/pages/{imageId}` | 原始 `image/jpeg`，`X-Content-SHA256` 为上传字节摘要 | 201 / 200，`{"pageId":"imageId","sha256":"..."}` |
| POST `/v1/documents/{id}/finalize` | JSON `{"pageIds":["C","A"]}`；Idempotency-Key 为 clientTaskId + `-finalize` | 202，`{"accepted":true}` |
| GET `/v1/documents/{id}` | 无 | 当前状态或完整结果 JSON |
| GET `/v1/documents/{id}/events` | 可选 Last-Event-ID | `text/event-stream` |

同一文档/图片 ID/摘要重复上传必须仍核验实际请求摘要，不重复排 OCR；同 ID 不同摘要 409。finalize 相同顺序可重试，不同顺序、缺失图片或冻结后新增图片为 409。上传中的同 ID 请求暂返回 429。JSON 命令须有 Content-Length，最大 64 KiB。图片允许无 Content-Length 的分块传输，按最大单图限制预留空间。

GET 返回 `documentId, schemaVersion="0.1", status, title, wordCount, needsReview=false, c2dXml, sha256`，不增加 blocks 数组。状态为 `IDLE/OCR/ASSEMBLING/VALIDATING/COMPLETED/FAILED`。处理中 XML、摘要和字数为 null。失败额外返回安全 `message`。完成标题优先文档标题或首个标题块，否则“文档”；字数是正文非空白 Unicode 字符数。摘要为 XML 原始 UTF-8 字节 SHA-256，客户端不能先重新序列化 XML。全部图片完成但没有合法正文时 XML/摘要为 null、字数 0，仍 COMPLETED。

通用错误 JSON 为 `{"message":"可展示说明"}`。401 鉴权、404 不存在、409 身份/冻结冲突、413 大小/数量限制、415 非 JPEG、422 字段错误、429 并发/任务上限、507 存储门槛。408、429 和网络/5xx 可退避重试；其他 4xx 排查后再重试。

## SSE 消费协议

使用标准 [SSE](https://html.spec.whatwg.org/multipage/server-sent-events.html)，事件 `id` 是当前文档内持久化序号，`data` 为 JSON。事件日志随文档保留，不单独截断。

| event | data |
| --- | --- |
| document.snapshot | documentId、status、当前有序 blocks、revision，已有的阶段计数，终态时 sha256 或 message |
| document.progress | documentId、status、currentImageId、completedOcrImages、completedImages、totalImages |
| blocks.patch | documentId、baseRevision、revision、afterBlockId、removeBlockIds、blocks |
| document.completed | documentId、status=COMPLETED、sha256 |
| document.failed | documentId、status=FAILED、message |

持久化事件另有 `publishedAtUnixMs` 供延迟诊断。未 finalize 时 totalImages 是当前已上传数量，可增长；冻结后是最终所需图片数。没有模型 token 进度或伪造百分比。

每个块为 `{"blockId":"稳定内部 ID","version":1,"xml":"完整合法块 XML"}`。`blocks.patch` 是一个原子连续区间修改：确认本地 revision 等于 baseRevision；找到 afterBlockId（null 为开头）；删除紧接其后的 removeBlockIds（必须连续且完全匹配）；在原位置插入完整 blocks；更新 revision。普通新增按块发；关联拆分、合并、替换放入一次 patch。后块先通过可先显示，前块修好插回正确位置。尾块续接沿用原 ID，恢复旧内容也提升 version。所有预览在最终完成前均可撤回。

首次连接在 SQLite 同一读事务获取快照及事件高水位，先发送 snapshot（其 id 即高水位），再读后续日志。客户端须原子保存预览与游标；断线带 Last-Event-ID 重连，按序补发并忽略已应用事件。游标无效返回 409；本地快照丢失或版本不匹配时不带游标重新获取 snapshot。终态事件或终态 snapshot 到达后 GET，校验摘要并采用最终 XML，主动关闭 SSE。终态且游标已是最新可返回 204，同样 GET。

默认 15 秒注释心跳、30 秒发送超时、250 毫秒轮询；同设备同文档最多两条连接。SSE 断开不取消任务，慢读者仅阻塞自己的连接，由发送超时清理；worker 不等待网络发送。API 目前单进程，连接数限制与上传恢复锁均按此部署，禁止启动多个 Uvicorn workers。

## 原子性与恢复

ServiceBlockStore 在草稿/正式状态同一次 fsync 检查点保存中追加 outbox。协调器将预览投影、递增版本、事件日志和消费收据写入同一个 SQLite FULL 同步事务，提交后才对 SSE 可见，再清空检查点 outbox。提交前崩溃重新消费；提交后、确认前崩溃由收据去重，不重复发事件。正式提交时 ID 转换与候选版本不直接决定流式版本。

worker 持有独占 worker/GPU/文档锁；正常 SIGTERM 进入清理，恢复保留在途尝试与预算。已落盘响应通过原 CLI 恢复逻辑复用。请求发出而响应未持久化时可能需要再次推理；不承诺模型 HTTP 请求本身 exactly-once，但预算、正式提交和事件应用不重复。API 重启独立于 GPU 进程。底层清理失败会阻止后续模型加载。

上传先鉴权与容量预留，再写临时文件、fsync、摘要和真实 JPEG 解码校验，原子移动文件并记录图片/OCR 队列，最后返回成功。API 启动持锁清理未完成上传和未登记文件。崩溃在文件移动和 SQL 提交之间时保留的孤儿图片会清理，客户端按相同身份重传。

## 配置、启动与运维

安装：`cd server && uv sync --locked --extra cuda --extra service`。CPU 合约测试只需 `--extra service`。配置样例 [service.example.toml](../server/deploy/service.example.toml)，`[service]` 每个字段可由 `C2D_字段大写` 环境变量覆盖。默认数据目录 `/home/zane/.local/share/capture2doc`；包含数据库、documents 下 uploads/ocr/pipeline、worker-runs、tmp。目录权限 0700，服务 umask 0077。

默认限制：JPEG 10 MiB、长边 1280、每文档 100 图、10 个未完成文档、目录 50 GiB、剩余 20 GiB。容量检查包含在途上传预留；当前容量门槛用于接收新图，推理产物也计入之后的检查，不是文件系统硬配额。图片不再次压缩。状态和诊断长期保留，手动清理。

```sh
capture2doc api --config ~/.config/capture2doc/service.toml
capture2doc worker --config ~/.config/capture2doc/service.toml
capture2doc token create --name phone --config ~/.config/capture2doc/service.toml
capture2doc token list --config ~/.config/capture2doc/service.toml
capture2doc token revoke TOKEN_ID --config ~/.config/capture2doc/service.toml
capture2doc storage inspect --config ~/.config/capture2doc/service.toml
capture2doc storage prune --document-id DOCUMENT_ID --config ~/.config/capture2doc/service.toml
# 确认列出的终态文档后加 --execute；清理前停止 API 和 worker。
```

部署文件在 [server/deploy](../server/deploy)。WSL 在配置及依赖就绪后运行 `sudo sh server/deploy/install-wsl-system.sh`，安装两个非 root systemd 服务及独立 nftables 规则。仅允许本机和 volcano `100.64.250.2` 访问 11209，不修改 SSH 或其他现有规则。模型继续使用已验证的本地回环 `10.255.255.254`。

volcano 将 [openresty.conf](../server/deploy/openresty.conf) 加入 http include，证书路径按现有受管理通配符证书调整，先 `openresty -t` 再 reload。SSE 关闭响应缓冲/缓存/压缩、read_timeout=120s；上传关闭请求缓冲，不在跳板机持久化照片。WSL 离线时返回网关错误，手机保留原图重试。使用 [NGINX proxy_buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering) 和 [sse-starlette](https://github.com/sysid/sse-starlette) 的心跳/发送超时能力。

模拟 Android 的客户端 [test_service_client.py](../server/scripts/test_service_client.py) 接收 `--base-url --token-file --output --images <按序图片...>`；支持已有 `--document-id`。令牌文件为 token create 输出 JSON 或原始 token，禁止提交。客户端持久化 snapshot/cursor，主动断线一次后重连，保存完整事件、首块延迟、投递延迟、最终 GET/XML 和摘要/节点一致性结果。输出目录应放 `.cache` 或数据根目录，避免把真实文档提交 Git。
