# FakeLive 从零技术栈与依赖决策

> 状态：Greenfield 技术基线草案  
> 适用范围：Windows 黑客松原型与可演进的正式产品  
> 更新时间：2026-07-23  
> 权威性：本文取代旧 `docs/TECH_STACK.md` 中“本地模型、离线优先、6657 RAG 是核心”等假设。新的核心模型是云端 `step-explore`。

## 1. 状态定义

本文不会把尚未跑过的组件写成既定事实。

| 状态 | 含义 | 是否可直接进入生产代码 |
| --- | --- | --- |
| **已选** | 产品/架构已经决定，除非新证据推翻 | 可以，但仍需按测试门槛验收 |
| **主候选** | 当前最匹配，必须先做最小 Spike | Spike 通过后才锁版本 |
| **备选** | 主候选失败时的替换路径 | 不与主候选同时堆进首版 |
| **待探针** | API、性能、兼容性或许可事实未闭合 | 不得据此承诺功能 |
| **不采用** | 与本产品核心边界不匹配 | 不进入核心依赖 |
| **仅参考** | 只学习交互/算法，不复制或链接代码 | 不进入分发物 |

## 2. 选型原则

- Windows 体验优先，跨平台不以牺牲透明 Overlay、设备选择和全局恢复为代价。
- 复用成熟的桌面、媒体、弹幕和测试能力，不手写已经被可靠库解决的核心渲染问题。
- Main、Renderer、UtilityProcess、Sidecar 之间依赖接口，不依赖具体模型或 ASR 实现。
- 云端协议只存在于 `StepExploreClient` 适配器内，业务层不直接拼 HTTP JSON。
- TypeScript 类型不能替代运行时校验；模型与 IPC 输入一律从 `unknown` 开始。
- 版本必须来自经过验证的 lockfile 与 ADR，不在方案文档里凭 README 或“最新版”锁死。
- 黑客松只安装解决核心闭环的依赖，不因为“以后可能有插件”提前引入插件框架。
- 许可证是选型门槛，不是发布前最后补表。

## 3. 技术栈总览

| 领域 | 技术 | 状态 | 作用 |
| --- | --- | --- | --- |
| 桌面运行时 | Electron | 已选 | Windows 窗口、托盘、快捷键、显示器、媒体授权、进程管理 |
| UI | Vue 3 Composition API | 已选 | Control、Overlay、Unlock Renderer |
| 语言 | TypeScript | 已选 | 主进程、Renderer、UtilityProcess 和共享契约 |
| 构建 | `electron-vite` + 固定兼容的 Vite | 主候选 | Main/Preload/多 Renderer 多入口构建 |
| Windows 打包 | `electron-builder` + NSIS x64 | 主候选 | 安装器、portable、签名与 extraResources |
| 包管理 | pnpm + lockfile | 已选 | 可重复安装与依赖审计 |
| 图标 | Lucide Vue | 已选 | 控制窗工具按钮 |
| 样式 | 原生 CSS | 已选 | 透明 Overlay、密度高的桌面控制台 |
| 云端多模态 | StepFun `step-explore` | 已选，能力待探针 | 画面理解、人群导演、独立人格决策 |
| POST SSE 解析 | Node `fetch` + `eventsource-parser` | 主候选 | 解析带 body/header 的 `/v1/messages` 流 |
| 弹幕引擎 | `bytedance/danmu.js` | 主候选 | DOM 容器上的滚动/固定弹幕与轨道 |
| 实时 ASR | StepFun `stepaudio-2.5-asr-stream` + `ws` | 已选，wire 待探针 | 实时主播转写、服务端 VAD、带 Bearer Header 的 WebSocket |
| 分段 ASR 备选 | StepFun `stepaudio-2.5-asr` + HTTP SSE | 已选为 Spike/回退候选 | 一次提交一段音频，不冒充双向实时流 |
| VAD | StepAudio `server_vad` | 已选为 P0 默认 | 不加载本地 ONNX；句界由云端事件确认 |
| 本地 ASR 回退 | `@huggingface/transformers` + Whisper ONNX | 可选后续候选 | 离线/隐私模式，不作为黑客松默认路径 |
| 运行时校验 | Zod 4 | 已选 | IPC、配置、SSE 和模型输出校验 |
| UI 状态 | Composition API；Pinia 仅在 P1 复杂化后引入 | Proposed | Renderer 投影，不存 Key/MediaStream/AbortController |
| 本地日志 | `electron-log` + 字段白名单脱敏 | 主候选 | Main 统一轮转，Renderer 无文件权限 |
| 密钥 | Electron `safeStorage` | 已选 | 加密开发者自有 StepFun Key；Main 按固定路径分别注入 Step-explore/StepAudio Adapter |
| 单元测试 | Vitest | 已选 | 状态机、调度、TTL、限流、轨道适配器 |
| 桌面 E2E | Playwright Electron | 已选 | Electron 工作流与 Renderer 验证 |
| 协议假服务 | Node `http` 本地 SSE Server | 已选 | 不依赖真实 Token 的错误与流式测试 |
| Windows 原生验证 | PowerShell/C# 小探针或测试辅助程序 | 主候选 | 真实点击穿透、进程/轨道和窗口证据 |

