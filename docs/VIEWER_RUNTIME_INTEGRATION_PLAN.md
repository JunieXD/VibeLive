# AI 观众前后端联动实施计划

> 状态：历史实施计划。AI 观众发言部分已由 [AUDIENCE_SPEAKING_PRODUCT_SPEC.md](./AUDIENCE_SPEAKING_PRODUCT_SPEC.md) 取代；其中 Director 相关链路不再适用。
>
> 锁定日期：2026-07-24
>
> 验收日期：2026-07-24
>
> 计划所有者：官方 Codex 单会话
>
> 需求记录：[VIEWER_RUNTIME_REQUIREMENTS_LOG.md](./VIEWER_RUNTIME_REQUIREMENTS_LOG.md)
>
> 执行边界：只使用官方 Codex、Git、pnpm、uv 和仓库测试工具；不使用 OMX、OMX Team、tmux worker 或 `.omx` 状态

## 1. 最终目标

把现有 Electron 模式与人格编辑器、FastAPI Session、真实画面/语音/文字输入、AI Director、独立 Viewer 模型请求、Overlay、共享记忆和成长梗联成一条可运行、可追踪、可重放的真实链路。

```text
ModeDefinition
  -> SessionViewerPool
  -> ObservationWave
  -> AI Director
  -> selected ViewerInstance IDs
  -> one independent request per selected Viewer
  -> as-completed BarrageEvent
  -> Overlay
  -> RoomWorkingMemory
  -> RoomLongTermMemory / ModeMeme
```

系统首先服务 AI 编码和调试：

- 所有重要状态都有版本、hash、ID 和机器可读 Schema。
- 所有异步结果都能解释为什么提交或丢弃。
- 不依赖 UI 才能运行、检查和重放链路。
- fake 证据与真实 Provider 证据严格分开。
- 真实 Provider 不可用时必须报告 `BLOCKED`，不能用 mock 冒充完成。

## 2. 核心概念

### 2.1 Room

`Room` 是共享大脑的持久命名空间。

- 首版 UI 只有一个默认 Room。
- 数据模型从首版就携带稳定 `room_id`。
- 同一 Room 中切换模式、停止 Session 或重新开始 Session，不会丢失 Room 长期记忆。
- headless、测试和未来其他直播项目使用不同 `room_id`，不能污染真实数据。

### 2.2 Session

`Session` 是一次直播运行。

- Session 有稳定 `session_id`。
- 每次热更新或后端恢复递增 `audience_epoch`。
- Session 内有固定版本的配置快照、Viewer 池和运行时状态。
- 旧 epoch 的请求、弹幕、记忆候选和成长梗候选不能提交。

### 2.3 PersonaTemplate

PersonaTemplate 只决定 Viewer 如何观察和表达：

- 核心人格、口吻、偏好、边界和行为规则。
- 不拥有私有长期事实记忆。
- 权威数据是版本化结构对象。
- `personality.md` 是可读、可导入导出的版本化交换格式，不是每个 Viewer 的独立运行时文件。

现有 32 个基础人格继续作为首版内置模板，但“必须永远恰好有 32 个人格”不再是产品约束。

### 2.4 ViewerInstance

ViewerInstance 是真正独立的 AI 观众：

- 仅在当前 Session 存在。
- 引用一个 PersonaTemplate。
- 有稳定 `viewer_instance_id`、实例序号、显示别名、微变体、短期私有状态、冷却和 latest-wins 邮箱。
- 同一 PersonaTemplate 可以创建多个 ViewerInstance。
- 多个 Viewer 共享 Room 大脑，但从不同人格和微变体角度回应。

重复模板的实例使用确定性别名：

```text
串子哥·01
串子哥·02
串子哥·03
```

### 2.5 ModeDefinition

ModeDefinition 决定：

- 当前 Room 中本次 Session 建立多少 Viewer。
- 使用哪些 PersonaTemplate。
- 每个 PersonaTemplate 对应的准确 Viewer 人数，`0` 表示不参与。
- 模式内人格覆盖。
- 普通与高光事件的响应人数范围。
- ambient 行为。
- 当前模式独立的成长梗 namespace。

### 2.6 ObservationWave

ObservationWave 是一次可重放的观察和反应边界。

- 同一波中的 Director 和 Viewer 使用冻结上下文。
- 同波快 Viewer 的结果不会改变慢 Viewer 的 prompt。
- 同波公开弹幕从下一波开始才进入其他 Viewer 的上下文。

### 2.7 Shared Brain

Shared Brain 分为：

- `RoomWorkingMemory`：当前 Session 中有界的公开近期事件。
- `RoomLongTermMemory`：跨 Session 保留、所有 Viewer 可访问的共同经历和可信事实。
- `ViewerPrivateState`：单个实例自己的最近发言、直接互动、注意点、临时情绪和冷却，不是私有长期记忆。

### 2.8 ModeMeme

