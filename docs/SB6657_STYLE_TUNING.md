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

## 刷新

在仓库根目录执行：

```powershell
python scripts/fetch_sb6657_corpus.py --page-size 500 --delay 0.35
python scripts/profile_sb6657_corpus.py `
  --output apps/backend/src/advx_backend/providers/model/room_6657_style_profile.json
```

刷新后必须审查 metadata 的 `complete`、`reported_total`、`unique_count` 和 SHA，再运行：

```powershell
uv run --project apps/backend pytest apps/backend/tests/test_room_6657_style_guidance.py
pnpm --filter @advx/desktop test -- src/shared/audience/audience.test.ts
uv run --project apps/backend ruff check apps/backend scripts
```