版本策略见第 13 节。本文会记录 2026-07-23 调研快照，但快照不是 lockfile；精确版本必须经过 Windows Spike 后锁定。

## 4. Step-explore 接入

### 4.1 已知且必须遵守的协议

以下内容来自项目方提供的内部 Step-explore 使用手册，是首版唯一权威协议：

| 项 | 值 |
| --- | --- |
| Base URL | `https://api.stepfun.com/v1` |
| Endpoint | `POST /v1/messages` |
| Model | `step-explore` |
| 格式 | **仅 Anthropic Messages API** |
| 认证 | `x-api-key: <YOUR_KEY>` |
| 版本头 | `anthropic-version: 2023-06-01` |
| Content-Type | `application/json` |
| System Prompt | JSON 顶层 `system` |
| Messages | `role` 仅 `user` / `assistant` |
| 输出上限 | `max_tokens` 必填 |
| 流式 | `stream: true`，SSE |
| 禁止 | `thinking` 参数 |
| 不支持 | `/v1/chat/completions`，会返回 400 |
| 限流 | 可能返回 HTTP 429，需要协调退避 |

已知文本请求的最小形态：

```http
POST https://api.stepfun.com/v1/messages
x-api-key: <redacted>
anthropic-version: 2023-06-01
content-type: application/json
```

```json
{
  "model": "step-explore",
  "max_tokens": 256,
  "system": "你是一个直播间观众。只返回约定的短结构。",
  "stream": true,
  "messages": [
    {
      "role": "user",
      "content": "根据当前上下文决定沉默或发一到两条短弹幕。"
    }
  ]
}
```

禁止做这些“兼容性猜测”：

- 不使用 OpenAI SDK 把请求发到 `/v1/chat/completions`。
- 不把 `system` 塞进 `messages` 冒充 system role。
- 不发送 `thinking`。
- 不假设普通 Anthropic SDK 的所有可选字段都被 Step-explore 支持。
- 不在图片探针前自行决定 image block 的 `source` 结构。

### 4.2 客户端实现选择

