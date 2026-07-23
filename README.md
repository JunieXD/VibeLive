# ADVX Live

ADVX Live 是一个跨平台 AI 虚拟直播间。Electron 桌面端采集用户选择的画面、麦克风和文字输入，FastAPI 本地后端使用 `faster-whisper` 进行本地 ASR，并通过用户配置的 OpenAI-compatible 多模态模型生成具有稳定身份的 AI 观众弹幕。

## 仓库结构

```text
apps/desktop      Electron + React 桌面端
apps/backend      FastAPI + uv 本地后端
packages/contracts 由 Pydantic/OpenAPI 生成的 TypeScript 合同
resources         观众预设和本地模型清单
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

需要开发本地语音链路时，再安装 ASR 依赖：

```bash
uv sync --project apps/backend --group dev --extra asr
```

常用命令：

```bash
pnpm typecheck
pnpm test
pnpm build
```

运行时下载的 ASR 模型、用户配置、观众记忆和日志不写入仓库。正式应用应将它们存放在 Electron `app.getPath("userData")` 对应目录。

## 文档

- [产品说明](./docs/PRODUCT.md)
- [系统架构](./docs/ARCHITECTURE.md)
- [决策与开放问题](./docs/DECISIONS.md)