ModeMeme 是模式内的可复用梗，不等于 Room 记忆。

- 共同经历跨模式共享。
- 具体梗句、复读方式和使用强度按 mode namespace 隔离。
- 同一共同经历可以在不同模式发展成不同梗。

## 3. “32”的正确含义

`32` 是一个 Session 最多存在的 ViewerInstance 数量，不是：

- 32 份必须互不复用的人格 Markdown。
- 32 个固定 PersonaTemplate。
- 每一波都必须产生 32 条弹幕。
- 每一波都必须立即并发 32 个网络请求。

ModeDefinition 使用：

```text
persona_counts: { persona_id: 0..32 }
normal_response_range
highlight_response_range
```

三个数量概念必须分开：

| 概念 | 含义 |
| --- | --- |
| `sum(persona_counts)` | 当前 Session 中存在多少 AI 观众 |
| response range | 一次 ObservationWave 建议选择多少 Viewer |
| `max_in_flight` | 同时进行多少个 Provider 网络请求 |

## 4. 配置权威边界

### 4.1 Electron 权威

Electron 是可编辑配置的权威：

- PersonaTemplate。
- ModeDefinition。
- 当前激活模式。
- 模式内人格覆盖。
- 用户手工成长梗操作。
- Provider profile、角色模型选择和安全凭据。

Electron 使用当前工作区编译完整 canonical runtime spec。

### 4.2 FastAPI 权威

FastAPI 是运行时的权威：

- 校验配置、重算 canonical hash。
- 建立和更新 SessionViewerPool。
- ObservationWave、Director、Viewer 请求和并发。
- RoomWorkingMemory、RoomLongTermMemory 和 ModeMeme。
- epoch、取消、TTL、stale 和提交围栏。
- Debug Trace、replay 和 telemetry。

两端不各自维护一套可编辑人格/模式数据库。

## 5. 版本化热更新

### 5.1 用户行为

- Electron 编辑始终自动保存本地工作区。
- 默认只有点击“应用到当前会话”才改变运行时。
- 开发模式可启用“保存后自动应用”。
- 应用成功后显示新 revision、config hash 和 `audience_epoch`。
- 应用失败继续使用旧版本，不允许半更新。
- 支持回滚到上一份已提交 runtime spec。

### 5.2 原子切换

热更新请求携带：

```text
apply_id
base_revision
audience_contract_version
canonical_runtime_spec
client_config_hash
```

后端流程：

1. 校验 Schema、引用、范围和 Provider capability。
2. 重算 canonical hash。
3. 持久化 pending revision。
4. 在 ObservationWave 边界原子切换。
5. 递增 `audience_epoch`。
6. 取消或废弃旧 epoch 工作。
7. 提交 revision 并返回机器可读 diff summary。

### 5.3 Viewer 状态协调

- Persona、覆盖和配额都未变化：保留 Viewer ID、短期状态和冷却。
- Persona 或模式覆盖发生变化：保留仍在对应配额内的 Viewer ID，但清空其短期私有状态并加载新 persona revision。
- 人数调整造成某人格超额时，优先把超额实例重置并分配到缺额人格；总人数缩小时才移除没有新席位的实例，ID 在当前 Session 不复用。
- 新增配额：创建新 ID、确定性别名、微变体和空状态。
- 切换整个模式：重建 Viewer 池并清空 ViewerPrivateState。
- RoomWorkingMemory 和 RoomLongTermMemory 不因模式切换清空。

### 5.4 Provider 热更新

endpoint、角色模型或凭据变化时：

1. 先执行最小 capability probe。
2. 探测成功后生成 provider revision。
3. 与 runtime spec 一起原子应用。
4. 探测失败继续使用旧配置。
5. 凭据不进入配置快照、Debug Trace 或 replay bundle。

## 6. PersonaTemplate 合同

PersonaTemplate 至少包含：

```text
persona_id
document_version
revision
content_hash
display_name
role
traits
speech_style
behavior
trigger_preferences
avoid_patterns
silence_bias
burst_bias
repetition_bias
cooldown_ms
content_flags
enabled
```

规则：

- UTF-8、LF、固定字段顺序和稳定序列化。
- 实质内容无变化时 revision 不递增。
- mode override 不修改 base persona revision。
- Markdown 与结构对象必须通过同一个 canonical parser/serializer 往返。
- 不兼容版本必须报错，不能静默丢字段。
- 内置 Persona ID 稳定；自定义 Persona 可以复制、停用和删除。

## 7. ModeDefinition 和 Viewer 池

### 7.1 ModeDefinition v3

```text
mode_id
namespace_id
revision
persona_counts
persona_overrides
normal_response_range
highlight_response_range
ambience
```

约束：

- `persona_counts` 中每个值为 0 到 32，合计为 1 到 32。
- response range 最大值不能超过 `persona_counts` 的合计。
- 0 个响应始终合法。
- 至少一个启用 Persona 的人数大于 0。
- 每个正人数直接决定 Viewer 池中的实例数，不存在第二套权重或比例模式。

