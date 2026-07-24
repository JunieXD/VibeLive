# AI 观众联动需求锁定记录

> 状态：历史需求记录。AI 观众发言部分已由 [AUDIENCE_SPEAKING_PRODUCT_SPEC.md](./AUDIENCE_SPEAKING_PRODUCT_SPEC.md) 取代；其中 Director 相关约束不再适用。
>
> 日期：2026-07-24
>
> 访谈方式：项目级 `grill-me-codex` 的单问题决策树
>
> 正式计划：[VIEWER_RUNTIME_INTEGRATION_PLAN.md](./VIEWER_RUNTIME_INTEGRATION_PLAN.md)

## 1. 执行说明

本轮只完成需求澄清和计划固化，没有修改产品代码。

上游 `grill-me-codex` 原本设计为 Claude 负责访谈、外部 Codex 负责第二阶段审查。当前会话本身已经是官方 Codex，因此只使用 Act 1 的逐问题访谈方法，不递归启动另一个 Codex，不运行自动构建，也不使用 OMX。

所有会改变产品行为的分支均由用户逐项确认。普通工程细节以后遵循“AI-friendly、机器可读、可重放、失败可解释”的默认原则，不再逐项打断用户。

## 2. 被纠正的旧假设

### 2.1 32 不是 32 个人格

旧假设：32 份人格 Markdown 对应 32 个观众。

锁定结论：

- 32 是 Session ViewerInstance 上限。
- PersonaTemplate 数量和 ViewerInstance 数量解耦。
- 现有 32 个 Persona 保留为模板素材库，但不是永久产品不变量。
- 同一 PersonaTemplate 可以生成多个独立 Viewer。

### 2.2 长期记忆不属于 Persona

旧假设：同 Persona 的 Viewer 共享该 Persona 的长期记忆。

锁定结论：

- 所有 Viewer 共享同一个 Room 大脑。
- Persona 决定观察和表达方式，不决定谁能知道哪些公开事实。
- Viewer 只有 Session 内短期私有状态。
- RoomWorkingMemory 保存当前公开上下文。
- RoomLongTermMemory 跨 Session 和模式共享。

### 2.3 Session 配置不是启动后永久冻结

旧假设：人格和模式修改只能在下一次 Session 生效。

锁定结论：

- 编辑自动保存。
- 默认通过“应用到当前会话”进行版本化原子热更新。
- 开发模式支持保存后自动应用。
- Electron 每次提交完整 canonical runtime snapshot，并携带 revision、hash 和 `apply_id`；后端重算 hash 后校验。
- 每次应用递增 epoch，旧异步结果全部失效。
- 更新可查询、可回滚、可重放。

### 2.4 调试信息不进入模型上下文

旧担忧：精简 Viewer 历史会降低可调试性。

锁定结论：

- Runtime Context 保持小而稳定。
- 另建完整结构化 Debug Trace。
- Agent 通过 trace、Debug API、headless harness 和 replay 还原链路。
- 不能为调试把无关历史塞回 prompt，改变模型行为。

## 3. 已锁定产品决定

### 3.1 配置和运行时

- Electron 管理可编辑 PersonaTemplate、ModeDefinition 和 Provider 设置。
- FastAPI 管理 Session、Viewer、Director、Shared Brain、ModeMeme 和 telemetry。
- 模型设置在 capability probe 通过后也可原子热更新。
- 首版一个活动 Model Provider profile，不同角色可以覆盖 model ID。

### 3.2 Mode 和 Viewer

- Mode 保存 viewer count、Persona 权重、普通/高光 response range 和 ambience。
- 权重只用于确定性 Viewer 池分配，不在 Director 中再次加权。
- 现有六个模式初始 Viewer 数为 24、28、16、14、24、14。
- Viewer 使用 Persona 名加实例序号作为首版确定性别名。
- 同 Persona 实例具有稳定的确定性微变体。
- 模式切换重建 Viewer 私有状态，但不清空 Room 大脑。

### 3.3 热更新

- 未变化 Viewer 保留 ID、状态和冷却。
- Persona 内容变化的 Viewer 保留 ID，但清空短期状态。
- 被移除 Viewer 的 ID 在当前 Session 不复用。
- 新 Viewer 创建新 ID 和空状态。
- 配置更新失败时继续使用旧版本。

### 3.4 ObservationWave

- 用户文字、final voice、显著画面变化和受控 ambient tick 可以触发 ObservationWave。
- partial voice 不进入正式链路。
- 同一波上下文冻结，AI 输出不直接递归触发下一波。
- `natural` 模式默认没有 ambient tick。
- `continuous` 模式使用有界 ambient tick，连续无真实输入后强制安静。

