# 决策与开放问题

> 状态：Living Document
>
> 这里只记录会改变产品边界或系统结构的决定。具体依赖版本和普通实现细节由代码与 lockfile 管理。

## 1. 状态说明

| 状态 | 含义 |
| --- | --- |
| `Accepted` | 当前实现应遵守的决定 |
| `Proposed` | 推荐方向，验证前仍可更改 |
| `Open` | 尚无足够信息，不应在其他文档中写死 |
| `Superseded` | 曾经采用但已被新方向取代 |

## 2. 已接受决定

### D-001：产品是本地 AI 虚拟直播间

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：用户在本机获得由 AI 观众组成的模拟直播体验，产品不向真实观众推流，也不创建公开直播间。
- 影响：真实平台账号、礼物、在线人数和真人互动不是核心链路。

### D-002：Windows 和 macOS 是目标平台

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：产品从架构上同时面向 Windows 和 macOS。
- 影响：系统权限、Overlay、打包和测试必须保留平台适配边界，不能把 Windows 专有行为写进领域合同。

### D-003：桌面端使用 Electron

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：Electron + TypeScript 承担桌面端；Renderer UI 使用 React。Electron 负责系统媒体采集、桌面 Overlay 和桌面生命周期。
- 影响：Renderer 使用最小权限，敏感能力由 Main Process 管理。

### D-004：本地后端使用 FastAPI

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：FastAPI 承担 ASR Provider 调度、近期上下文、AI 编排、Provider 接入和弹幕处理；Python 项目使用 `uv` 管理依赖和锁文件。
- 影响：AI 逻辑不放入 Electron Renderer；FastAPI 是随桌面应用运行的本机组件，不依赖用户预装 Python。

### D-005：主播语音通过本地 ASR 转写

- 状态：`Superseded`，由 D-022 取代
- 日期：2026-07-23
- 决定：第一版本地 ASR 使用 `faster-whisper`，由 Silero VAD 负责语音活动检测和分段。外部模型使用最终转写文本作为语音上下文。
- 影响：原始音频默认不发送给 Model Provider；两项实现仍放在 `AsrProvider` 后，允许以后根据双平台实测替换。

### D-006：模型接入必须供应商无关

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：用户可以配置外部模型，第一版实现 OpenAI-compatible 多模态协议；业务层只依赖统一 `ModelProvider`。
- 影响：endpoint、model、鉴权、图像格式和错误处理封装在 Adapter 内。兼容接口仍需能力探测，不能假设所有服务行为完全相同。

### D-007：模型同时使用近期画面和语音文本

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：生成弹幕的观察上下文由近期画面帧、用户文字、ASR 文本及必要的公开房间事件组成。
- 影响：具体帧数、窗口和频率由实测决定，不成为固定产品参数。

### D-008：主要输出是桌面弹幕

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：AI 观众反馈以覆盖目标内容的实时弹幕为主要输出，而不是普通聊天侧栏。
- 影响：Overlay 必须不妨碍用户操作，并提供独立的暂停和恢复路径。

### D-009：AI 身份必须透明

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：产品明确说明观众和弹幕由 AI 生成，不伪造真人身份或真实在线人数。
- 影响：产品文案、录制场景和界面状态都应遵守这一边界。

### D-010：运行参数由配置和实测决定

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：采样频率、观察窗口、帧数、并发、弹幕数量、TTL 和超时不是当前产品不变量。
- 影响：文档描述选择原则和验收结果，不在验证前写死数字。

## 3. 第一版技术基线

### D-011：FastAPI 作为 Electron 管理的本地子进程

- 状态：`Accepted`
- 决定：Electron Main 启动仅监听回环地址的 FastAPI 子进程，负责健康检查、异常处理和退出清理。
- 影响：生产构建携带自己的 Python Runtime；具体冻结/打包工具通过双平台构建 Spike 选择。

### D-012：控制面使用 HTTP，实时数据使用 WebSocket

- 状态：`Accepted`
- 决定：HTTP 处理健康检查、配置和会话命令；WebSocket 传输音频、画面、运行状态和弹幕事件。
- 影响：服务只监听回环地址，每次启动使用短期随机凭证，所有实时队列必须有界。

### D-013：媒体由 Electron 统一采集