### 7.2 Workspace 迁移

一次性迁移：

```text
viewer_count = clamp(old_burst_limit.maximum, 1, 32)
normal_response_range = old_base_activity
highlight_response_range = old_burst_limit
```

六个内置模式初始 Viewer 数：

| 模式 | Viewer 数 |
| --- | ---: |
| CSGO：热闹游戏房 | 24 |
| CSGO：6657 玩机器风格 | 28 |
| CSGO：新人友好 | 16 |
| CSGO：温和陪伴 | 14 |
| CSGO：竞技嘴硬局 | 24 |
| CSGO：纯乐子冷场包 | 14 |

迁移后，旧 workspace 的 `targetConcurrentViewers`、`personaIds` 和 `personaWeights` 使用既有最大余数法一次性换算为 `personaCounts`；保存后的 v4 workspace 只保留精确人数。总人数不再自动跟随 response range。

### 7.3 精确人数建池

1. 校验每个 `persona_counts` 值为整数 0 到 32，合计为 1 到 32。
2. 忽略人数为 0 的 Persona，并拒绝引用未知或禁用 Persona 的正人数。
3. 为每个剩余 Persona 创建恰好等于其人数的 ViewerInstance。
4. 稳定排序只用于实例 ordinal、别名和微变体，不改变各 Persona 的人数。

UI 直接保存人数并显示合计，不保存权重、比例或独立目标人数。

### 7.4 Viewer 微变体

每个 ViewerInstance 根据 Session seed、persona ID 和 ordinal 派生稳定微变体：

- 表达长度。
- 嘴硬、质疑或鼓励强度。
- 梗偏好。
- 关注点。
- 合法 silence 倾向。

微变体不能改变 Persona 核心边界，必须写入 Viewer 快照并支持重放。

## 8. ObservationWave

### 8.1 触发源

- 用户文字立即触发，优先级最高。
- final ASR transcript 立即触发。
- 画面显著变化或观察冷却到期时触发 screen-only wave。
- `continuous` 模式可以由受控 ambient tick 触发。
- AI 弹幕本身不能直接递归触发新波。

相近时间内的画面、文字和 final 语音合并成一波。合并窗口和冷却是可配置运行参数，不是产品硬常量。

### 8.2 FrameBundle

每波使用历史关键帧序列：

```text
frame_bundle_size
frame_window_ms
frame_selection_strategy
frame_max_dimension
frame_quality
```

首版默认：

```text
frame_bundle_size = 3
frame_selection_strategy = change_peaks
```

也就是默认使用 `change_peaks + 3 张历史画面`；张数和策略都可以热更新，便于实测对比效果与延迟。

支持：

- `latest_n`
- `evenly_spaced`
- `change_peaks`

这些参数支持版本化热更新。每张帧保留时间戳、顺序、尺寸、编码和 hash。无有效历史时允许只有一张，不能复制图片凑数。

### 8.3 视觉输入模式

```text
viewer_visual_input_mode = direct_frames | shared_summary
```

- 默认 `direct_frames`：所有 selected Viewer 独立看到同一 FrameBundle。
- 备用 `shared_summary`：一次视觉理解形成结构化摘要，Viewer 仍各自独立请求。
- 首版只允许手动切换，不自动降级。
- 两种模式共享相同 Viewer、Director、记忆和事件合同。
- 不实现多个 Viewer 合并成一个模型请求。

### 8.4 语音

- partial transcript 只用于 UI 状态和 Debug Trace。
- 只有 final transcript 创建正式 `user_voice` 事件并进入模型、记忆和 replay。
- final 使用稳定 utterance ID，修订和重复提交必须幂等。
- final 与对应历史 FrameBundle 按时间对齐。

### 8.5 点名

- 文字 `@` 自动补全直接提交 `target_viewer_id` 或 `target_persona_id`。
- 语音使用可追踪 mention resolver。
- 唯一且高置信实例匹配形成强制 Viewer 目标。
- 只匹配 Persona 时，由 Director 选择至少一个该 Persona 的实例。
- 歧义无法消除时按普通 Room 发言处理。
- 被点名 Viewer 可以合法 silence，但必须返回明确结果。

## 9. AI Director

### 9.1 输入

Director 接收：

- Room、Session、epoch 和 Observation IDs。
- 冻结的 ObservationWave。
- compact Viewer roster。
- ModeDefinition 和本地响应预算。
- Viewer 冷却、可用性和近期公开事件。
- 当前相关 RoomMemory slice。

Director 不需要接收每个 Persona 的完整 Markdown。

### 9.2 本地预算

FastAPI 根据以下信息计算硬上限：

- normal/highlight response range。
- 事件类型和优先级。
- 点名约束。
- Viewer 冷却和可用性。
- 当前 Provider 压力和队列容量。

