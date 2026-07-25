# 6657 风格调优

## 目标

`room-6657` 使用 sb6657 的公开弹幕数据学习表达结构和节奏，但不把外部弹幕当作可直接播放的语料池。运行时仍然必须先回应当前画面、主播话语或公开房间上下文，再按 6657 模式的 Persona 生成新文本。

## 数据边界

- 数据来源：`https://hguofichp.cn:10086/machine/Page`。
- 抓取脚本只发送 `Accept` 和明确的 `User-Agent`，不发送 `dpahjdoiaw` 或 `siteToken`。
- 只调用只读分页接口，不调用投稿、计数、投票、登录或 AI 生成接口。
- 全量原始 JSONL 只保存在被 Git 忽略的 `.advx-data/sb6657/`。
- 仓库只提交聚合统计画像，不提交外部弹幕原文、用户名或可直接复读的示例。
- 运行时不访问 sb6657 网络接口；上游不可用不会阻断直播。
- 只有上游总数稳定、抓取数量匹配且明确到达 `lastPage=true` 时，语料才可生成画像。

sb6657 后端不开源，也没有公开 SLA、版本兼容或限流承诺。抓取结果只能作为可刷新、可降级的外部风格证据。

## 当前画像

2026-07-24 完整分页抓取报告：

- 上游报告 22,024 条记录，精确文本去重后得到 21,714 条。
- 语料 canonical SHA-256：`78318e2e6f04065fd024850891cf5a9a6c74d3c96e0339182e02c34e83158457`。
- 全量句长中位数 36 字；高复制量四分位切片共 5,460 条，中位数 39 字。
- 热门切片问号出现率约 12.8%，感叹号约 14.7%，受控重复结构约 40.4%。
- 热门切片括号旁白约 9.2%，命令或建议口吻约 11.6%。

画像文件是 `apps/backend/src/advx_backend/providers/model/room_6657_style_profile.json`。它只包含统计、来源和哈希，不包含 `barrage` 字段。

## 生成链路

桌面端为 `room-6657` 的 13 个 Persona 提供模式内覆盖，分别约束问号、嘴硬、拱火、节目效果、梗结构重写、抽象联想、受控复读、反向预测和本场回扣。
旧工作区中未编辑的 revision 1 内置模式会自动升级到 revision 2；用户已编辑的更高 revision 保持不变。

后端模型适配器仅在 `mode_id == "room-6657"` 时注入紧凑 `style_profile`：

- 长度范围来自高复制量语料切片。
- 标点、复读、括号和命令语气使用聚合频率，而不是固定套话。
- 每次请求只携带当前 Persona 对应的一条风格镜头。
- 风格画像不是画面证据、房间记忆或事实来源。
- System Prompt 明确禁止重建或逐字复刻来源语料。

其他模式不接收这份画像。

## SkillOpt 持续优化

项目内的 6657 生成规则以 `.codex/skills/room-6657-style/SKILL.md` 为唯一人工维护源。
`scripts/sync_room_6657_skill.py` 将其中的运行指令和 13 个 Persona 镜头编译成后端使用的确定性 JSON；运行时不读取 SkillOpt 状态，也不连接外部弹幕数据库。

