# 实时 Ingest 数据面协议

> 状态：Implemented
>
> 本文定义 Electron 与本地后端之间的实时输入合同。`IngestService` 与
> WebSocket Handler 已按此合同接入，`/ws` 同时承载控制消息和实时输入。
>
> 本文记录当前已实现的 protocol v3。二进制 ingest envelope 仍有独立的 version
> 字段，当前为 `1`；它与 WebSocket JSON 协议版本不是同一个版本空间。

## 1. 范围与兼容性

数据面使用与控制面相同的 `/ws` 连接，并要求已完成 `client.hello` 握手。JSON
控制消息带 `protocol_version: 3`；二进制包使用独立的 envelope version。连接必须以
`client.hello` 声明 v3，后续每一条 JSON 客户端消息也必须继续声明 v3：

- `client.hello` / `backend.ready`
- `client.ping` / `backend.pong`
- `session.status`、`barrage.event`、`protocol.error`
- `ingest.ack`、`ingest.rejected`

Handler 会先校验会话、重复 `input_id`、消息大小和顺序，再调用 Application 的
`IngestPort`。拒绝一个输入不会关闭已完成握手的连接，除非它同时违反现有 WebSocket
协议规则。

版本门禁语义是确定的：握手或握手后的 JSON 消息声明非 v3 时，后端先发送
`protocol.error`（`code: version_mismatch`、`supported_version: 3`），再以 `4406`
关闭连接。握手 token 无效以 `4401` 关闭；握手超时以 `4408` 关闭；JSON schema
不合法或消息顺序不合法以 `4400` 关闭。上述协议错误不同于单个 ingest 输入被拒绝。

## 2. JSON 消息

| 方向 | `type` | 必填字段 | 含义 |
| --- | --- | --- | --- |
| client -> backend | `client.text.submit` | `session_id`, `input_id`, `created_at_ms`, `text` | 提交一条用户文字输入。 |
| client -> backend | `client.audio.commit` | `session_id`, `input_id`, `committed_at_ms` | 提交同一 `input_id` 的单个音频 binary envelope，形成一个 ASR 段。 |
| backend -> client | `ingest.ack` | `session_id`, `input_id`, `input_kind`, `stage`, `accepted_at_ms` | `stage` 为 `received` 或 `committed`。 |
| backend -> client | `ingest.rejected` | `code`, `message`，以及可选的 `session_id`、`input_id`、`input_kind` | 输入被拒绝，身份无法可靠解析时关联字段省略。 |
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

音频顺序为：发送一条 `audio` binary envelope，收到 `received` ACK 后发送
`client.audio.commit`，再收到 `committed` ACK。一个 binary envelope 对应一个
`input_id` 和一个有界 ASR 段。图片没有 commit 消息，接收成功后返回 `frame` 的
`received` ACK。

## 3. 二进制 Envelope

每个 WebSocket binary frame 恰好包含一个 envelope。字段使用网络字节序（big-endian），
可变长字符串使用 UTF-8，不包含 NUL。固定 header 是 24 字节，Python 编解码格式为
`>4sBBHHQHI`。

| 偏移 | 字段 | 编码 | 说明 |
| --- | --- | --- | --- |
| 0 | magic | 4 bytes | ASCII `ADVX`。 |
| 4 | version | `u8` | 当前为 `1`。 |
| 5 | media type | `u8` | `1` = audio，`2` = image。 |
| 6 | session ID length | `u16` | `session_id` 的 UTF-8 字节数。 |
| 8 | input ID length | `u16` | `input_id` 的 UTF-8 字节数。 |
| 10 | captured at | `u64` | UTC Unix 毫秒。 |
| 18 | format length | `u16` | `format` 的 UTF-8 字节数。 |
| 20 | body length | `u32` | 正文的字节数。 |
| 24 | variable data | bytes | `session_id`、`input_id`、`format`、正文，按此顺序连接。 |

总长度必须严格等于：

```text
24 + session_id_length + input_id_length + format_length + body_length
```

`format` 是实际 wire format 描述，不绑定 Provider。音频可使用
`audio/pcm;rate=16000;channels=1;format=s16le`，图片可使用 `image/webp`、
`image/jpeg` 等；具体 Adapter 支持集在接入时校验，不能把供应商字段写入本协议。

| 限制 | 上限 |
| --- | ---: |
| `session_id` / `input_id` / `format` | 各 128 UTF-8 bytes |
| audio body | 1,048,576 bytes |
| image body | 4,194,304 bytes |
| 完整 binary envelope | 4,194,712 bytes |

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