Director 在预算内决定准确 ViewerInstance ID，可以选择 0 个。

### 9.3 输出

Director 每波只调用一次，输出两个独立对象：

```text
CrowdDecision
MemeCandidate?
```

CrowdDecision 至少包含：

```text
selected_viewer_ids
reason_codes
evidence_event_ids
evidence_frame_indexes
created_at
expires_at
```

规则：

- 只能选择当前 epoch 池内实例。
- 不能包含弹幕正文。
- 不能重复、伪造或跨 Session 选择 ID。
- reason code 不是思维链。
- 当前波 AI 输出只能在下一波参与新 MemeCandidate 判断。

### 9.4 strict 和 resilient

- `strict`：Director 失败时本波安静并报告错误，用于开发和真实验收。
- `resilient`：本地确定性 fallback 在预算内选少量 Viewer。
- fallback 结果标记 `decision_source = fallback`。
- 不复用上一波 Director 结果。
- fallback 不能作为真实 Director 验收证据。

开发和验收默认 strict；普通体验可配置 resilient。

### 9.5 Ambient

- `natural` 模式默认无 ambient tick。
- `continuous` 模式允许受控 ambient tick。
- ambient 受冷却、响应预算和最大连续轮数限制。
- 多轮没有新的画面、文字或语音输入后强制安静。
- AI 输出不直接触发 ambient。

## 10. 独立 Viewer 请求

### 10.1 一实例一请求

- 每个 selected ViewerInstance 创建一个独立逻辑 Provider 请求。
- 不把多个人格或多个实例合并为一个 prompt。
- `persona_counts` 已在池分配时精确生效，不在调用阶段二次放大。
- shared summary 只复用视觉理解，不合并 Viewer 请求。

### 10.2 请求内容

```text
room_id
session_id
audience_epoch
observation_id
generation_request_id
viewer_instance_id
viewer_sequence
persona_revision
instance_variant
mode_context
frame_bundle_or_summary
user_text_and_final_voice
public_context
viewer_private_state
room_memory_slice
deadline_at
```

Provider 不能自行指定 Viewer 身份。

### 10.3 输出

每个 Viewer 每波只允许：

```text
action = barrage | silence
text?
reaction_type
evidence_event_ids
evidence_frame_indexes
```

规则：

- `barrage` 恰好一条短弹幕。
- `silence` 是合法结果，不算失败。
- 不保存或要求思维链。
- evidence 只能引用请求中实际提供的事件和帧。
- 画面事实至少引用一个 frame。
- 用户回应引用对应 text/voice event。
- 非法 evidence、身份或 Schema 拒绝提交。

### 10.4 并发和排队

- 初始 `max_in_flight_viewer_requests = 12`。
- 配置范围为 1 到 32。
- 超出并发槽位的 selected Viewer 进入有界队列。
- TTL 从 ObservationWave 创建时开始，不从真正 dispatch 时开始。
- telemetry 分别记录 selected、queued、dispatched、completed、silence、published、rejected 和 expired。

### 10.5 Latest-wins

每个 Viewer：

- 单调递增 `viewer_sequence`。
- 最多一个执行中请求。
- 最多一个等待中的最新请求。
- 新波覆盖尚未执行的旧任务。
- 旧任务即使返回也必须通过 epoch、sequence 和 deadline 围栏。

### 10.6 失败与重试

- 其他成功 Viewer 按完成顺序立即显示，不等待失败请求。
- 网络错误、429 或 5xx 且剩余 TTL 足够时，只重试同一 Viewer 一次。
- Schema 错误、内容拒绝、取消、stale 或 TTL 不足不重试。
- 重试失败保持缺席，不换另一个 Viewer 补位。

### 10.7 提交围栏

发布弹幕或产生任何状态副作用前重新核验：

- Session 仍可接受结果。
- epoch 当前。
- Viewer 仍属于池。
- sequence 当前。
- deadline 未过期。
- 请求未取消。
- evidence 和内容校验通过。

失败、取消、过期、stale 和被拒绝结果必须零副作用。

### 10.8 As-completed 和去重

- 合法结果独立发布，快请求不等待慢请求。
- 语义近似重复时保留最早通过结果。
- 未公开的重复结果不进入 ViewerPrivateState、RoomWorkingMemory、记忆或梗使用次数。

## 11. Context 和 Shared Brain

### 11.1 冻结 public context

同一波所有 Viewer 使用同一份 public context snapshot：

- 用户公开文字。
- final 用户语音。
- 已经公开的历史 AI 弹幕。
- 必要 system event。
- 当前模式和 Room 状态。

同波新弹幕从下一波才可见。

### 11.2 ViewerPrivateState

只保存：

- 该 Viewer 已公开的近期弹幕。
- 用户明确点名或引用它的互动。
- 当前关注点、临时情绪和冷却。
- 与它直接相关的公开事件引用。

不保存：

