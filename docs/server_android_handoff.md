# 服务端 / Android 联合开发交接

## 服务端已交付的契约

HTTPS 443 → OpenResty → SD-WAN → 后端 TCP 11209。API 与 GPU worker 独立运行，Paddle → Qwen 单请求轮换；SSE 连接断开不取消任务。环境地址、系统账号及设备令牌通过机器本地私密配置提供，禁止写入本文、代码常量、提交或 PR。

| 接口 | 安卓适配边界 |
| --- | --- |
| POST `/v1/documents` | clientTaskId 与 Idempotency-Key 相同，重试复用文档 |
| PUT `/v1/documents/{id}/pages/{imageId}` | 独立 JPEG，长边≤1280、≤10 MiB；X-Content-SHA256；同 ID 不同摘要 409 |
| POST `/v1/documents/{id}/finalize` | pageIds 冻结最终顺序；幂等键为 clientTaskId + `-finalize` |
| GET `/v1/documents/{id}` | status、schemaVersion、title、wordCount、needsReview=false、完整 c2dXml 与 sha256；不返回 blocks 数组 |
| GET `/v1/documents/{id}/events` | Bearer 鉴权的 SSE；snapshot、progress、blocks.patch、completed、failed |

SSE blockId 是稳定修改身份，不能用展示编号替代。patch 必须原子校验 baseRevision、锚点和连续删除区间，再插入完整块并更新 revision。预览与 Last-Event-ID 原子保存；游标或版本不匹配时重新取 snapshot。收到终态后完整 GET，校验原始 XML UTF-8 字节摘要，再采用最终文档。

OCR 只作参考，等待所需 OCR 任务终态，不要求完整文本。局部修复最多五次，耗尽后局部兜底或保留内部缺失记录，继续后图。所有图片处理结束且结果保存成功即 COMPLETED；没有质量复核门槛。执行、存储和 GPU 清理故障仍为 FAILED。

## 已完成的服务端验证

本地与部署机均完成 415 项回归；公网三图与有序八图完成，八图 57 个块，预览与最终 XML/摘要一致，GPU 清理通过。八图投递延迟中位数约 276 ms，P95 约 980 ms，2 个事件略超 1 秒目标；该性能边界尚不能记为全部满足。详见 [脱敏验收摘要](server_service_validation.md)。

本阶段新增 7 项部署模板检查，本地完整回归为 422 项通过；未重复运行真实模型八图测试。

独立 JPEG 载荷是下一轮联调重点：已测原始动态照片低分辨率但含附加视频，上传耗时明显。安卓侧需核对实际派生文件字节数，不仅核对像素尺寸。

## 分支协作状态与本阶段边界

用户确认安卓主体测试已完成，开发接近完成；服务端与安卓本地 checkout 当前均在 `main`。安卓目录存在未提交的文档渲染、连接设置和任务同步更改。服务端未重新执行安卓测试；同步以两个实际工作目录的 `main` 为准，不切换至旧的 `dev_android` 分支。

服务端不修改安卓功能代码、不代替安卓测试、不切换或重置有未提交工作的 checkout。同步前重新核对远端头和本地改动；可快进且不会覆盖现有文件时同步共享提交，遇到分叉则通过合并请求协调。每个服务端阶段记录完成事项、契约变化、验证范围与未决事项，提交后同步远端，供安卓分支消费。

本阶段接口字段和错误语义保持不变；部署模板移除了机器地址、账号和路径，增加本地渲染入口，默认监听回环地址。已运行服务继续使用机器本地私密配置。完整服务协议见 [后端说明](server_service.md)，机器可读契约见 [OpenAPI](server_openapi.json)。
