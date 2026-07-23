# 实时 Ingest 数据面协议

> 状态：Implemented
>
> 本文定义 Electron 与本地后端之间的实时输入合同。`IngestService` 与
> WebSocket Handler 已按此合同接入，`/ws` 同时承载控制消息和实时输入。

## 1. 范围与兼容性

数据面使用与控制面相同的 `/ws` 连接，并要求已完成 `client.hello` 握手。JSON
控制消息带 `protocol_version: 1`；二进制包使用独立的 envelope version。此次增加
为加性合同，以下既有消息的字段和语义不变：

- `client.hello` / `backend.ready`
- `client.ping` / `backend.pong`
- `session.status`、`barrage.event`、`protocol.error`

Handler 会先校验会话、重复 `input_id`、消息大小和顺序，再调用 Application 的
`IngestPort`。拒绝一个输入不会关闭已完成握手的连接，除非它同时违反现有 WebSocket
协议规则。

## 2. JSON 消息

| 方向 | `type` | 必填字段 | 含义 |
| --- | --- | --- | --- |
| client -> backend | `client.text.submit` | `session_id`, `input_id`, `created_at_ms`, `text` | 提交一条用户文字输入。 |
| client -> backend | `client.audio.commit` | `session_id`, `input_id`, `committed_at_ms` | 提交同一 `input_id` 的单个音频 binary envelope，形成一个 ASR 段。 |
| backend -> client | `ingest.ack` | `session_id`, `input_id`, `input_kind`, `stage`, `accepted_at_ms` | `stage` 为 `received` 或 `committed`。 |
| backend -> client | `ingest.rejected` | `code`, `message`，以及可选的 `session_id`、`input_id`、`input_kind` | 输入被拒绝，身份无法可靠解析时关联字段省略。 |

`input_kind` 的值为 `text`、`audio` 或 `frame`。`ingest.rejected.code` 为
`invalid_input`、`session_not_active`、`duplicate_input`、`unknown_input`、
`out_of_order`、`payload_too_large`、`unsupported_format`、
`unsupported_binary_version`、`unsupported_media_type` 或
`malformed_binary_envelope`。运行时尚未注入 Ingest Pipeline 或其容量暂不可用时返回
`pipeline_unavailable`。

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
和总字节数，并在会话结束时清理。`Observation` 只能携带 `FrameRef`，其 `data_ref` 是
不透明的本地引用，不得是 data URI、base64 正文或可恢复的媒体内容。

需要像素的 Provider Adapter 通过 `FrameResolver` 以 `session_id` 和 `FrameRef` 解析临时
的 `ResolvedFrame`。原始音频、图片正文和 `ResolvedFrame.body` 不得写入日志、Room Event、
Observation、SQLite 或生成请求的结构化元数据。