- 失败、取消、过期或被过滤输出。
- 隐藏推理。
- 完整原始画面。
- 整个 Room 的重复历史副本。

### 11.3 RoomWorkingMemory

- 所有成功公开内容都进入有界工作记忆。
- 所有 Viewer 下一波都可见。
- 使用事件 ID 和 revision，不复制多套文本。
- 窗口、token budget 和时间范围可配置。

### 11.4 RoomLongTermMemory

- 绑定 `room_id`，跨 Session 和模式共享。
- 所有 Viewer 有权访问同一记忆库。
- 每波先检索相关 memory slice，而不是把全部数据库塞进 prompt。
- selected Viewer 至少收到相同核心记忆片段。
- Persona 可以影响额外关注排序，但不能形成私有不可见长期库。
- Debug Trace 记录候选记忆、选择、分数和 memory revision。

### 11.5 来源和类型

所有公开 AI 弹幕可以进入工作记忆，但长期记忆按类型筛选：

- 用户偏好和现实事实必须有 user text、final voice、可信 screen event 或 system event 作为证据。
- AI 互动可以形成 `room_lore` 和共同经历。
- AI 输出不能单独证明用户或现实世界事实。
- 敏感身份、健康、财务等信息默认不保存。
- 每条记忆保留 evidence IDs、类型、revision、创建时间和撤销状态。

### 11.6 异步提取

- Barrage 发布不等待 memory extractor。
- 波次完成后按需要运行一次低优先级 RoomMemory extractor。
- 用户明确偏好优先；普通 AI 闲聊不必每波提取。
- memory extractor 使用独立并发槽位。
- 失败不撤回弹幕。
- 旧 epoch candidate 不能提交。
- 下一波只读取已提交 memory revision。

### 11.7 原子提交

候选幂等、evidence、memory revision、candidate outcome 和 memory head 前进在同一 SQLite 事务完成，并使用 compare-and-swap。

## 12. ModeMeme

- 绑定稳定 mode namespace。
- Director 每波可以输出一个独立 MemeCandidate。
- 候选可以引用当前真实输入和此前公开 AI 互动。
- 本波 AI 输出在下一波才参与 Director 的梗判断。
- 用户可以手动把公开弹幕提升为候选。
- 通过来源、长度、重复、内容安全和 namespace 校验后默认自动入库。
- 用户可关闭自动成长；关闭后进入待处理列表。
- 自动入库提供非打断通知和立即撤销。
- 新增、撤销、停用、恢复、置顶、使用次数和归档均持久化。
- MemeCandidate 不能直接生成 BarrageEvent。
- 模式切换不会把梗带入其他 namespace。

## 13. Provider 配置

首版使用：

- 一个活动 OpenAI-compatible Model Provider profile。
- 独立 StepFun ASR Provider。
- 角色级 model ID：

```text
director_model
viewer_model
memory_model
visual_summary_model
```

所有角色默认继承同一 model ID，高级配置允许覆盖。

合同保留 `provider_profile_id`，但首版不实现多个 endpoint、多套密钥或自动 Provider 故障切换。

执行当天必须重新探测：

1. `GET /v1/models`
2. Director 结构化输出
3. 图片输入
4. Viewer 最小并发
5. memory 结构化输出
6. final ASR

不把未验证模型 ID 写死为事实。HTTP 402、403、模型不可用或持续 429 会阻塞真实 E2E，但不阻塞确定性实现。

## 14. Agent-friendly Debug Contract

### 14.1 Schema 单一来源

- FastAPI/Pydantic 定义 HTTP 合同。
- OpenAPI 生成 TypeScript。
- WebSocket、Debug Trace 和 Replay Bundle 导出版本化 JSON Schema。
- Electron 不长期手写第二套同名合同。
- CI 校验 Schema snapshot、生成结果无漂移和协议不兼容。

### 14.2 Debug Trace

Runtime Context 与 Debug Trace 分离。每个请求至少记录：

```text
trace_id
room_id
session_id
audience_epoch
config_hash
observation_id
director_budget
director_decision
viewer_instance_id
viewer_sequence
persona_revision
instance_variant
public_context_event_ids
private_state_event_ids
memory_revision_and_ids
frame_hashes
prompt_manifest
provider_role_and_model
queue_and_provider_timing
response_status
validation_result
retry
stale_or_cancel_reason
memory_and_meme_side_effects
```

不记录凭据、思维链、原始音频或默认完整私人截图。

### 14.3 Debug API

开发模式提供 localhost + token 的只读机器接口：

- 当前 Session、epoch 和配置。
- Viewer 池、Persona 映射和微变体。
- ObservationWave。
- Director 预算和选择。
- 队列与请求状态。
- public context 和 ViewerPrivateState 引用。
- RoomMemory、ModeMeme 和提交历史。
- trace 查询和导出。

UI Debug Inspector 只能消费同一 API，不能维护第二套状态。

### 14.4 Headless harness

