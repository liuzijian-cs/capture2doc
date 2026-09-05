# Android 流式文档验证记录

日期：2026-09-05。所有实现、资源、样例与测试限定在 `android/`，服务端与仓库根目录文档未修改。

## 服务端主线合并补记

2026-09-05：将服务端 `0c416d2` 合并到安卓 `3f859a5`，无冲突，保留安卓实现；合并带入服务端已有部署模板与根目录文档，不另改服务协议。

- 合并后 64 项 Android 单元测试强制重跑通过；Debug 构建和 Lint 成功（复用未变更任务的缓存）。
- 服务端回归已结束：418 项通过，4 项网络测试因沙箱禁止绑定本地端口而在初始化阶段报错，未重跑，不记为全部通过。
- 本轮真机任务因设备不可用未执行任何用例。随后按用户要求暂停真机测试及真实服务联调，不追加或重跑测试；撤回本轮临时新增、未经设备验证的 JPEG 尾部数据测试。
- 下文“已验证”为安卓实现提交时的历史结果，不代表本轮新增真机验收。真实服务闭环及目标 App 粘贴仍待后续按需验证，不阻塞本次合并。

## 已验证

- 64 项 JVM 单元测试通过：鉴权与幂等键、JPEG 摘要确认、公共 JSON/旧返回字段拒绝、SSE 100 个连续事件无丢失、204/409、块插入/拆分/合并/替换/撤回、重复事件、终态防回退、原始 UTF-8 摘要、空结果、全标签渲染、七色映射、安全链接和非法 XML。
- Debug APK、设备测试 APK 构建成功。Debug Lint 通过，无错误；仍有工具链升级、原有资源与应用配置相关警告。没有为了消除版本提示升级既有工具链。
- 最终一轮 32 项设备回归全部通过，覆盖首页、本地任务/页序、上传重试幂等、预览原子恢复、旧 session 迟到事件、终态 snapshot/204 后 GET、409 游标恢复、错误摘要保留预览、Keystore 密文恢复、实际 Parcel 大小限制，以及后台冷启动恢复最终缓存且不发起 GET。
- 阅读页体验调整后，13 项核心设备测试再次全部通过：本地 KaTeX/公式源码回退、混合样式与合并单元格、已有 DOM 保留、向上阅读保持位置/查看最新、后台停止动效、已完成文档从顶部打开及缓存/剪贴板检查。
- 测试前台宿主调整后，11 项缓存、终态补取、Keystore 和剪贴板设备测试再次全部通过。平台 Activity 切换停顿仍然存在，见下文。
- `https://c2d.liu.zj.cn/openapi.json` 匿名只读访问返回 HTTP 401，确认服务入口可达且要求鉴权；这不是已完成真实服务联调的证明。
- 最终 Debug APK 已保留数据覆盖安装并成功冷启动。手机连接设置页可用，令牌保持遮蔽；点击“测试连接”后当前填写的令牌收到 401，页面正确提示更新连接设置。没有读取令牌明文或创建测试服务文档。
- `git diff --check` 通过。构建产物、设备密文配置、临时截图与运行日志不纳入源码提交。

第一次设备测试发现 `ClipData.newHtmlText` 未声明 `text/plain` MIME，虽保留了纯文本 item，但双类型断言失败。已保留其富文本 item，并显式声明 HTML 与纯文本；后续复测通过。测试未通过时未记录为成功。

最终回归期间发现设备将后台测试进程冻结，日志中长时间 GC 包含了冻结时间；生成线程诊断、解除冻结后测试通过。缓存/剪贴板测试增加了独立的前台 Compose 测试界面，超长载荷用例随后快速完成；Activity 切换期间仍观察到平台停顿。没有修改系统冻结或动画设置，不将这些耗时用作大文档性能结论。

## 未完成的真实验收

- 尚无已验证可用的设备令牌，当前设置页的只读连接测试返回 401，未完成真实“拍摄 → 上传 → SSE → 最终校验 → 复制”闭环。应用内更新有效令牌后才能进行；不要把令牌提交到仓库或发进日志。
- 未在飞书与思源的文档编辑区实际粘贴本次载荷。标准 HTML、纯文本回退、颜色/链接/表格载荷通过测试，不等于两款目标 App 一定保留所有样式；原生公式转换不在本轮承诺范围。
- 未完整人工验收相机点击完成后的导航、菜单启用时机，以及真实网络切断/恢复和 OS 杀进程后重进的端到端组合。已有独立的页序提交、后台重试、数据重建、DOM 滚动及生命周期自动测试，不能将这些称为同一条真实链路全部通过。
- 未执行全量已有相机硬件测试或 20 页性能压力测试。本轮运行与改动相关的设备测试类。

## 复现

在 `android/` 目录、兼容 JDK 下运行：

```sh
./gradlew :app:testDebugUnitTest :app:assembleDebug :app:lintDebug
./gradlew :app:connectedDebugAndroidTest \
  '-Pandroid.testInstrumentationRunnerArguments.class=io.github.liuzijiancs.capture2doc.DocumentWebViewTest,io.github.liuzijiancs.capture2doc.data.document.DocumentContentInstrumentedTest,io.github.liuzijiancs.capture2doc.data.task.TaskRepositoryInstrumentedTest,io.github.liuzijiancs.capture2doc.TaskHomeContentTest'
```

构建最初遇到依赖下载 TLS 中断。使用已有 Android Studio JBR、现有网络代理并重试后成功，没有改项目仓库配置或清除缓存。测试时设备需解锁并保持空闲。

报告与产物（由构建生成，不纳入源码）：

- `app/build/reports/tests/testDebugUnitTest/index.html`
- `app/build/reports/lint-results-debug.html`
- `app/build/reports/androidTests/connected/debug/index.html`
- `app/build/outputs/apk/debug/app-debug.apk`

固定样例：`app/src/test/resources/all-tags.xml`，JVM 与 Android 测试共用。设备信息、令牌和业务文档不写入开发记录。
