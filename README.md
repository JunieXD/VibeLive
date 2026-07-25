# AI Audience（众声）

> 同一幕，众声不同。

AI Audience 是为创作者准备的 AI 虚拟观众席。它能够看懂用户正在分享的画面，听懂
主播的表达，并让一群拥有不同身份和性格的 AI 观众通过实时弹幕参与现场。

它不负责制造虚假流量，也不代替真正的直播平台。它解决的是开播前和零观众阶段最直接
的问题：当没有人回应时，创作者很难判断自己的内容是否清楚、有趣，节奏是否合适，
更难练习真实的互动感。

有了 AI Audience，即使一个人开播，也能面对一个会观察、会思考、会接话的观众席。

## AI Audience 能带来什么

- **有人看懂正在发生什么**：观众会结合近期画面、主播语音和文字消息作出回应，而不是
  随机发送与现场无关的套话。
- **不止一种声音**：不同观众拥有自己的性格、偏好和关注点，面对同一个瞬间可以产生
  不同看法。
- **真正可以对话**：用户可以用语音或文字与整个房间交流，也可以点名某位观众继续
  之前的话题。
- **关系能够延续**：同一场直播中的观众会保持身份和近期状态，不会每次发言都像一个
  完全陌生的人。
- **现场氛围可以选择**：用户可以切换不同观众模式，改变在线人数、表达风格和房间
  气氛。
- **弹幕不会挡住操作**：观众回应可以显示在桌面覆盖层或独立互动窗口中，用户仍然可以
  正常使用正在分享的应用。

## 一次完整的使用体验

1. 选择需要分享的屏幕、窗口或应用，并选择麦克风；Windows 用户还可以开启系统声音。
2. 选择一种观众模式，确认当前会参与直播的 AI 观众。
3. 开始直播后，观众会根据画面变化、主播表达和文字消息实时作出反应。
4. 用户可以回应弹幕、点名某位观众，或者把新的问题抛给整个房间。
5. 需要安静时可以暂停或清屏，也可以对单个观众进行禁言、解除禁言或移出房间。
6. 停止直播后，本场观众离场；下一次开播会形成新的现场。

## 适合谁

- 想练习直播表达、解说节奏和临场互动的新主播。
- 希望在游戏过程中获得即时反馈和陪伴的玩家。
- 需要预演课程、产品演示或线上分享的讲述者。
- 想在发布前测试内容是否容易理解、是否能引发讨论的创作者。
- 希望独自创作时不再面对完全安静屏幕的用户。

## 隐私与产品边界

AI Audience 创建的是本地虚拟直播体验，不向公网推流，也不创建真人可以加入的公开
房间。所有虚拟观众都会明确标识为 AI，不会被包装成真实用户或真实流量。

屏幕帧、语音转写和必要的房间上下文只会发送给用户主动配置并启用的服务。用户配置、
观众记忆和日志不会写入仓库，模型与 ASR 凭据通过 Electron `safeStorage` 保存。

## 技术实现

- Electron、React、TypeScript、Tailwind CSS 和 Zustand 构建桌面端。
- FastAPI、Python、SQLAlchemy 和 SQLite 构建本地后端。
- WebSocket 与二进制协议承载实时状态、采集数据和弹幕事件。
- StepFun ASR 提供语音转写。
- OpenAI-compatible API 接入用户选择的多模态模型。
- Pydantic、OpenAPI 和生成的 TypeScript 类型维护跨进程合同。

## 仓库结构

```text
apps/desktop      Electron + React 桌面端
apps/backend      FastAPI + uv 本地后端
packages/contracts 由 Pydantic/OpenAPI 生成的 TypeScript 合同
resources         观众预设等随应用分发的静态资源
tests             跨应用端到端测试与夹具
docs              产品、架构和决策文档
```

## 开发环境

- Node.js 24+
- pnpm 11+
- Python 3.11 或 3.12
- uv 0.11+

## 开始开发

```bash
pnpm install
uv sync --project apps/backend --group dev
pnpm contracts
pnpm dev
```

`pnpm dev` 会用同一份临时本地令牌启动 FastAPI 和 Electron。首次联调需要在桌面端“设置”中填写 OpenAI-compatible 模型地址、模型名称、模型 API Key 和 StepFun ASR API Key，然后选择画面与麦克风并开始直播。完整步骤见[真实管线联调](./docs/REAL_PIPELINE.md)。

常用命令：

```bash
pnpm typecheck
pnpm test
pnpm build
```

用户配置、观众记忆和日志不写入仓库。正式应用应将它们存放在 Electron `app.getPath("userData")` 对应目录；StepFun 和模型 Provider 的凭据使用 Electron `safeStorage` 保存。

## 文档

- [产品说明](./docs/PRODUCT.md)
- [系统架构](./docs/ARCHITECTURE.md)
- [后端详细设计](./docs/BACKEND_DESIGN.md)
- [真实管线联调](./docs/REAL_PIPELINE.md)
- [决策与开放问题](./docs/DECISIONS.md)