Agent 不依赖 Electron UI 即可：

1. 启动隔离后端。
2. 创建 Room 和 Session。
3. 应用 runtime spec。
4. 提交 frame、final transcript 和 user text fixtures。
5. 等待 Director/Viewer 结果。
6. 查询池、队列、共享记忆和梗。
7. 导出 trace。
8. 重放。
9. 停止并清理。

命令支持 JSON stdin/stdout、稳定 exit code、机器可读错误、固定 seed 和虚拟时间，并由 root `pnpm` 脚本提供统一入口。

### 14.5 Replay

- `recorded`：使用记录的 Provider 输出确定性重放，不产生外部费用。
- `live`：使用相同输入重新调用当前 Provider，对比结果和性能。
- 默认 recorded。
- live 必须显式开启。
- Bundle 包含 Schema version、seed、虚拟时间、事件序列、配置 hash 和脱敏输入引用。

### 14.6 测试隔离

- 每次 headless/E2E 使用独立 data dir、SQLite、端口、token 和 room。
- 默认 synthetic frames、固定 transcript 和 fake Provider。
- 只有显式 live 模式才能读取真实 Provider 配置。
- 测试不能修改正式工作区、RoomMemory 或 ModeMeme。
- 失败保留脱敏 artifact，成功自动清理临时环境。

## 15. 持久化和恢复

需要增加或扩展：

| 持久对象 | 目的 |
| --- | --- |
| `rooms` | 默认 Room 和长期共享记忆 namespace |
| `session_records` | idempotency、状态、epoch、config hash 和恢复 |
| `session_runtime_revisions` | 每次启动和热更新的 canonical spec |
| `session_viewer_instances` | Viewer ID、Persona、ordinal、微变体和生命周期 |
| `room_events` | 有界、可恢复的公开结构事件；不含原始媒体 |
| `room_memory_heads` | 当前长期记忆 collection revision |
| `room_memory_candidates` | 候选、证据、幂等键和结果 |
| 现有 memory/evidence 表 | 迁移为 room-scoped 长期记忆 |
| `mode_memes` | ModeMeme 当前状态 |
| `mode_meme_events` | 入库、撤销、编辑、归档和恢复日志 |

### 15.1 Session start

- request 携带 `client_request_id` 和 canonical runtime spec。
- 同 request ID + 同 hash 返回同一 Session。
- 同 request ID + 不同 hash 返回 409。
- 并发重复启动只创建一条 Session 和一套资源。
- 使用 starting -> running 两阶段提交。

### 15.2 后端恢复

- 后端重启后恢复相同逻辑 `session_id`。
- 递增 `audience_epoch`。
- 清除旧队列和网络任务。
- 从持久配置、Viewer 池和有界 Room events 重建工作状态。
- 不恢复原始音频、完整帧或旧 Provider 请求。
- Electron 显示 recovered 状态。
- 校验失败则停止恢复并返回机器错误，不能静默创建新状态。

## 16. Transport v2

HTTP、WebSocket、Electron 和 Overlay 升级到 protocol v2。

关键版本：

```text
protocol_version = 2
audience_contract_version = 1
trace_schema_version = 1
replay_schema_version = 1
```

BarrageEvent 至少包含：

```text
room_id
session_id
audience_epoch
observation_id
generation_request_id
viewer_instance_id
persona_id
display_name
viewer_sequence
reaction_type
evidence_refs
text
created_at
expires_at
```

缺失 Viewer 身份的旧事件必须拒绝，不能降级成系统观众。

## 17. UI 必须能力

### 17.1 Mode 编辑

- 每种 Persona 的人数可编辑为 0 到 32。
- 各 Persona 人数合计为 1 到 32，并实时显示。
- normal/highlight response range。
- ambient 类型。
- 视觉模式和 FrameBundle 参数。

### 17.2 Session 控制

- 启动时显示 Room、mode、Viewer 数和 Provider capability。
- “应用到当前会话”。
- 开发模式自动应用开关。
- 当前 config revision、hash 和 epoch。
- 回滚上一版本。
- pause、resume、clear、stop 保持原有明确语义。

### 17.3 Viewer 和 Overlay

- 显示确定性实例别名。
- 文本输入支持结构化 `@` 点名。
- 每条 AI 弹幕内部可追溯到 Viewer、Persona、Observation 和 request。
- 界面继续明确标注 AI 观众，不能伪装真人在线人数。

### 17.4 Memory 和 Meme

- RoomLongTermMemory 查看、修改、撤销、删除和重置。
- ModeMeme 自动成长开关、候选列表、撤销、停用、恢复、置顶和归档。
- 所有 UI 操作调用 backend API，不维护第二套事实状态。

## 18. 实施阶段

### Phase 0：基线和 Provider capability

- 记录 Git、Node、pnpm、Python、uv、migration head。
- 跑当前测试和类型检查。
- 重新执行模型列表、结构化文本、图片、并发和 ASR 探测。
- 生成脱敏 capability artifact。