### 3.5 画面

- 默认每个 selected Viewer 独立看同一历史 FrameBundle。
- FrameBundle 默认 `change_peaks + 3 张`。
- 帧数、历史窗口、选择策略、尺寸和质量可热更新。
- 备用 `shared_summary` 手动切换，不自动降级。
- 首版不实现多个 Viewer batching。

### 3.6 Director

- FastAPI 计算本地硬预算。
- Director 决定准确 ViewerInstance ID。
- 每波只调用一次 Director。
- Director 输出 CrowdDecision 和独立 MemeCandidate，不能输出弹幕正文。
- Director 可以选择 0 个 Viewer。
- strict 模式失败时安静；resilient 模式使用明确标记的本地 fallback。

### 3.7 Viewer 请求

- 每个 selected Viewer 发起一个独立 Provider 请求。
- 初始最大并发 12，其余进入有界队列。
- TTL 从 ObservationWave 创建时开始。
- 每实例 latest-wins。
- 瞬时错误且 TTL 足够时只重试同一 Viewer 一次。
- 请求失败不换其他 Viewer 补位。
- 每 Viewer 每波返回 0 或 1 条弹幕。
- silence 是合法结果。
- 输出必须带 reaction type 和 evidence refs。
- 快结果不等待慢结果。

### 3.8 用户点名

- 文字通过结构化 `@` 指定 Viewer 或 Persona。
- final 语音通过可追踪 resolver 识别点名。
- 准确点名的 Viewer 必须进入本地选择约束。
- Persona 点名由 Director 选择至少一个对应实例。

### 3.9 Shared Brain

- 所有已公开弹幕进入有界 RoomWorkingMemory。
- 所有 Viewer 下一波可见同一公开上下文。
- RoomLongTermMemory 按当前 ObservationWave 检索相关切片。
- 所有 Viewer 访问同一记忆库，Persona 只影响关注排序。
- 用户事实必须有非 AI 证据。
- AI 互动可以形成 room lore，但不能证明用户现实事实。
- 长期记忆在波次后异步提取，不阻塞弹幕。

### 3.10 ModeMeme

- 经历在 Room 中共享，梗按 Mode 隔离。
- Director Candidate 通过本地校验后默认自动入库。
- 用户可以关闭自动成长。
- 自动入库支持通知、撤销、持久化和重启恢复。
- 同波 AI 输出从下一波开始才参与梗判断。
- Candidate 不能直接显示为弹幕。

### 3.11 调试和验收

- Pydantic/JSON Schema 是跨进程合同来源。
- Debug API 和 JSON artifact 是权威，UI 只是查看器。
- headless harness 是首版必要能力。
- recorded replay 默认确定性运行。
- live replay 必须显式开启。
- 测试环境与真实用户数据强隔离。
- 首个验收场景是固定 CS2/CSGO 片段加脚本化语音、文字、模式和热更新。
- 真实模型按身份、证据、类别和状态变化验收，不比较固定文案。
- Provider 不可用时真实验收状态为 `BLOCKED`。

### 3.12 后端恢复

- 后端重启后恢复同一逻辑 `session_id`，并递增 `audience_epoch`。
- 旧队列、旧网络请求和旧 epoch 候选全部失效。
- 从 committed runtime snapshot、Viewer 池和有界公开 Room events 重建运行态。
- 不恢复原始音频、完整画面或旧 Provider 请求。
- snapshot 或事件链校验失败时 fail closed，返回机器可读错误，不能静默创建伪连续状态。

## 4. Out Of Scope

- 多 Room UI。
- 多 Model Provider endpoint。
- 自动 Provider 故障切换。
- 多 Viewer batching。
- 自动视觉算法降级。
- AI 弹幕直接递归触发 AI。
- 原始音频、完整画面和思维链持久化。
- 云同步、账号、真实直播平台和真人观众。
- 跨设备共享记忆。
- 运行时随机改写 Persona 核心人格。

## 5. 当前未锁死的运行参数

以下参数必须可配置并通过 telemetry 调整，不是产品承诺：

- ObservationWave 合并窗口。
- 画面变化阈值。
- FrameBundle 历史窗口、数量、尺寸和质量。
- Viewer 请求 TTL。
- queue capacity。
- Provider timeout 和 retry backoff。
- RoomWorkingMemory 窗口和 token budget。
- ambient tick 冷却和最大连续轮数。
- 长期记忆检索数量和阈值。

## 6. 访谈结论

产品需求树已经闭合，没有仍需用户决定的实现阻塞项。

开始实现前只需要重新探测当前 Provider 的真实模型、视觉、结构化输出、并发、余额和 ASR 能力。这是环境 Gate，不是产品需求问题。