第一版优先使用 Electron/Node 自带的 `fetch`、`AbortController`，并用 [`eventsource-parser`](https://github.com/rexxars/eventsource-parser) 解析流，实现一个很薄的 `StepExploreClient`：

- 显式拼接已知请求字段。
- 支持首字节超时、总超时和主动取消。
- 增量解析 SSE event，而不是等待完整正文。
- 保留未知 event 的脱敏结构日志，便于能力探针。
- 把 HTTP 状态、错误体、`Retry-After` 和使用量字段转为领域错误。
- API Key 由 Main 解密后通过私有通道按会话注入 Persona Engine，只用于固定 StepFun origin 的请求；除用户当前输入所在的一次性 KeyEntry 外，永不进入 Renderer、日志或通用 IPC。

不使用浏览器原生 `EventSource`：它的构造器不能表达本接口所需的 POST body 与自定义认证头。`eventsource-parser` 只负责 SSE framing；Anthropic 风格 event 的字段仍由本地 Zod schema 校验。

在没有证据表明官方 SDK 与内部接口完全兼容前，不为了少写几十行代码引入 SDK。若后续证明 SDK 兼容，可在不改变 `StepExploreClient` 的情况下替换适配器。

### 4.3 API Gate 0：必须先跑的真实探针

在生产客户端、32 人格调度和 Prompt 优化之前，先保存脱敏探针证据：

1. 文本非流式或流式的实际成功结构。
2. SSE 的 event 名、data 结构、结束标记、usage 和错误事件。
3. `AbortController` 在首 token 前和流中取消的实际行为。
4. 真实图片 content block 结构。
5. 支持的 MIME、Base64/URL/File-ID 方式、单图与总字节限制。
6. 每次 2、4、10、20 张图片的成功率、首 token 和完整响应延迟。
7. 1、4、8、16、32 并发阶梯与真实 429 拐点。
8. 400、401/403、408/超时、429、5xx 和半截 SSE 错误体。

产品已决定保留 10/20 秒观察窗口、每次请求默认选择 2–4 个关键帧。探针用于校准分辨率、压缩、关键帧上限和延迟，不把“全量 10/20 张复制给每个人格”作为默认方案。

### 4.4 429 与并发

高频调用不是“给每个人格套一个 retry 库”：

- 全部人格共享一个 `GlobalRateController`。
- 遵守 `Retry-After`；没有时使用有上限指数退避和抖动。
- 429 后暂停环境梗，降低目标活跃人数，再进入半开探测。
- 每个人格最多一个在途请求和一个最新待处理观察。
- 真实完成度表述为“32 个稳定身份；真实并发验证到 N”，N 必须来自记录。

## 5. Electron、Vue 与 TypeScript

### 5.1 Electron

选择 Electron 的原因不是现有代码惯性，而是它直接提供核心桌面能力：

- `BrowserWindow`：透明、无边框、置顶、任务栏隐藏。
- `setIgnoreMouseEvents`：直播时鼠标穿透。
- `setContentProtection`：请求排除捕获的基础能力。
- `screen`：显示器 bounds、scale factor 与热插拔事件。
- `desktopCapturer` / display media 授权：屏幕和窗口来源。
- `globalShortcut` 与 `Tray`：Overlay 失控时的独立恢复。
- `utilityProcess`：隔离 Persona Engine。
- `MessageChannelMain` / `MessagePort`：传递大帧和 PCM。
- `safeStorage`：本机 API Key 加密。
- 一次性 KeyEntry Renderer：只负责当前输入，独立 preload、DevTools/导航/网络关闭，提交后销毁；Control 永不回读密钥。

必须打开：

- `contextIsolation: true`
- `sandbox: true`
- `nodeIntegration: false`
- 最小化的 preload `contextBridge`

Electron API 是否“被调用”不是完成证据：内容保护、点击穿透、多 DPI 和设备选择都要走真实 Windows 验收。

### 5.2 Vue 3

Vue 只承担 Renderer UI：

- Control 使用 Composition API 管理设备、配置和会话派生状态。
- Overlay 只消费 `BarrageCommand`，不持有业务引擎状态。
- Unlock 是单独入口与 preload，不与 Control 共享大组件树。
- 状态权威在 Main/Persona Engine，Renderer Store 只是投影。

黑客松不引入大型前端状态库。若单向事件流足够，用 Composition API 和只读快照即可；出现真实跨页复杂性后再写 ADR。

### 5.3 TypeScript

- 主进程、Renderer、UtilityProcess 使用同一领域契约包。
- 网络/IPC 边界运行时校验后再收窄类型。
- `sessionId`、`sessionGeneration`、`requestId` 与单调时间戳是所有异步消息的必备字段。
- 弹幕库的松散 JS API 包在本地 `BarrageEngineAdapter` 后，业务代码不直接依赖第三方对象。
- 不将 Step-explore 原始 wire types 扩散到 Scheduler 与 UI。

## 6. 屏幕与音频采集

| 能力 | 首版技术 | 状态 | 说明 |
| --- | --- | --- | --- |
| 来源枚举/授权 | Electron `desktopCapturer` + display media handler | 已选 | Main 决定可授权来源 |
| 屏幕视频 | `getDisplayMedia` / 授权后的 `MediaStream` | 已选 | 只获取用户明确选择的来源 |
| 麦克风 | `getUserMedia({ audio: { deviceId } })` | 已选 | 必须验证 track 的真实 `deviceId` |
| 代表帧 | Canvas / OffscreenCanvas + Worker | 已选 | 缩放、编码、变化摘要 |
| 音频处理 | Web Audio + `AudioWorklet` | 已选 | 音量、重采样、PCM 分块 |
| 大对象传输 | `MessagePort` + transferable `ArrayBuffer` | 已选 | Main 不复制媒体 |
| 扬声器测试 | `HTMLMediaElement.setSinkId` 或 Web Audio 输出能力 | 待 Electron 实机探针 | 设备选择不等于系统音频采集 |
| 系统音频理解 | Electron loopback 或其他 Windows 路径 | 延后 | 与输出设备选择是两个能力 |

采集 Spike 要验证：

- 窗口/显示器切换、来源结束事件和重授权。
- 100%、125%、150% DPI 下的图像尺寸和 Overlay 对齐。
- 控制窗、Unlock 和弹幕是否进入自身截图。
- 每秒代表帧编码对游戏 FPS、CPU/GPU 和内存的影响。
- 暂停/停止后 tracks 的 `readyState === 'ended'`。

## 7. 弹幕引擎研究

### 7.1 选型结论

主候选是 [ByteDance `danmu.js`](https://github.com/bytedance/danmu.js)，但在 Spike 通过前仍不写成不可替换依赖。

其官方仓库说明它：

- 可用于任意 DOM 容器，不要求必须存在视频播放器。
- 支持滚动、顶部、底部、样式、显示区域和虚拟轨道。
- 支持碰撞避免、速度/时长、透明度、暂停、继续、停止和按类型显示/隐藏。
- 使用 MIT 许可证。

它与 FakeLive 的“实时透明容器”直接匹配。主要风险是 JavaScript API 较松散，因此必须使用本地 TypeScript Adapter 和运行时归一化。

### 7.2 候选对比

| 方案 | 许可 | 优点 | 风险 | 结论 |
| --- | --- | --- | --- | --- |
| [`bytedance/danmu.js`](https://github.com/bytedance/danmu.js) | MIT | 任意 DOM、live 用法、scroll/top/bottom、区域、轨道、样式和播放控制与需求高度重合 | 类型契约弱；需要实测高密度、resize、销毁和 Electron 透明窗 | **主候选** |
| [`imtaotao/danmu`](https://github.com/imtaotao/danmu) | MIT | TypeScript 为主，强调碰撞检测和高度自定义，适合更强过滤/扩展 | pre-1.0 API 稳定性与迁移成本需评估；文档/包能力需实测 | **备选 A** |
| [`weizhenye/Danmaku`](https://github.com/weizhenye/Danmaku) | MIT | 支持 DOM 与 Canvas、live mode、RTL/top/bottom、resize、clear；Canvas 可作为高密度路径 | API 与维护节奏、Canvas 字体/命中区、Electron 高 DPI 需实测 | **备选 B** |
| [DPlayer](https://github.com/DIYgod/DPlayer) | MIT | 成熟播放器与弹幕体验 | 核心是视频播放器，带入 media timeline、控制栏与播放器生命周期 | **不采用为核心** |
| [ArtPlayer](https://github.com/zhw2590582/ArtPlayer) | MIT | 插件丰富、播放器交互成熟 | 同样以视频播放器为宿主，不符合独立桌面 Overlay | **不采用为核心** |
| [`pakku.js`](https://github.com/xmcp/pakku.js) | GPL-3.0 | 合并刷屏、过滤、密度分析和 B 站体验有参考价值 | GPL-3.0 会影响分发与衍生代码义务 | **仅参考，不复制/嵌入** |

### 7.3 为什么不手写完整弹幕引擎

轨道碰撞不是简单地随机选一个 `top`：

- 同轨前一条弹幕的尾部必须安全离开入口。
- 后发快弹幕不能在屏幕中追上慢弹幕。
- 字体、缩放、描边和长文本会改变真实宽度。
- resize、DPI 和保护区会使轨道容量变化。
- 顶/底固定弹幕与滚动弹幕使用不同占用规则。

首版应复用成熟引擎，再在 Adapter 上补 TTL、保护区、去重和产品级命令，不把黑客松耗在重写排版算法。

### 7.4 弹幕 Spike 验收

用同一组 30/60/120 条短弹幕场景比较主候选与备选：

- 透明窗口中的平均/峰值 CPU、内存和 Overlay 帧间隔。
- 正常密度下不可读几何碰撞为零。
- 更快后发弹幕不追尾。
- `pause/play/clear/hide/show/destroy` 后无残留节点和定时器。
- 100%、125%、150% DPI 与窗口 resize 后轨道正确。
- 字号、颜色、描边、透明度、速度、显示区域可见生效。
- 保护区可以由 Adapter 排除，而不必 fork 第三方源码。

通过后才在 `pnpm-lock.yaml` 锁定精确版本并写 ADR。若必须 fork，先确认维护成本和许可证，再使用仓库固定 commit。

## 8. StepAudio ASR 与 VAD

### 8.1 当前结论

供应商和模型已经确定为 StepFun StepAudio 2.5。直播主路径使用官方 [双向流式 WebSocket API](https://platform.stepfun.com/docs/zh/api-reference/audio/asr-stream)：

- 模型 `stepaudio-2.5-asr-stream`。
- `wss://api.stepfun.com/v1/realtime/asr/stream`。
- `Authorization: Bearer $STEPFUN_API_KEY`，与 Step-explore 共用开发者自有 StepFun Key。
- `session.update` 后持续发送 Base64 `input_audio_buffer.append`。
- 默认 `server_vad`；未启用时由客户端发送 `input_audio_buffer.commit`。
- delta 的 `text` 是累计全文并可能纠错，必须整体替换；最终以 `completed.transcript` 入观察上下文。

WebSocket 客户端使用纯 JS 的 [`ws`](https://github.com/websockets/ws)，运行在受信任的 Node/UtilityProcess 边界，而不是 Browser Renderer。原因是官方鉴权要求握手 `Authorization` Header，浏览器原生 `WebSocket` API 不能设置任意 Header。Adapter 固定 origin，关闭不必要的扩展，并对消息大小、心跳和事件 Schema 做硬限制。

官方页把 `stepaudio-2.5-asr` 定义为同步/异步/一次性 SSE 模型，把 `stepaudio-2.5-asr-stream` 定义为实时双向流模型。FakeLive 不用一次性 SSE 冒充实时上传。2026-07-23 官方页面显示两者价格分别为 0.15 元/小时与 1.2 元/小时；价格属于可变运营信息，接入前重新确认。

### 8.2 候选方向

| 方向 | 候选例 | 优点 | 代价/风险 |
| --- | --- | --- | --- |
| StepAudio WebSocket | `stepaudio-2.5-asr-stream` | P0 主路径；实时分片、server VAD、累计纠错、最终 transcript | 需持续上传音频；心跳、会话上限和帧格式待探针 |
| StepAudio HTTP SSE | `stepaudio-2.5-asr` | 同一供应商；一次性提交片段；实现简单 | 不是双向实时流；需客户端先分段；不支持二遍纠错参数 |
| 浏览器/WASM Whisper | [`@huggingface/transformers`](https://huggingface.co/docs/transformers.js/en/index) + Whisper ONNX | 可选离线/隐私路径，避免 Electron ABI | 模型体积、CPU/内存和中文准确率；不阻塞 P0 |
| 本地 ONNX/原生 Sidecar | sherpa-onnx、SenseVoice、whisper.cpp 等 | 可离线；部署形态可替换 | 原生打包、模型分发和游戏资源竞争；后续再评估 |

P0 不引入 `vad-web`。默认使用 StepAudio `server_vad`，初始参数参考官方示例 `silence_duration_ms=800`、`threshold=0.5`，真实值用游戏噪声样本校准。服务端 VAD 意味着麦克风开启期间音频分片正在上传，UI 不能把“当前无人说话”显示成“未上传”。本地 VAD 仅作为未来节流/隐私增强项。

官方文档存在一处必须探针关闭的歧义：会话格式支持 `pcm/ogg` 且示例配置为 `pcm_s16le/16k/16bit/mono`，但 `input_audio_buffer.append.audio` 的字段说明又写“WAV 格式”。Adapter 要把 wire 编码做成窄配置点，在真实请求成功前不得写死裸 PCM 假设。

### 8.3 ASR 决策门槛

使用同一批经同意的中文游戏麦克风样本测试 StepAudio 主路径；本地候选只在需要离线/隐私回退时进入同一套测试：

- 开口到稳定转写的 P50/P95。
- 人名、游戏术语、口语和短句准确率。
- 背景枪声、键盘声、扬声器串音下的误识别。
- server VAD 漏句率、误触发、空转上传比例与一句话切分边界。
- WebSocket 建连、心跳、空闲超时、断线重连和会话最长持续时间。
- `delta.text` 前文纠错、`stash` 和 `completed.transcript` 的状态机正确性。
- PCM/WAV 分块的真实 wire 兼容性、单帧安全大小和端到端网络带宽。
- 客户端 CPU、内存和网络占用；本地回退另测模型体积、冷启动与 Windows 打包。
- 停播取消后是否仍有音频/识别任务残留。

完整体验要求主播语音真实进入人格上下文；仅画面降级可以运行，但不能算语音链路通过。

## 9. 运行时校验与数据格式

需要运行时校验的边界：

- Control 提交的 `LiveConfig`。
- Renderer 与 Main 的 IPC。
- Capture 的 Frame/Audio 元数据。
- StepAudio WebSocket 客户端与服务端事件。
- Step-explore SSE event 与最终人格/导演决定。
- Persona 配置文件和设置迁移。

主候选采用 **Zod 4**：同一 schema 可推导 TypeScript 类型，错误信息适合 IPC 和模型协议诊断，桌面应用的包体也不值得为此牺牲可维护性。若 Overlay bundle 测量证明 Zod 成为真实热点，只在 Overlay 边界改为预归一化领域消息，不在整个项目同时维护第二套 schema 系统。Valibot 保留为包体敏感时的备选，自有校验器只允许用于极窄且稳定的内部数值热路径。

无论选择哪个库，模型输出都先解析为 `unknown`，限制：

- 单条弹幕字符数。
- 最多 0–2 条。
- 枚举、数值范围和数组长度。
- 不接受模型指定任意 CSS、HTML、URL、文件路径、IPC channel 或系统命令。

### 9.1 内容安全实现

P0 采用本地 `ContentSafetyPipeline`，不新增未经批准的云审核接收方：

- Zod 结构/长度/字符边界。
- Unicode 规范化和版本化 `hard-deny-v1` 高风险规则包。
- 用户屏蔽词、攻击性等级、单人格和单主题频率上限。
- 过滤异常 fail-closed，候选直接丢弃。
- Vitest 对正常、边界、规避写法和提示注入 fixture 做回归。

生成模型不能审核自己的最终输出。公开发布前，再以独立 ADR 比较本地分类器和经批准的审核 API；任何新增云审核都会改变数据流与同意文案。完整分类和失败策略见 [07_SECURITY_PRIVACY_CONTENT.md](./07_SECURITY_PRIVACY_CONTENT.md#101-p0-的本地执行方案)。

## 10. 测试技术栈

### 10.1 单元测试：Vitest

覆盖纯逻辑：

- 32 人格状态隔离与乱序完成。
- one-in-flight + latest-pending。
- Director 冷却、burst 回落与失败回退。
- 全局 429 状态机、`Retry-After`、抖动和半开。
- TTL、优先级、精确/近似去重、屏蔽词。
- `sessionGeneration` 过滤旧消息。
- Barrage Adapter 的命令映射和销毁。

使用 fake clock 和 seeded RNG，避免把“等几秒”写进测试。

### 10.2 协议集成：本地 Anthropic SSE Fake Server

用 Node 内置 `http` 即可先构造：

- 正常多 chunk。
- 慢首 token。
- 空决定和两句决定。
- 429 + `Retry-After`。
- 连接中断、半截 JSON、未知 event、5xx。
- 客户端取消后服务器确认连接关闭。

Fake Server 验证协议状态机，不证明真实 Step-explore 图片或配额；真实能力仍由 Gate 0 证明。

### 10.3 Electron E2E：Playwright

覆盖：

- Onboarding、来源/设备选择、开始、暂停、清屏、恢复、停播。
- Control、Overlay、Unlock 窗口数量和生命周期。
- Overlay DOM/Canvas 几何、样式、轨道和 resize。
- 模拟来源丢失、ASR 故障、Director 故障和 429。

Playwright locator 能点击不等于 Windows 原生穿透。原生验收需要额外探针：

- Overlay 中心点下方测试应用真实收到点击。
- 全局快捷键和托盘在 Overlay 失控时可恢复。
- Windows 捕获结果中不存在 Overlay 自身。

### 10.4 真实 API 与桌面验证

- 真实 API Probe 独立脚本，凭据从环境变量或 `safeStorage` 注入。
- 1/4/8/16/32 阶梯负载单独运行，每一级冷却并记录 P50/P95。
- 真实桌面 E2E 至少跑 4 个独立人格接收真实截图和真实主播转写。
- 保存脱敏 `run-manifest.json`、`events.jsonl`、并发表和延迟表。

## 11. 日志、指标与诊断

第一版不引入远程遥测平台，优先由 Main 使用 [`electron-log`](https://www.npmjs.com/package/electron-log) 统一落盘和轮转；需要评测的高频事件另写受控 JSONL：

- Renderer 只能发送经过 schema 校验的日志事件，不能直接取得文件权限。
- 字段白名单，不做“把整个 request 对象 stringify”。
- 默认不记录截图、音频、完整转写、Prompt、API Key 和模型完整原文。
- 记录 `runId`、`sessionId`、personaId、requestId、状态码、字节数、耗时和 drop reason。
- 日志按大小/日期轮转，UI 提供清除。

正式版是否引入崩溃上报/遥测需要单独隐私 ADR 和默认关闭策略，黑客松不作为核心。

## 12. 构建与分发

### 12.1 开发与构建

- `electron-vite` 分别构建 Main、Preload、Control、Capture、Overlay 和 Unlock 入口。
- `electron-builder` 管理 Windows x64 package；黑客松可生成 unsigned portable/NSIS，公开分发必须配置 Authenticode 或 Azure Trusted Signing。
- Persona Engine 作为 UtilityProcess 入口随应用构建。
- 原生 ASR runtime、模型和 DLL 若被采用，放在 ASAR 外。
- API Key 和用户设置永不打进安装包。NSIS 若提供“同时删除用户数据/凭据”选项，必须明确默认值并做安装-存钥-卸载-磁盘检查；portable 构建没有卸载器，不能承诺删除 exe 会自动清理 `userData`。

仓库当前若仍使用 Electron Forge，可把它当成“复用旧脚手架、减少黑客松迁移”的备选，而不是 greenfield 默认。Forge 官方 Vite 插件仍需按其 experimental 风险验收；不要同时维护 Forge 和 electron-builder 两套正式发布配置。

### 12.2 安全构建

- 启用 Electron Fuses，关闭不需要的运行能力。
- 发布物生成 SHA-256，尽可能进行 Windows 代码签名。
- CI 从干净环境安装 frozen lockfile。
- 不在 CI 日志或测试 artifact 中回显 API Key。
- 真实 API 测试与普通 PR CI 分离，只在受控环境运行。

### 12.3 供应链

- 依赖必须有明确来源、维护状态和许可证。
- 生成第三方 NOTICE/SBOM；保留直接和传递依赖许可证。
- 自动漏洞扫描只是信号，升级前仍跑 Electron 打包、Overlay、设备和 SSE 回归。
- 不从 CDN 在运行时加载弹幕库、Vue、字体或任意脚本。
- 不下载未校验的 ASR 二进制；外部 runtime 和模型记录哈希。

## 13. 版本策略

### 13.1 2026-07-23 调研快照

下表用于建立可复现实验起点，不表示这些版本已经在本项目打包通过：

| 组件 | 调研快照 | 备注 |
| --- | --- | --- |
| Electron | `43.2.0` | Chromium 150 / Node 24 系列；只面向 `win32-x64` |
| Vue | `3.5.40` | Composition API |
| TypeScript | `7.0.2` | 仍需验证 Vue/Vite/测试依赖兼容性 |
| electron-vite | `5.0.0` | peer 支持 Vite 5/6/7 |
| Vite | `7.3.6` | 暂钉 Vite 7，不直接升 Vite 8 |
| electron-builder | `26.15.3` | NSIS x64 主候选 |
| danmu.js | `1.2.1` | 无内置 TS declarations，必须包 Adapter |
| eventsource-parser | `3.1.0` | 仅解析 SSE framing |
| ws | `8.21.1` | MIT；StepAudio WebSocket 握手需自定义 Authorization Header |
| Zod | `4.4.3` | 运行时边界 schema |
| @ricky0123/vad-web | `0.0.30` | 0.x，资源路径和降级必测 |
| @huggingface/transformers | `4.2.0` | Whisper 模型及模型许可证另行锁定 |
| Vitest | `4.1.10` | 纯逻辑和协议层 |
| Playwright | `1.61.1` | Electron API 仍按 experimental 对待 |
| electron-log | `5.4.4` | 强制字段白名单和脱敏 |

上游版本会变化；实现时必须重新读取 registry、官方兼容表和安全公告。尤其不能让 `electron-vite@5` 无约束解析到不兼容的 Vite 8。

调研来源：[Electron releases](https://releases.electronjs.org/)、[Electron npm](https://registry.npmjs.org/electron/latest)、[Vue npm](https://registry.npmjs.org/vue/latest)、[TypeScript npm](https://registry.npmjs.org/typescript/latest)、[electron-vite npm](https://registry.npmjs.org/electron-vite/latest)、[Vite npm](https://registry.npmjs.org/vite/latest)、[electron-builder npm](https://registry.npmjs.org/electron-builder/latest)、[danmu.js npm](https://registry.npmjs.org/danmu.js/latest)、[eventsource-parser npm](https://registry.npmjs.org/eventsource-parser/latest)、[ws npm](https://registry.npmjs.org/ws/latest)、[Zod npm](https://registry.npmjs.org/zod/latest)。

### 13.2 锁定流程

真正的版本选择流程：

1. 在 Spike 分支安装一个候选的精确版本。
2. 记录 Node/pnpm/Electron/Chromium/Windows 构建环境。
3. 跑 typecheck、unit、package、Electron E2E、干净机 smoke 和性能基准。
4. 在 ADR 中记录为什么选择该版本及已知风险。
5. 将精确解析结果提交到 `pnpm-lock.yaml`。
6. 发布分支禁止无评估的浮动大版本升级。

版本号可以在 `package.json` 使用项目既有策略，但可重复构建以 lockfile 为准。文档中出现的上游 release 只能作为调研快照，不能替代锁定证据。

## 14. 许可证策略

| 类别 | 处理原则 |
| --- | --- |
| MIT / BSD / Apache-2.0 | 通常可进入候选，但仍保留 NOTICE、版权和传递依赖审计 |
| LGPL | 需要确认动态链接、修改和重新链接义务后再决定 |
| GPL / AGPL | 默认不进入闭源或许可证未定的核心分发物；必须经过明确产品与法律决策 |
| 无许可证/来源不明 | 不使用 |
| 只看算法思想 | 可以阅读公开说明，但不得复制受限代码或做规避许可证的改写 |

`pakku.js` 是明确的 GPL-3.0 项目，因此当前只允许做体验和问题空间参考。不要复制其实现、打包其代码或将其作为运行时依赖。

## 15. 不采用与延后

### 15.1 不采用

- **OpenAI Chat Completions 兼容层**：Step-explore 明确不支持。
- **DPlayer / ArtPlayer 作为 Overlay 核心**：播放器生命周期与透明桌面容器不匹配。
- **在 Main 中跑 32 路 SSE/图像编码/ASR**：会把模型故障和 UI 生命周期绑定。
- **手写完整弹幕轨道引擎**：黑客松收益低，已有 MIT 候选。
- **GPL 弹幕代码直接进入核心**：产品许可证尚未做出接受该义务的决策。
- **任意插件 Host/市场**：不是核心闭环，权限面过大。
- **真实 Bilibili/Huya 连接器**：黑客松明确不做。

### 15.2 延后

- 系统音频理解。
- 手环专用 GATT/串口/厂商 SDK。
- 多显示器同时覆盖、独占全屏游戏和目标窗口自动跟随。
- `+1` 波的通用插件形式。
- Canvas 高密度渲染切换，除非 DOM Spike 失败。
- 云端遥测、公共 Persona 市场和远程内容策略服务。

## 16. 技术 ADR 清单

| ADR | 决策 | 状态 | 关闭条件 |
| --- | --- | --- | --- |
| ADR-TECH-001 | Electron + Vue + TypeScript 作为桌面基础 | 已选 | Windows 核心能力验证通过 |
| ADR-TECH-002 | Step-explore 使用原生 Anthropic Messages 适配器 | 已选 | 文本/图片/SSE/错误探针通过 |
| ADR-TECH-003 | 不用 OpenAI Chat Completions 兼容层 | 已选 | 协议手册已明确 |
| ADR-TECH-004 | `danmu.js` 为主候选 | 主候选 | 高密度、DPI、销毁、保护区 Spike 通过 |
| ADR-TECH-005 | `imtaotao/danmu` 为 TypeScript 备选 | 备选 | 主候选不满足时比较 |
| ADR-TECH-006 | `weizhenye/Danmaku` 为 DOM/Canvas 备选 | 备选 | DOM 性能不足时比较 |
| ADR-TECH-007 | StepAudio 2.5 已选；直播使用 `stepaudio-2.5-asr-stream` WebSocket | 已选，wire 待探针 | 延迟、准确率、帧格式、心跳、限流和停止语义完成 |
| ADR-TECH-008 | 10/20 秒窗口默认选择 2–4 个关键帧 | 已选，参数待校准 | 真实图像/大小/延迟结果完成 |
| ADR-TECH-009 | Zod 4 作为运行时校验库 | 已选 | 主/Utility/Renderer 共用 schema 与 bundle smoke 通过 |
| ADR-TECH-010 | 扬声器输出路由 | 待探针 | Electron 实机 `setSinkId` 验证完成 |
| ADR-TECH-011 | pakku.js 只作参考 | 已选 | 除非产品明确接受 GPL-3.0 义务 |
| ADR-TECH-012 | electron-vite + Vite 7 + electron-builder | 主候选 | Windows x64 packaged smoke 和签名路径通过 |
| ADR-TECH-013 | fetch + eventsource-parser 处理 POST SSE | 主候选 | Fake/真实 SSE、断流和取消夹具通过 |
| ADR-TECH-014 | P0 使用 StepAudio server VAD；vad-web/Transformers.js 仅作后续本地增强或回退 | 已选/后续备选 | server VAD 噪声样本通过；本地路径按需验证 |
| ADR-TECH-015 | 本地 ContentSafetyPipeline + hard-deny-v1 | 已选用于 P0 | 红线/正常对抗 fixture、fail-closed 和脱敏日志通过 |
| ADR-TECH-016 | `ws` 运行于受信任 Node 边界以携带 StepAudio Bearer Header | 已选，待 Spike | 握手、心跳、取消、打包和秘密扫描通过 |

## 17. 上游参考

桌面与进程：

- [Electron BrowserWindow](https://www.electronjs.org/docs/latest/api/browser-window)
- [Electron utilityProcess](https://www.electronjs.org/docs/latest/api/utility-process)
- [Electron MessagePortMain](https://www.electronjs.org/docs/latest/api/message-port-main)
- [Electron desktopCapturer](https://www.electronjs.org/docs/latest/api/desktop-capturer)
- [Electron safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage)
- [Electron security checklist](https://www.electronjs.org/docs/latest/tutorial/security)
- [electron-vite](https://electron-vite.org/)
- [electron-builder](https://www.electron.build/docs/)

协议、语音与校验：

- [eventsource-parser](https://github.com/rexxars/eventsource-parser)
- [Zod](https://zod.dev/)
- [vad-web](https://github.com/ricky0123/vad)
- [Transformers.js](https://huggingface.co/docs/transformers.js/en/index)
- [Playwright Electron](https://playwright.dev/docs/api/class-electron)

弹幕：

- [ByteDance danmu.js](https://github.com/bytedance/danmu.js)
- [imtaotao/danmu](https://github.com/imtaotao/danmu)
- [weizhenye/Danmaku](https://github.com/weizhenye/Danmaku)
- [DPlayer](https://github.com/DIYgod/DPlayer)
- [ArtPlayer](https://github.com/zhw2590582/ArtPlayer)
- [pakku.js](https://github.com/xmcp/pakku.js)

架构边界见 [05_ARCHITECTURE.md](./05_ARCHITECTURE.md)。功能与验收中出现的“待探针”必须在决策日志中保留，不得在演示文案中被悄悄改写成“已支持”。