退出条件：基线明确；真实 Provider 不可用时标记 live Gate `BLOCKED`。

### Phase 1：文档、Schema 和失败测试

- 修订 PRODUCT、DECISIONS、ARCHITECTURE 和 BACKEND_DESIGN。
- 冻结 Room、Session、PersonaTemplate、ViewerInstance、ModeDefinition、ObservationWave。
- 先写 workspace migration、pool allocation、epoch、event、trace 和 replay Schema 测试。

退出条件：旧文档不再把 32 Persona 当作 32 AI，也不再把独立调用写成未决默认。

### Phase 2：Backend contracts 和生成类型

- 实现 Pydantic HTTP/WS/debug/replay models。
- 导出 OpenAPI 和 JSON Schema。
- 运行一次 `pnpm contracts`。
- 加协议版本和 mismatch 测试。

退出条件：Python 是合同来源，TypeScript 无手写漂移。

### Phase 3：Headless 和 Debug 骨架

- 建立 JSON CLI、隔离 data dir、fake providers、virtual clock 和固定 seed。
- 建立 Debug Trace writer、query API 和 artifact layout。
- 能用最小 fixture 创建/停止 Session 并查询状态。

退出条件：后续功能都能通过 headless 测试，而不是等待 UI。

### Phase 4：Desktop workspace v2

- Persona document version/revision/hash。
- Mode viewer count、weights、response ranges、namespace 和 revision。
- v1 -> v2 migration。
- allocation preview。
- 加载失败锁写和 rejected backup 保持有效。

退出条件：六个内置模式迁移准确，no-op 不递增 revision，模式覆盖互相隔离。

### Phase 5：Room、Session、Viewer 持久化和热更新

- Alembic migration。
- Room 和 runtime revision repositories。
- idempotent start。
- deterministic Viewer pool 和 aliases。
- atomic apply、rollback、epoch 和 selective reconciliation。
- crash recovery。

退出条件：并发 start、两类 crash window、热更新和恢复测试通过。

### Phase 6：ObservationWave

- 文本、final voice、screen 和 ambient triggers。
- coalescing。
- FrameBundle buffer、三种选择策略和参数热更新。
- mention resolver。
- direct frames/shared summary 手动切换。

退出条件：同一输入生成稳定 wave，partial 不触发，FrameBundle 可查询和重放。

### Phase 7：AI Director

- Director port/provider/schema。
- local budget。
- exact Viewer selection。
- MemeCandidate。
- strict/resilient fallback。
- evidence validation。

退出条件：Director 不能产生弹幕，非法 ID 被拒绝，fallback 明确标记。

### Phase 8：独立 Viewer runtime

- per-viewer mailbox。
- global semaphore 和有界队列。
- independent Provider calls。
- retry、TTL、latest-wins 和 commit fence。
- barrage/silence schema。
- as-completed publish 和 dedup。

退出条件：独立调用数、快慢顺序、stale 零副作用和 retry 行为可证明。

### Phase 9：Realtime 和 Overlay

- protocol v2。
- Viewer-aware events。
- Electron client/preload/renderer integration。
- alias、点名、pause/clear/stop 行为。

退出条件：真实 Overlay 每条弹幕都能通过 trace ID 追溯。

### Phase 10：Shared Brain

- RoomWorkingMemory。
- memory retrieval slice。
- async extractor。
- source/type rules。
- memory head CAS 和管理 API。

退出条件：所有 Viewer 下一波共享公开上下文；跨 Session 恢复 Room 长期记忆。

### Phase 11：ModeMeme

- v1 本地 meme 幂等迁移到 backend。
- Director candidate validation。
- 自动入库开关。
- undo、restart、archive 和 mode isolation。

退出条件：经历共享但梗不串模式，candidate 不能直接显示。

### Phase 12：Replay 和确定性 E2E

- recorded replay。
- synthetic CS2/CSGO fixture。
- fake Director/Viewer/memory。
- 热更新、恢复、共享记忆和 ModeMeme 全链路。

退出条件：artifact 标记 `deterministic_proof = true`，不声称真实 Provider。

### Phase 13：真实 StepFun E2E

使用固定 CS2/CSGO 片段和脚本化输入：

1. 普通跑图，验证 silence 和不编造。
2. 高光连续击杀。
3. 明显失误。
4. 用户 final 语音点名 Viewer。
5. 用户文字回应。
6. 6657 热更新指定人格人数。
7. RoomLongTermMemory 跨 Session 恢复。
8. MemeCandidate、自动入库、撤销和 backend restart。

退出条件：artifact 标记 `credentialed_provider_proof = true`，无 fake fallback。

### Phase 14：完整质量门

```powershell
pnpm contracts
pnpm typecheck
pnpm test
pnpm build
uv run --project apps/backend ruff check apps/backend
```