- 状态：`Accepted`
- 决定：Electron 使用 Chromium/Electron 媒体 API 统一处理屏幕和麦克风权限；通过 AudioWorklet 形成有界音频块并发送给 FastAPI。
- 影响：Python 不直接枚举或独占麦克风。平台原生采集只在 Electron API 无法满足实测要求时引入。

### D-014：每个观众是独立且连续的逻辑实体

- 状态：`Superseded`，由 D-031 和 D-032 取代
- 决定：旧版把观众身份、人格和长期记忆绑定为同一对象。
- 影响：稳定 Viewer 身份继续保留，但长期记忆改为 Room 共享，PersonaTemplate 与 ViewerInstance 也不再混为一类。

### D-015：发言与调度算法暂不固定

- 状态：`Superseded`，由 D-034 取代
- 决定：旧版保留批量或独立调用、Director 拓扑和观众接话方式为开放问题。
- 影响：首版现在已经锁定 ObservationWave、Director 精确选实例和一实例一独立请求。

### D-016：首版模型请求使用非流式结构化结果

- 状态：`Accepted`
- 决定：弹幕输出很短，第一版优先请求一次完整的结构化结果；只有实测延迟需要时再增加流式解析。
- 影响：Provider 必须支持超时与取消。对 JSON Schema 的支持由能力探测决定，模型结果仍需 Pydantic 校验。

### D-017：透明 BrowserWindow 承担弹幕 Overlay

- 状态：`Accepted`
- 决定：弹幕使用透明、置顶、点击穿透的 Electron `BrowserWindow` 渲染；渲染引擎放在 Adapter 后并优先采用成熟弹幕库。
- 影响：具体弹幕库需要在 Windows/macOS 上验证透明窗口、轨道、销毁和高 DPI 后选择。

### D-018：Pydantic 是跨进程数据合同来源

- 状态：`Accepted`
- 决定：FastAPI/Pydantic 定义控制面和事件 Schema，并生成 TypeScript 类型或客户端；WebSocket 事件同样需要版本化 Schema。
- 影响：不在 Python 和 TypeScript 两侧长期手写两套容易漂移的合同。

### D-019：本地配置、密钥与日志基线

- 状态：`Accepted`
- 决定：普通配置、观众档案和观众记忆保存在本地。ASR 和模型凭据由 Electron `safeStorage` 保存，经短期鉴权的本地通道按会话注入 FastAPI 内存。两端使用带会话标识的结构化本地日志。
- 影响：Renderer、命令行、普通配置和日志不出现明文凭据；日志不记录原始音频、完整画面和长段转写。

### D-020：ASR 模型首次下载并校验

- 状态：`Superseded`，由 D-022 取代
- 决定：应用携带可运行的后端 Runtime，体积较大的 ASR 模型在首次使用前下载，并校验版本和哈希。
- 影响：需要下载进度、失败重试、磁盘空间提示和删除入口；CPU 是最低运行基线，加速能力后续按平台实测增加。

### D-021：第一版使用本地轻量内容管线

- 状态：`Accepted`
- 决定：弹幕先经过结构、长度、时效、重复、屏蔽词和基础硬规则检查；第一版不接外部审核服务。
- 影响：过滤失败时丢弃候选，不能只依赖 Prompt，也不能宣传为覆盖所有语言风险。

### D-022：第一版使用 StepFun Step Plan ASR

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：第一版通过独立 `AsrProvider` 调用 StepFun Step Plan 的 `stepaudio-2.5-asr`。Provider 使用 HTTP + SSE 接收增量和最终转写，只把最终文本送入观众上下文。
- 影响：麦克风音频会发送到用户明确启用的 StepFun 服务，界面必须告知数据去向；凭据使用 `safeStorage` 保存并按会话注入后端。Step Plan 需要一次提交一个有限音频段，具体分段算法与参数通过实测决定。如果延迟不足，可以增加双向流式 ASR Adapter，不改变业务合同。

### D-023：后端采用 Application、Domain、Port 与 Adapter 边界

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：FastAPI 后端使用单进程、单活动会话设计。API 只处理协议，Application Service 编排用例，Domain 维护不变量，业务层通过 Port 使用 Repository、ASR 和 Model Provider，SQLite、StepFun 和 OpenAI-compatible 实现属于 Adapter。
- 影响：接口放在 Application Port，而不是具体 Infrastructure 或 Provider 模块中；WebSocket Handler、SQLAlchemy 模型和供应商 wire format 不能进入业务逻辑。具体模块设计见 [BACKEND_DESIGN.md](./BACKEND_DESIGN.md)。

