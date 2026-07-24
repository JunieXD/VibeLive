# 实时 Ingest 数据面协议

> 状态：Implemented
>
> 本文定义 Electron 与本地后端之间的实时输入合同。`IngestService` 与
> WebSocket Handler 已按此合同接入，`/ws` 同时承载控制消息和实时输入。
>
> 本文记录当前已实现的 realtime protocol v4，并保留一个发布周期的 v3 兼容路径。
> 二进制 ingest envelope 有独立的版本空间；v4 客户端使用 `ADVX-BIN/3`。

## 1. 范围与兼容性

数据面使用与控制面相同的 `/ws` 连接，并要求已完成 `client.hello` 握手。新客户端发送
`protocol_version: 4` 与 `supported_protocol_versions: [4, 3]`，后端选择双方支持的
最高版本，并在 `backend.ready` 中回显。旧客户端仍可只声明 v3。握手后的每条 JSON
消息必须继续使用本连接协商出的版本：

- `client.hello` / `backend.ready`
- `client.ping` / `backend.pong`
- `session.status`、`barrage.event`、`protocol.error`
- `ingest.ack`、`ingest.rejected`

Handler 会先校验会话、重复 `input_id`、消息大小和顺序，再调用 Application 的
`IngestPort`。拒绝一个输入不会关闭已完成握手的连接，除非它同时违反现有 WebSocket
协议规则。

版本门禁语义是确定的：双方没有共同版本，或握手后的 JSON 消息偏离已协商版本时，
后端先发送 `protocol.error`（`code: version_mismatch`、
`supported_version: 4`），再以 `4406` 关闭连接。握手 token 无效以 `4401` 关闭；
握手超时以 `4408` 关闭；JSON schema 不合法或消息顺序不合法以 `4400` 关闭。上述
协议错误不同于单个 ingest 输入被拒绝。

## 2. JSON 消息

| 方向 | `type` | 必填字段 | 含义 |
| --- | --- | --- | --- |
| client -> backend | `client.text.submit` | `session_id`, `input_id`, `created_at_ms`, `text` | 提交一条用户文字输入。 |
| client -> backend | `client.audio.commit` | `session_id`, `input_id`, `source`, `committed_at_ms` | 仅 v3：提交先前发送的音频 envelope。v4 音频在 binary 消息内原子提交。 |
| client -> backend | `client.voice.activity` | `session_id`, `source`, `occurred_at_ms` | 对应来源恢复说话，用于延长该来源当前语音轮次。 |
| backend -> client | `ingest.ack` | `session_id`, `input_id`, `input_kind`, `stage`, `accepted_at_ms` | `stage` 为 `received` 或 `committed`。 |
| backend -> client | `ingest.rejected` | `code`, `message`，以及可选的 `session_id`、`input_id`、`input_kind` | 输入被拒绝，身份无法可靠解析时关联字段省略。 |
| backend -> client | `asr.transcript` | `source`, `text`, `final`, `started_at_ms`, `ended_at_ms`, `utterance_id`, `revision` | 部分或最终 ASR 文本；最终文本已成功成为 Room Event。 |
| backend -> client | `barrage.event` | `barrage` | Viewer 输出；包含 Room、Session、epoch、Observation、生成请求、Viewer 身份、意图、目标与 evidence refs。 |

`input_kind` 的值为 `text`、`audio` 或 `frame`。`ingest.rejected.code` 为
`invalid_input`、`session_not_active`、`duplicate_input`、`unknown_input`、
`out_of_order`、`payload_too_large`、`unsupported_format`、
`unsupported_binary_version`、`unsupported_media_type` 或
`malformed_binary_envelope`。运行时尚未注入 Ingest Pipeline 或其容量暂不可用时返回
`pipeline_unavailable`。

`ingest.rejected` 是输入级拒绝：后端发送拒绝消息后保持已握手连接，客户端可以修正后
继续提交。可恢复的 binary envelope 错误（版本、media type、长度或编码）同样映射为
`ingest.rejected`，不会因为单个坏输入直接关闭连接。只有 WebSocket frame/消息本身违反
协议规则时才进入上一节的 `protocol.error` 关闭语义。