另行完成：

- headless E2E。
- Electron 控制窗和 Overlay smoke。
- live provider E2E。
- redaction scan。
- generated contract diff。
- 当前工作树和未关联 worktree 审计。

## 19. CS2/CSGO 验收 Oracle

真实模型不比较固定文案，检查行为合同：

- 结构合法且身份准确。
- evidence 引用属于本波。
- Director 选择符合场景允许的 Persona 类别。
- 6657 热更新后各人格实例数与配置人数完全一致。
- 普通跑图允许 silence，不编造击杀。
- 点名目标 Viewer 返回明确结果。
- 快请求先显示。
- 过期、取消和 stale 不显示、不写状态。
- RoomWorkingMemory 对下一波所有 Viewer 可见。
- RoomLongTermMemory 跨 Session 和模式可见。
- ModeMeme 不串 namespace。
- 内容边界继续生效。

确定性 fake 测试断言精确事件；真实 Provider 使用类别、身份、证据和状态变化作为机器 oracle，并保留少量人工观感检查。

## 20. 完成标准

- [x] 32 被正确实现为 ViewerInstance 上限。
- [x] 现有 32 Persona 仅作为模板库。
- [x] 六个 Mode 迁移值准确。
- [x] 6657 人格人数热更新精确重平衡实例。
- [x] 同 Persona 实例具有不同 ID、别名、微变体和短期状态。
- [x] Viewer 都访问同一 Room Shared Brain。
- [x] Mode 切换不清空 Room 长期记忆。
- [x] 经历共享，梗按 Mode 隔离。
- [x] 编辑自动保存，显式 apply 原子热更新。
- [x] 开发模式支持保存即应用。
- [x] 热更新可回滚，旧 epoch 零副作用。
- [x] direct frames 默认，shared summary 可手动切换。
- [x] FrameBundle 数量、窗口和策略可热更新。
- [x] partial voice 不触发，final voice 幂等触发。
- [x] 点名约束 Director。
- [x] 每波一次 Director。
- [x] Director 只选准确 Viewer ID。
- [x] AI 输出不直接递归触发下一波。
- [x] 每个 selected Viewer 一次独立调用。
- [x] 首版没有多 Viewer batching。
- [x] 每 Viewer 每波 0 或 1 条。
- [x] Viewer 输出带 reaction type 和 evidence refs。
- [x] 快结果先显示，失败 Viewer 不换人。
- [x] queue、TTL、latest-wins 和 retry 可观察。
- [x] stale/cancelled/expired 零副作用。
- [x] Debug API、JSON trace 和 headless harness 可用。
- [x] recorded replay 确定性通过。
- [x] live replay 只有显式开启才调用 Provider。
- [x] 相同 Session 可在后端重启后以新 epoch 恢复。
- [x] synthetic CS E2E 通过。
- [ ] credentialed StepFun E2E 通过。
- [x] Provider 不可用时结果明确为 `BLOCKED`。
- [x] 全部质量门通过。

2026-07-24 验收说明：上面的已勾选项由当时 Windows 工作树的定向测试、全量质量门、recorded replay 和 Electron + FastAPI + Overlay smoke 共同证明。真实 StepFun 能力探测 `7/7` 通过，但最终实网运行的 12 次生产 Director 请求中有 7 次在 30 秒后被上游超时，未产生可绑定的 Viewer 调用，因此完整 credentialed E2E 保持未勾选。失败运行的验证器和脱敏产物已在测试减负时移除；如需恢复真实 Provider 验收，应重新实现可运行的 runner 并生成当前版本证据，不得复用历史结果。macOS 权限、点击穿透、采集释放和打包启动未在本机验证。

## 21. Out Of Scope

- 多 Room UI。
- 多 Model Provider endpoint。
- 自动 Provider 故障切换。
- 多 Viewer batching。
- 根据性能自动切换视觉模式。
- AI 弹幕直接递归触发 AI。
- 原始音频、完整画面和思维链持久化。
- 云同步、账号、真实直播平台和真人观众。
- 跨设备共享记忆。
- 运行时随机生成核心 Persona。
- 未经版本和 epoch 保护的隐式热更新。

## 22. Git 检查点

建议按可独立验证的边界提交：

```text
docs: lock viewer runtime requirements
feat(contracts): add room viewer runtime schemas
feat(debug): add headless runtime harness
feat(desktop): migrate audience workspace to v2
feat(backend): persist room sessions and viewer pools
feat(backend): support atomic audience hot reload
feat(backend): build observation waves
feat(backend): add audience director
feat(backend): run independent viewer requests
feat(desktop): render viewer-aware barrages
feat(backend): add shared room memory
feat(backend): persist mode meme lifecycle
test(e2e): add deterministic viewer runtime replay
test(e2e): verify credentialed provider pipeline
```

每个提交先通过对应定向测试。不得通过重写历史或隐藏 fallback 把失败描述成成功。