### D-024：结构化恢复状态使用 SQLite，原始媒体只保存在有界内存

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：Room、Session runtime revision、Viewer 池、共享长期记忆、记忆来源、模式成长梗和有界结构化 Room Event 使用 SQLite 持久化，并使用版本化迁移。原始音频、完整画面、思维链、完整 Prompt 和待显示弹幕不写入数据库。
- 影响：有界 Room Event 用于后端恢复，不等于保存完整直播历史。recorded replay 使用显式制作、脱敏且版本化的 fixture/bundle，不自动保存原始媒体。数据库由 FastAPI 单独拥有，位于 Electron `userData` 数据目录；用户删除的记忆及来源执行物理删除。

### D-025：观众内容按模式、人格和成长梗库分层

- 状态：`Superseded`，由 D-033 取代
- 日期：2026-07-23
- 决定：旧版把 32 个基础人格、模式和成长梗作为主要运行层级。
- 影响：现有素材继续保留，但新版增加 Room、ViewerInstance 和 SessionViewerPool，并将 32 改为 Viewer 上限。

### D-026：模式可复制，人格编辑与覆盖必须隔离

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：内置模式保留可重置基线，也可复制为自定义模式。用户通过完整人格编辑器修改当前模式的人格覆盖；模式内覆盖不能改写内置基础人格或其他模式。人格文档使用带明确格式版本的 `personality.md`。
- 影响：编辑器需要覆盖全部人格字段并报告版本或字段校验错误。复制、编辑、导入和运行时解析都必须测试模式隔离，不能依赖共享对象的意外联动。

### D-027：导演分别输出调度决定和成长梗候选

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：导演输出 `SceneAssessment`，并可附带独立的 `MemeCandidate`。梗可以来自用户文字、最终语音转写、近期真实事件或 AI 互动；导演判断成立且本地校验通过后，候选自动进入当时的激活模式。
- 影响：自动入库必须可撤销，梗条目需要持久化、衰减和归档。`MemeCandidate` 不能直接显示为弹幕、写成 `audience_barrage` 或进入 Overlay；只有后续归属于明确观众的合法弹幕才能显示。

### D-028：当前迭代先实现桌面前端与产品合同

- 状态：`Superseded`，由 D-031 至 D-039 的 Viewer runtime 联动基线取代
- 日期：2026-07-23
- 决定：当前迭代先实现 Electron/React 桌面前端和共享 TypeScript 合同，覆盖模式、人格编辑、版本化 `personality.md` 与成长梗库的本地行为。
- 影响：该阶段已经留下可用的桌面工作区，同时仓库已有 FastAPI/SQLite、StepFun ASR、OpenAI-compatible Provider 和 protocol v1 链路。当前目标是实现 Room shared brain、Viewer runtime、真实 Director、原子热更新、Debug/replay 和 protocol v2；现有 v1 或前端 demo 不能冒充这些 v2 能力。

### D-029：成长梗使用可恢复的初始归档规则

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：当前桌面端在启动时归档超过 30 天未使用、累计使用少于 3 次且未置顶的梗。归档条目保留并可由用户恢复；置顶条目不参与自动归档。
- 影响：这是当前可验证的本地规则，不是最终衰减评分。真实使用数据可以推动后续调参，但在新决定生效前，代码、文档和测试必须保持这组参数一致。

### D-030：观众工作区加载失败时锁写并保留恢复副本

- 状态：`Accepted`
- 日期：2026-07-23
- 决定：`audience-workspace.json` 不存在时可以创建默认状态；读取、JSON 解析或 Schema 校验失败时必须禁止自动保存和关闭时刷新，不得用默认状态覆盖原文件。可读但被拒绝的内容按指纹复制为 `audience-workspace.rejected-<hash>.json`，只有用户显式重置后才重新开放写入。
- 影响：当前版本在载入时按稳定 ID 注入最新内置人格基线，同时保留自定义人格、模式覆盖和梗库，避免内置文案更新使整个工作区不可访问。派生 `personality.md` 同步失败会返回具体原因、写入本地诊断并后台重试，但不改变 JSON 已保存的事实。

