# AI 虚拟直播间文档

> 状态：产品方向基线
>
> 更新日期：2026-07-24
>
> AI 观众发言机制以 [AUDIENCE_SPEAKING_PRODUCT_SPEC.md](./AUDIENCE_SPEAKING_PRODUCT_SPEC.md) 为准；旧文档中关于 Director 的描述均已废弃。

## 产品定义

这是一个面向 Windows 和 macOS 的 AI 虚拟直播间桌面应用。

用户像主播一样展示自己选定的屏幕内容，可以发送文字弹幕，也可以直接对麦克风说话，但应用不会把内容推送给真实观众，也不创建公开直播间。应用通过用户启用的 StepFun ASR 分别转写主播麦克风和 Windows 系统声音，再把带来源标记的近期对话和近期画面交给用户配置的外部模型，由一组具有稳定身份的 AI 观众进行交流并生成实时弹幕。

核心体验是：用户面对的不是随机弹幕生成器，而是一群明确标注为 AI、各自拥有名字、人格、偏好和会话内状态的虚拟观众。它们共享直播间中公开发生的事情和长期共同记忆，但会从各自人格角度作出不同回应。

观众内容按 Room、模式、PersonaTemplate、ViewerInstance 和成长梗组织。当前保留 32 个内置人格模板和 6 个模式；模板数量与一个 Session 中 1 到 32 个独立 ViewerInstance 没有一一对应关系。

```text
屏幕画面 ───────────────┐
                       ├─> 近期上下文 ─> 外部模型 ─> AI 弹幕
用户文字 ───────────────┤
麦克风（主播） ─> StepFun ASR A ─┤
Windows 系统声音 ─> StepFun ASR B ─┘
```

## 已确定的方向

- 桌面端使用 Electron。
- Renderer UI 使用 React + TypeScript。
- 本地后端使用 FastAPI，Python 依赖和锁文件使用 `uv` 管理。
- 产品目标平台是 Windows 和 macOS。
- 第一版使用两个相互隔离的 StepFun ASR API 通道，分别转写麦克风和 Windows 系统声音；业务层仍只依赖统一 ASR Provider。
- 系统声音采集默认开启并标记为推荐，用户可以关闭；非 Windows 平台明确显示当前不支持系统回环采集。
- 麦克风和系统声音的原始音频会发送给用户明确启用的 StepFun 服务；弹幕生成模型只接收带来源标记的最终文本。
- 第一版接入 OpenAI-compatible 多模态协议，并保留统一 Model Provider 接口。
- Electron Main 管理本地 FastAPI 子进程；HTTP 处理控制面，WebSocket 处理实时数据。
- 屏幕、麦克风和 Windows 系统回环音频由 Electron 统一采集，两路音频通过独立 Web Audio 管线分块送入 FastAPI，不进行混音。
- 模型结合近期画面、用户文字、近期语音文本和公开房间对话生成弹幕。
- 每个 ViewerInstance 都有稳定 ID、PersonaTemplate、实例微变体和会话内短期状态；所有 Viewer 共享 Room 工作记忆和跨 Session 长期记忆。
- 内置模式可以复制为自定义模式；人格覆盖只在所属模式内生效，完整编辑结果使用版本化 `personality.md` 表示。
- 用户发送的文字弹幕和语音都会进入房间对话，AI 观众可以回应用户、画面和其他观众。
- 观众身份与行为连续性是产品要求；现有 32 个 PersonaTemplate 是可扩展素材库，Mode 使用每个人格的精确人数确定性建立 Viewer 池。
- Director 在后端预算内选择准确 ViewerInstance；每个被选实例发起一次独立模型请求，首版不做多 Viewer batching。
- 人格、模式和角色模型配置通过 revision、hash 和 epoch 保护的原子热更新应用到当前 Session。
- Debug API、结构化 trace、headless harness 和 recorded/live replay 是首版必要能力。
- 导演保留 `CrowdDecision` 调度输出，并新增独立的 `MemeCandidate`；候选梗不能直接显示为弹幕。
- 梗可以来自用户文字、语音、近期真实事件或 AI 互动，经导演判断和本地校验后自动进入当前模式，并支持撤销、持久化、衰减和归档。
- 目标后端实时链路使用单进程、有界内存管线；Room、Session revision、Viewer 池、共享长期记忆和成长梗库使用 SQLite 持久化。
- 弹幕通过桌面覆盖层显示，并且不能妨碍用户操作原应用。
- 界面必须明确说明观众是 AI，不能伪装成真人或真实在线人数。
- 用户可以随时暂停、清屏和停止；停止后不得继续采集或补发旧弹幕。