v4 音频顺序为：客户端发送一条带 `microphone` 或 `system_audio` 来源、
`turn_id` 和协调标记的 `ADVX-BIN/3` envelope；后端完成 push + commit 后只返回一次
`committed` ACK。客户端不再发送 `client.audio.commit`。v3 兼容路径仍按
binary -> `received` ACK -> `client.audio.commit` -> `committed` ACK 执行。

同一 `input_id`、时间、格式、正文和提交元数据的精确重试是幂等的；同一 `input_id`
携带不同内容会被拒绝。图片没有 commit 消息，成功后返回 `frame` 的 `received` ACK；
客户端在 ACK 超时时最多原样重发一次，并继续处理后续帧。断线会清理该连接尚未提交的
音频和对应 ASR 缓冲。

麦克风和系统声音使用相同 `turn_id` 协调。麦克风 envelope 可以声明
`system_audio_required: true`。若麦克风 final 已完成而系统声音在 3 秒内没有完成，
后端以 `system_audio_degraded: true` 仅用麦克风触发观察；迟到的系统声音仍持久化，
但不会再次触发同一轮。

## 3. 二进制 Envelope

每个 WebSocket binary frame 恰好包含一个 envelope。当前 `ADVX-BIN/3` 使用 9 字节
固定头和一段 UTF-8 JSON header；整数使用网络字节序（big-endian），Python 固定头格式
为 `>4sBI`。

| 偏移 | 字段 | 编码 | 说明 |
| --- | --- | --- | --- |
| 0 | magic | 4 bytes | ASCII `ADVX`。 |
| 4 | version | `u8` | 当前为 `3`。 |
| 5 | JSON header length | `u32` | 后续 JSON header 的 UTF-8 字节数，最大 4096。 |
| 9 | JSON header | bytes | 紧凑 JSON 对象。 |
| `9 + header_length` | body | bytes | 音频 PCM 或图片正文。 |

JSON header 必须包含：

- `media_type`、`session_id`、`input_id`、`captured_at_ms`、`format`、
  `body_length`；
- 音频还必须包含 `source` 与 `turn_id`，麦克风可包含
  `system_audio_required`；
- 图片不得包含音频来源或协调字段。

总长度必须严格等于 `9 + header_length + body_length`，JSON 中的
`body_length` 必须与实际正文完全一致。

`format` 是实际 wire format 描述，不绑定 Provider。音频可使用
`audio/pcm;rate=16000;channels=1;format=s16le`，图片可使用 `image/webp`、
`image/jpeg` 等；具体 Adapter 支持集在接入时校验，不能把供应商字段写入本协议。

| 限制 | 上限 |
| --- | ---: |
| `session_id` / `input_id` / `format` | 各 128 UTF-8 bytes |
| audio body | 2,097,152 bytes |
| image body | 4,194,304 bytes |
| JSON header | 4,096 bytes |
| 完整 v3 binary envelope | 4,198,409 bytes |

v3 兼容连接继续接受旧的 v1 24 字节 header（`>4sBBHHQHI`）和 v2 25 字节 header
（`>4sBBBHHQHI`）。v1 audio 统一映射为 `microphone`，v1 image 映射为无来源。
realtime v4 音频必须使用 v3 envelope；未知版本仍返回
`unsupported_binary_version`。

长度、magic、版本、类型或 UTF-8 不合法时，不得尝试把正文交给 ASR 或 FrameStore。
应用层将错误映射为 `ingest.rejected` 并保持连接；只有同时违反 WebSocket 协议规则时
才会关闭连接。

## 4. 帧所有权与隐私

`FrameInput` 的 bytes 只能进入有界的 `FrameStore`。Store 同时声明最大帧数、单帧字节数
和总字节数，并在会话结束时清理。当前 Observation 合同和目标 `ObservationWave` 都只能
携带 `FrameRef`；其 `data_ref` 是不透明的本地引用，不得是 data URI、base64 正文或可恢复
的媒体内容。

需要像素的 Provider Adapter 通过 `FrameResolver` 以 `session_id` 和 `FrameRef` 解析临时
的 `ResolvedFrame`。原始音频、图片正文和 `ResolvedFrame.body` 不得写入日志、Room Event、
ObservationWave、SQLite、Debug Trace、replay bundle 或生成请求的结构化元数据。