### D-031：PersonaTemplate 与 ViewerInstance 分离

- 状态：`Superseded`，由 D-040 细化
- 日期：2026-07-24
- 决定：PersonaTemplate 是可复用表达模板；ViewerInstance 是 Session 内独立 AI 观众。一个 Session 最多创建 32 个 ViewerInstance，同一 PersonaTemplate 可以对应多个实例。现有 32 个 PersonaTemplate 继续作为素材库，但模板数量不是产品不变量。
- 影响：每个实例拥有稳定 ID、确定性别名、微变体和短期状态。`personality.md` 是结构化 PersonaTemplate 的交换格式，不为每个 Viewer 复制一份。

### D-032：长期记忆属于 Room 共享大脑

- 状态：`Accepted`
- 日期：2026-07-24
- 决定：所有 Viewer 共享 RoomWorkingMemory 和绑定 `room_id` 的 RoomLongTermMemory；Persona 只影响关注角度和表达。Viewer 私有状态仅保存 Session 内最近发言、直接互动、注意点和冷却。
- 影响：公开 AI 弹幕下一波对所有 Viewer 可见。用户事实需要非 AI 证据；AI 互动可以形成 room lore。经历跨模式共享，ModeMeme 仍按模式隔离。

### D-033：Mode 通过人数和权重建立 Viewer 池

- 状态：`Superseded`，由 D-040 取代
- 日期：2026-07-24
- 决定：ModeDefinition 使用 `viewer_count`、`persona_weights`、普通/高光 response range、人格覆盖和 ambience。权重只用于确定性 Viewer 池分配，不在 Director 中再次加权。
- 影响：六个现有模式的初始 Viewer 数为 24、28、16、14、24、14。UI 保存权重并预览准确实例数，房间人数、每波响应人数和网络并发数必须分开。

### D-034：ObservationWave、Director 与独立 Viewer 请求

- 状态：`Superseded`，由 D-040 取代 Director 选人部分
- 日期：2026-07-24
- 决定：文字、final 语音、显著画面变化和受控 ambient tick 形成 ObservationWave。FastAPI 计算硬预算，Director 每波调用一次并选择准确 ViewerInstance ID；每个 selected Viewer 发起一次独立模型请求。
- 影响：首版不实现多 Viewer batching。同一波使用冻结上下文，AI 输出不能直接递归触发新波。Viewer 每波返回一条弹幕或合法 silence，并携带 reaction type 和 evidence refs。

### D-035：运行时配置使用版本化原子热更新

- 状态：`Superseded`，由 D-040 细化 Viewer 保留语义
- 日期：2026-07-24
- 决定：Electron 自动保存编辑；默认通过“应用到当前会话”把完整 runtime spec 原子切换到新 `audience_epoch`，开发模式可启用保存后自动应用。
- 影响：旧 epoch 请求零副作用；配置可以查询、回滚和重放。未变化 Viewer 保留状态，Persona 内容变化清空对应短期状态，模式切换重建 Viewer 池但不清空 Room 记忆。

### D-036：默认独立看历史帧，手动切换共享视觉摘要

- 状态：`Accepted`
- 日期：2026-07-24
- 决定：默认 `direct_frames`，所有 selected Viewer 独立接收相同历史 FrameBundle；备用 `shared_summary` 只复用一次视觉理解，Viewer 请求仍然独立。
- 影响：FrameBundle 默认使用 `change_peaks + 3 张`，数量、窗口、选择策略、尺寸和质量可热更新。首版不自动降级视觉模式。

### D-037：机器可读调试和重放是首版产品能力

- 状态：`Accepted`
- 日期：2026-07-24
- 决定：Pydantic/JSON Schema 是合同来源；Debug API、结构化 trace、headless harness 和 recorded/live replay 是首版必要能力，UI 只消费同一数据源。
- 影响：agent 不依赖 UI 即可创建 Session、提交 fixture、查询状态、导出 trace 和重放。测试环境与真实 Room 数据强隔离，live replay 必须显式开启。

### D-038：Provider 使用单 profile 和角色模型

- 状态：`Accepted`
- 日期：2026-07-24
- 决定：首版使用一个活动 OpenAI-compatible Model Provider profile，Director、Viewer、memory 和 visual summary 可以覆盖不同 model ID；StepFun ASR 独立配置。
- 影响：endpoint 或模型热更新前必须 capability probe。当前凭据实际模型列表和能力是执行 Gate，Provider 不可用时真实验收状态为 `BLOCKED`。