当前实现已具备 Electron/React 桌面端、FastAPI、SQLite、StepFun ASR、OpenAI-compatible 模型 Provider 和实时弹幕管线。桌面端已接入真实 Session、音频、画面、文字和 Overlay 链路；前端的模式与人格工作区和后端内置观众目录仍是两套数据源，后续需要统一。

## 仍未确定

以下内容需要通过原型和实测决定，不是当前产品承诺：

- 画面采样频率、观察窗口长度、FrameBundle 数量和关键帧选择参数的最终默认值。
- 两路音频的最终分段参数以及 StepFun ASR API SSE 的延迟是否满足体验要求。
- Observation 合并窗口、响应预算、Viewer TTL、并发和 ambient tick 的最终默认值。
- RoomLongTermMemory 检索数量、阈值和衰减参数。
- 首次启动默认激活哪个内置模式，以及房间活跃度参数。
- 是否用真实使用数据形成的衰减评分替换当前 30 天低频归档规则。
- 媒体编码、弹幕渲染库、Python 冻结工具和精确依赖版本。
- 固定 CS2/CSGO 验收片段之外还需要增加哪些场景。

这些问题统一记录在 [DECISIONS.md](./DECISIONS.md)，验证后再转为正式决定。

## 文档结构

| 文档 | 内容 |
| --- | --- |
| [PRODUCT.md](./PRODUCT.md) | 产品目标、用户流程、MVP 范围和验收标准 |
| [ARCHITECTURE.md](./ARCHITECTURE.md) | Electron、FastAPI、ASR Provider 和模型 Provider 的系统边界 |
| [BACKEND_DESIGN.md](./BACKEND_DESIGN.md) | FastAPI 模块、运行时数据流、SQLite Schema、事务与迁移设计 |
| [DECISIONS.md](./DECISIONS.md) | 已接受决定、候选方案和开放问题 |
| [VIEWER_RUNTIME_INTEGRATION_PLAN.md](./VIEWER_RUNTIME_INTEGRATION_PLAN.md) | AI 观众实例、Director、独立请求、记忆与成长梗的前后端联动执行计划 |
| [VIEWER_RUNTIME_REQUIREMENTS_LOG.md](./VIEWER_RUNTIME_REQUIREMENTS_LOG.md) | 需求访谈中锁定的产品语义、纠偏结论和首版边界 |
| [AUDIENCE_SPEAKING_PRODUCT_SPEC.md](./AUDIENCE_SPEAKING_PRODUCT_SPEC.md) | AI 观众发言的已确认产品规则、固定参数与验收场景；其发言机制规则优先于旧设计文档 |
| [SB6657_STYLE_TUNING.md](./SB6657_STYLE_TUNING.md) | 6657 外部语料抓取边界、聚合画像、模式专属生成约束和刷新验证流程 |

## 文档原则

1. 产品文档描述用户价值和可观察行为，不锁定可替换的供应商或实现。
2. 架构文档固定模块边界和数据合同，不提前固定未经验证的参数。
3. 候选方案必须标记为 `Proposed` 或 `Open`，不能写成已经实现。
4. 具体库版本、供应商协议和性能数字应由代码、配置与实测证据管理。
5. 决策发生变化时，只修改受影响的文档，不复制同一结论到多份专题文档。