微软开源的 [SkillOpt](https://github.com/microsoft/SkillOpt) 固定在
`resources/skillopt/skillopt.lock.json` 记录的提交。源码下载到 Git 忽略的
`.advx-data/tools/SkillOpt/`，审查证据保存在本地 `.skillopt-sleep/`，
不会修改用户级 SkillOpt 配置或项目记忆。

真实模型调用在系统临时目录中的最小工作区执行。该工作区初始只含一份禁止文件和工具访问的
`AGENTS.md`；子进程使用环境变量白名单和临时 `HOME`，临时 `CODEX_HOME`
只复制认证文件，不加载用户插件、MCP、hooks、历史会话或记忆。优化结束后，staging
搬回项目并立即用 SHA-256 绑定基线 Skill、候选、门禁报告、manifest、审核任务集和
SkillOpt 上游提交。

首次安装与校验：

```powershell
python scripts/run_room_6657_skillopt.py bootstrap
python scripts/run_room_6657_skillopt.py validate
python scripts/run_room_6657_skillopt.py dry-run --backend mock
```

真实优化使用项目内已经人工审核的 5 个训练任务、4 个验证任务和 3 个最终保留任务：

```powershell
python scripts/run_room_6657_skillopt.py run --backend codex
python scripts/run_room_6657_skillopt.py status
python scripts/run_room_6657_skillopt.py evaluate --backend codex `
  --skill .skillopt-sleep/staging/<timestamp>/proposed_SKILL.md
```

真实运行最多提出两处有界修改，关闭 memory 演进，并使用验证集门禁。通过门禁的候选只会写入
`.skillopt-sleep/staging/`；不会自动进入运行时。审查 `report.md` 和
`proposed_SKILL.md`，并通过 3 个最终任务后，先记录显式审批：

```powershell
python scripts/run_room_6657_skillopt.py approve `
  --staging .skillopt-sleep/staging/<timestamp> `
  --reason "candidate preserves Persona and safety contracts"
```

然后使用同一个 staging 路径采用：

```powershell
python scripts/run_room_6657_skillopt.py adopt `
  --staging .skillopt-sleep/staging/<timestamp>
```

如果候选虽然通过模型门禁，但破坏 Persona 区分、短句节奏或其他产品合同，应记录拒绝而不是追求分数：

```powershell
python scripts/run_room_6657_skillopt.py reject `
  --staging .skillopt-sleep/staging/<timestamp> `
  --reason "candidate overrides a Persona-specific contract"
```

`evaluate` 只有使用真实 Codex 且评测 staging 候选时才生成可采用的 `evaluation.json`。
`approve` 将人工理由绑定到该评测和候选字节；`adopt` 会再次检查 provenance、最终评测、
审批、当前 live baseline、标题、13 个 Persona、安全锚点、文档增长上限和可编译性，
再同步后端 JSON。采用和回滚都持有项目级文件锁，并在写入前再次比较哈希。

每次采用都会在对应 staging 目录留下 `backup/SKILL.md` 和 `adoption.json`，
可显式回滚：

```powershell
python scripts/run_room_6657_skillopt.py rollback `
  --staging .skillopt-sleep/staging/<timestamp>
```

回滚使用 compare-and-swap：只有当前 live Skill 仍是该次采用的候选、运行时生成物也未变化，
且备份哈希与原基线一致时才会执行。任务集不包含 sb6657 原文、用户名或私有会话。
SkillOpt 优化的是可审查的生成规则，不是模型权重，也不是可逐字检索的弹幕库。

### 2026-07-24 受控轮次

- 在两轮正确拒绝未改善候选后，真实 Codex 优化在 5 个训练任务和 4 个验证任务上，将
  mixed gate 从 `0.594` 提升到 `0.785`。
- 未参与训练或验证的最终任务分别验证短问号 Persona、嘴硬转认可和 no-copy 边界；
  hard score 均为 `1.00`，soft score 分别为 `1.00`、`0.82`、`0.88`。
- 最终采用的两条学习规则只收紧 `fun_seeker` 的事件/结果锚点和
  `cheat_suspector` 的赞叹式复盘边界。
- 已采用 Skill SHA-256 为
  `74df137558e2466b6f8e7eb9155a226ceb85d60c70c20b5520ae004a55add817`。
- 候选、最终评测、显式审批、采用记录和回滚备份均由本地 staging 中的哈希链绑定。

## 刷新

在仓库根目录执行：

```powershell
python scripts/fetch_sb6657_corpus.py --page-size 500 --delay 0.35
python scripts/profile_sb6657_corpus.py `
  --output apps/backend/src/advx_backend/providers/model/room_6657_style_profile.json
python scripts/sync_room_6657_skill.py
```

刷新后必须审查 metadata 的 `complete`、`reported_total`、`unique_count` 和 SHA，再运行：

```powershell
python scripts/run_room_6657_skillopt.py validate
pnpm test
uv run --project apps/backend ruff check apps/backend scripts
```