### D-039：首个完整 E2E 使用固定 CS2/CSGO 场景

- 状态：`Accepted`
- 日期：2026-07-24
- 决定：使用固定 CS2/CSGO 片段、脚本化 final 语音和文字验证普通跑图、高光、失误、点名、6657 权重热更新、共享记忆和模式梗，再补真实游戏 smoke。
- 影响：真实模型按身份、证据、反应类别和状态变化验收，不比较固定弹幕文本。

### D-040：Session Viewer 生命周期与逐 Viewer 行为决策

- 状态：`Accepted`
- 日期：2026-07-24
- 决定：PersonaTemplate 是持久化行为模板，ViewerInstance 是仅属于单次 Session 的独立观众，拥有与 Persona 无关的用户名和头像种子。Mode 使用 `target_concurrent_viewers` 表示目标同时在线人数，Persona 权重只在创建 Viewer 时决定 assignment。Director 只输出 `SceneAssessment`；每个 active 且未禁言 Viewer 使用本地可解释概率和稳定抽样独立决定是否发言，最终候选再各自调用一次模型。
- 影响：Viewer 可以加入、离开、同场重返、限时禁言、解除禁言和被踢；被踢后本场不可重返并可由新 Viewer 补位。Mode 热更新保留 ViewerIdentity。最终提交同时校验 epoch、sequence、presence/moderation/behavior revisions。正常关播清空当前 audience 和私有状态，新直播创建全新 Viewer；异常重启恢复同一未终止 Session 的 Viewer。

## 4. 开放问题

### Q-001：Electron 与 FastAPI 的媒体编码是什么？

- 状态：`Open`
- 已定边界：第一版 StepFun ASR 输入使用单声道 16 kHz PCM S16LE。
- 需要回答：画面使用 JPEG、WebP 或其他格式，音频如何分段，以及本地数据面使用 WebSocket 二进制消息还是其他传输。

### Q-003：MVP 的默认模型体验是什么？

- 状态：`Open`
- 需要回答：首次启动必须由用户填写自己的 Provider，还是提供一个可选的默认服务。

### Q-005：弹幕 Overlay 的第一版范围是什么？

- 状态：`Open`
- 需要回答：单显示器还是多显示器，窗口化内容还是包含独占全屏，是否需要顶部/底部固定弹幕和保护区。

### Q-006：性能目标如何设定？

- 状态：`Open`
- 需要回答：端到端延迟、资源占用和弹幕密度应在原型测量后形成什么门槛。
- 约束：测量前不写虚假的硬数字。

### Q-009：后端冻结和弹幕引擎如何落地？

- 状态：`Open`
- 需要回答：Python Runtime 使用哪种目录式冻结工具，双平台 Spike 后选择哪个弹幕库，以及嵌套 Runtime 如何完成签名和更新。

## 5. 已取代的旧决定

以下内容属于上一版方案，不再是实现要求：

| 旧决定 | 当前处理 |
| --- | --- |
| 仅支持 Windows | 被 D-002 取代 |
| 固定使用 StepFun `step-explore` | 被 D-006 取代 |
| 未经 Provider 隔离的 StepAudio 云端 ASR | D-022 改为通过统一 `AsrProvider` 接入 |
| 固定 32 个独立人格调用 | D-031 将 32 改为 ViewerInstance 上限，PersonaTemplate 数量独立 |
| 固定的人群导演模型 | D-034 固定 Director 合同和一实例一独立请求，但角色 model ID 可配置 |
| CSGO 四类事件作为产品验收 | D-039 改为固定 CS fixture 加脚本化输入和真实 smoke |
| Electron UtilityProcess 承担 AI 后端 | 被 D-004 取代 |
| 黑客松四天排期和并发阶梯 | 不属于长期产品文档 |
| 供应商专用请求头、SSE 事件和密钥规则 | 未来放入对应 Provider 的实现文档或代码 |

## 6. 决策模板

```md
### D-XXX：标题

- 状态：Accepted / Proposed / Open / Superseded
- 日期：YYYY-MM-DD
- 背景：
- 决定：
- 备选：
- 理由：
- 影响：
- 验证证据：
```
