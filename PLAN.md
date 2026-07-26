# Job Market Monitor — 执行方案 (PLAN.md)

> 本文档是项目的单一事实来源(source of truth),面向人和 AI 协作者。
> 任何 AI session 在动手前应先完整阅读本文档。实现与本方案冲突时,以本方案为准;
> 若方案本身需要修改,先更新本文档再改代码。
> 状态标记:每个 Phase 完成后,把对应章节的 `status: todo` 改为 `status: done` 并附一行说明。
> 英文版见 `PLAN.en.md`,两份内容需保持同步:改动任一份时同步更新另一份。

---

## 1. 项目概述

**目标**:一个 job market monitoring system,持续采集三个地区(新加坡、美国、澳洲)的
tech 岗位数据,用 LLM 分类到自定义 taxonomy,提供:

1. **岗位层**:筛选出用户关注方向的岗位,通过 Telegram 即时 alert + digest 推送
2. **市场层**:岗位量趋势、地区对比、技能需求演变、新兴 title 涌现等市场分析看板

**性质**:个人项目,但按 portfolio 标准建设 —— 干净的架构、README、架构图、
LLM 分类 eval、可复现的部署。

**用户背景**:当前岗位是 Data Analyst,考虑向 DS/AI 方向转型。

**核心设计原则**:
- pipeline → DB → 前端 三层解耦,前端(Streamlit)未来可替换为 Next.js 而不动其余部分
- **数据尽早开始积累**:趋势分析依赖时间序列,ingest 上线优先级最高,看板可以晚做
- 只用合法、稳定的数据源(公开 API),不爬取 ToS 禁止的站点(LinkedIn/Indeed/Seek 网页)
- 全部跑在免费 tier 上,唯一持续成本是 LLM API(目标 < $15/月)

## 2. 已确定的决策

| 决策点 | 结论 |
|---|---|
| 定位 | 找岗位 + 市场分析并重 |
| 数据源 | ATS 公开 API 为主(Greenhouse/Lever/Ashby)+ Adzuna 补充市场广度 |
| 分类 | LLM(claude-haiku-4-5)结构化抽取,配人工标注 eval set |
| 看板 | Streamlit(先),架构解耦,未来可换 Next.js |
| Alert | Telegram bot(即时推送 + 每日 digest) |
| 存储 | SQLite,DB 文件随 repo 提交(见 §5 说明);数据量大后可迁 Supabase |
| 调度 | GitHub Actions cron |
| 语言 | Python 3.11+ |

## 3. 岗位 Taxonomy

分类输出必须是以下 slug 之一。**Tier 决定用途**:tier 1 = 目标岗位(可触发 alert、
进入岗位层看板);tier 2 = 市场信号(只进市场层统计,不 alert);tier 3 = 其他。

| slug | Tier | 说明 / 判定要点 |
|---|---|---|
| `data_scientist` | 1 | DS、Applied Scientist、Decision Scientist 等 |
| `ai_engineer` | 1 | AI Engineer、LLM Engineer、GenAI Engineer;偏应用层 LLM 开发 |
| `ml_engineer` | 1 | MLE、ML Platform、ML Infra;含偏 MLE 的 Research Engineer |
| `agentic_engineer` | 1 | Agent Engineer、Agentic Engineer、AI Agent 开发;新兴 title,重点跟踪 |
| `forward_deployed_engineer` | 1 | FDE、Forward Deployed SWE;含明显客户驻场性质的 Solutions Engineer/Architect |
| `engineering_analyst` | 1 | Engineering Analyst、Analytics Engineer、Product/Business Analyst 中偏技术的 |
| `data_analyst` | 1 | 用户当前岗位。既是 fallback 选项,也用于对比 DA vs DS/MLE 市场供需,辅助转型判断 |
| `software_developer` | 2 | 通用 SWE(前端/后端/全栈/mobile/infra)。**非目标岗位**,仅作为行业大盘基线:tech 招聘整体冷热的参照系 |
| `other` | 3 | 以上都不是(PM、设计、销售、运营等) |

边界规则(写进分类 prompt):
- "Member of Technical Staff" 类模糊 title → 依据 job description 判断,不依据 title
- Research Scientist(偏发论文)→ `other`;Research Engineer(偏工程)→ `ml_engineer`
- 同时符合多类时选最具体的:`agentic_engineer` > `ai_engineer` > `ml_engineer` > `software_developer`
- Solutions Engineer 默认 `other`,除非 JD 明确 forward-deployed/驻场/客户现场交付 → `forward_deployed_engineer`

## 4. 系统架构

```
GitHub Actions cron (每日 1-2 次, UTC 时间避开高峰)
  └─ python -m jobmon.run_pipeline
       1. ingest    : 各 source adapter 拉取 → 统一 schema
       2. upsert    : 写入 SQLite;更新 first_seen/last_seen/is_active
       3. classify  : 对新增且未分类的岗位调 LLM → classifications 表
       4. alert     : 规则引擎匹配 → Telegram 即时推送 + digest
       5. commit    : 把更新后的 data/jobs.db 提交回 repo

Streamlit Cloud (独立部署,只读 repo 里的 jobs.db)
  └─ dashboard/app.py : 岗位层 + 市场层两组页面
```

各步骤幂等:pipeline 重跑不产生重复数据、重复 alert、重复计费的 LLM 调用。

## 5. 数据模型 (SQLite)

DB 文件:`data/jobs.db`,随 repo 提交(单文件、天然版本化、Streamlit Cloud 可直接读)。
控制体积:`description` 截断到 8000 字符;预计年增量在几十 MB 量级,可接受。
若超过 ~200MB 或需要并发写,迁移到 Supabase Postgres(schema 兼容,SQLAlchemy 层切换)。

```sql
CREATE TABLE jobs (
  id             INTEGER PRIMARY KEY,
  source         TEXT NOT NULL,       -- greenhouse | lever | ashby | adzuna
  source_job_id  TEXT NOT NULL,       -- 源内唯一 ID
  company        TEXT NOT NULL,
  title          TEXT NOT NULL,
  location_raw   TEXT,
  country        TEXT,                -- SG | US | AU | OTHER (adapter 内规则解析)
  is_remote      INTEGER DEFAULT 0,
  url            TEXT,
  description    TEXT,                -- 纯文本,≤8000 字符
  posted_at      TEXT,                -- 源提供的发布时间, ISO 8601, 可空
  first_seen     TEXT NOT NULL,       -- 本系统首次见到
  last_seen      TEXT NOT NULL,       -- 本系统最近一次见到
  is_active      INTEGER NOT NULL DEFAULT 1,
  closed_at      TEXT,                -- 连续 2 次 run 未见到时标记
  dedupe_key     TEXT,                -- norm(company)+norm(title)+country 的 hash
  UNIQUE(source, source_job_id)
);

CREATE TABLE classifications (
  job_id           INTEGER PRIMARY KEY REFERENCES jobs(id),
  category         TEXT NOT NULL,     -- taxonomy slug
  tier             INTEGER NOT NULL,
  seniority        TEXT,              -- intern|junior|mid|senior|staff_plus|manager|unknown
  skills           TEXT,              -- JSON array, 规范化小写, 如 ["python","langgraph","mcp"]
  salary_min       REAL,
  salary_max       REAL,
  salary_currency  TEXT,
  visa_sponsorship TEXT,              -- yes|no|unknown
  confidence       REAL,              -- 0-1, 模型自报
  model            TEXT NOT NULL,
  classified_at    TEXT NOT NULL
);

CREATE TABLE alerts_sent (             -- alert 幂等: 同一 job+rule 只发一次
  job_id    INTEGER REFERENCES jobs(id),
  rule_name TEXT,
  sent_at   TEXT NOT NULL,
  PRIMARY KEY (job_id, rule_name)
);

CREATE TABLE ingest_runs (             -- 运行日志, 用于监控 pipeline 自身健康
  run_at     TEXT NOT NULL,
  source     TEXT NOT NULL,
  ok         INTEGER NOT NULL,
  jobs_seen  INTEGER,
  jobs_new   INTEGER,
  error      TEXT
);
```

**生命周期规则**:
- upsert 时命中 `(source, source_job_id)` → 更新 `last_seen`,`is_active=1`
- 某源本次 run 成功但某岗位未出现,且连续 2 次 run 未出现 → `is_active=0`, 记 `closed_at`
  (源本身拉取失败时不做下线判定,避免误杀)
- `closed_at - first_seen` ≈ time-to-fill,市场层指标

**跨源去重**:`dedupe_key = sha1(normalize(company) + "|" + normalize(title) + "|" + country)`。
ATS 数据为准,Adzuna 命中已有 dedupe_key 时只更新 last_seen 不新建。
normalize:小写、去标点、去 "inc/pte/ltd" 等后缀、压缩空白。v1 用精确指纹,模糊匹配留作 backlog。

## 6. 数据源规格

### 6.1 Greenhouse (无需认证)
- `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true`
- 字段映射:`id→source_job_id`, `title`, `location.name→location_raw`, `absolute_url→url`,
  `content→description`(HTML,需转纯文本), `updated_at→posted_at`(近似)

### 6.2 Lever (无需认证)
- `GET https://api.lever.co/v0/postings/{site}?mode=json`
- `id`, `text→title`, `categories.location→location_raw`, `hostedUrl→url`,
  `descriptionPlain→description`, `createdAt→posted_at`(epoch ms)

### 6.3 Ashby (无需认证)
- `GET https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true`
- `jobs[]`: `id`, `title`, `location`, `jobUrl→url`, `descriptionHtml→description`,
  compensation 字段若有则直接入库(可省一部分 LLM 抽取)
- 只取 `isListed: true` 的岗位

### 6.4 Adzuna (需 app_id + app_key,免费注册)
- `GET https://api.adzuna.com/v1/api/jobs/{country}/search/{page}` ,country ∈ {us, au, sg}
- 参数:`what` 用 taxonomy 关键词轮询(如 "data scientist", "machine learning engineer"),
  `results_per_page=50`, `max_days_old=2`(只拉增量)
- 免费额度有限(注册后确认实际配额),adapter 必须:限速、失败退避、当日配额用尽则跳过并记录
- Adzuna 的 `category`/`salary_min`/`salary_max` 字段直接入库
- 注意:Adzuna description 是截断摘要,分类质量低于 ATS 全文,confidence 会偏低,属预期

### 6.5 公司列表 `config/companies.yaml`
```yaml
# 条目格式
- name: Anthropic
  ats: greenhouse        # greenhouse | lever | ashby
  token: anthropic       # board_token / site / org
  note: ai-lab
```
- Phase 1 生成种子列表 **150-300 家**,构成:AI labs 与 AI infra(Anthropic/OpenAI/
  Mistral/HuggingFace/Scale/…)、美国大中型 tech、新加坡本地+区域 HQ(Grab/Sea/
  GovTech/Stripe SG/…)、澳洲(Canva/Atlassian/…)、有 FDE 岗位传统的公司(Palantir/
  Anthropic/OpenAI/Databricks 等)
- 生成时需逐一验证 token 有效(请求返回 200 且有岗位),无效的注释掉并标注
- 用户会 review 这份列表;后续增删只改 YAML 不改代码

### 6.6 可选扩展(backlog,不在主线)
- HN "Who is Hiring" 月度 thread(Algolia API)——早期信号源
- MyCareersFuture(新加坡)——SG 深度补充

## 7. LLM 分类模块

- **模型**:`claude-haiku-4-5`($1/$5 per MTok)。用户明确选择便宜模型;不要擅自升级
- **调用方式**:Anthropic Python SDK。**首选 Message Batches API**(50% 折扣,24h 内完成,
  对每日 cron 完全够用):当日新岗位攒成一个 batch 提交,下次 run 收结果;当日岗位少(<20)
  时直接同步调用。分类 prompt 的固定部分(taxonomy 定义+规则)放 system 并标 cache_control
- **输入**:title + company + location + description(截断 4000 字符,分类够用)
- **输出**:structured outputs(`output_config.format` json_schema 或 `messages.parse()` +
  Pydantic),schema 即 classifications 表字段。禁止自由文本解析
- **幂等**:只处理 classifications 中不存在的 job_id;batch 的 custom_id 用 job_id
- **成本估算**:~300 新岗位/日 × ~1.2K tokens 输入(大部分 cache 未命中部分)+ ~150 输出
  ≈ $0.5/日,Batch API 后 ≈ $0.25/日 ≈ **$8/月上限**;实际新增岗位量可能更低
- **降本备选**(先不做):title 正则先分流明显 case,只有模糊 title 走 LLM

### Eval(portfolio 重点)
- `eval/labeled.jsonl`:从真实数据抽 150-200 条,人工标注 category(+ seniority 抽查)
- 分层抽样:每个 tier-1 类至少 15 条,含故意的难例(MTS、Solutions Engineer、Research Engineer)
- `python -m jobmon.eval` 输出:整体 accuracy、per-category precision/recall、混淆矩阵
- 目标:tier-1 类 macro-F1 ≥ 0.85;结果写进 README
- prompt 改动必须重跑 eval,记录在 `eval/RESULTS.md`(prompt 版本 → 指标)

## 8. Alert 模块 (Telegram)

- 发送:`POST https://api.telegram.org/bot{TOKEN}/sendMessage`(MarkdownV2),
  chat_id 为用户私聊。TOKEN/CHAT_ID 走 secrets
- **即时 alert**:命中 `config/alert_rules.yaml` 任一规则的新岗位,单条推送
- **每日 digest**:一条汇总消息:各 tier-1 类新增数(按地区)、Top 新岗位列表(≤15 条,
  链接)、异常信号(某类单日新增超过 30 天均值 2σ 时提示)
- 幂等靠 `alerts_sent` 表

```yaml
# config/alert_rules.yaml 格式
- name: anthropic-sg-any
  company: [Anthropic]           # 可选, 列表, 不区分大小写
  country: [SG]                  # 可选
  category: null                 # 可选, taxonomy slugs; null = 任意 tier-1
- name: fde-anywhere
  category: [forward_deployed_engineer, agentic_engineer]
- name: sg-target-roles
  country: [SG]
  category: [data_scientist, ai_engineer, ml_engineer, data_analyst]
```
规则字段之间 AND,字段内列表 OR。tier-2/3 岗位永不即时 alert。

## 9. 看板 (Streamlit)

`dashboard/app.py`,多页应用,数据只读 `data/jobs.db`。图表遵守 dataviz skill
(实现看板的 session 先加载该 skill)。

**页面 1 — 岗位浏览(岗位层)**
- 筛选:category(默认全部 tier-1)、country、seniority、company、是否 active、关键词
- 列表:title / company / 地点 / 类别 / 薪资(若有)/ first_seen / 链接;按 first_seen 倒序

**页面 2 — 市场趋势(市场层)**
- 各 tier-1 类岗位量周度趋势(线图);software_developer 作灰色基线叠加
- 三地区对比(SG/US/AU 分面或分组)
- DA vs DS/MLE 供给对比(转型参考)

**页面 3 — 技能与新兴信号**
- skills 词频 Top N 及其周度变化(重点跟踪 agent 相关:langgraph、mcp、agentic 等)
- 新兴 title 检测:jobs.title 中首次出现且随后持续出现的 n-gram
- time-to-fill 分布(closed 岗位)

**页面 4 — Pipeline 健康**(内部页):ingest_runs 摘要、各源最近成功时间、分类积压数

## 10. Repo 结构

```
job-market-monitor/
├── PLAN.md                      # 本文档
├── PLAN.en.md                   # 英文版, 与本文档保持同步
├── README.md                    # Phase 5 完善(架构图、eval 结果、截图)
├── pyproject.toml               # 依赖: httpx, pydantic, anthropic, pyyaml, streamlit, pytest, ruff
├── config/
│   ├── companies.yaml
│   ├── alert_rules.yaml
│   └── taxonomy.py              # taxonomy 定义(slug/tier/描述), prompt 与看板共用
├── src/jobmon/
│   ├── run_pipeline.py          # 入口, 按 §4 顺序编排
│   ├── db.py                    # schema 创建 + upsert + 查询封装
│   ├── models.py                # Pydantic: NormalizedJob, Classification
│   ├── ingest/
│   │   ├── base.py              # adapter 协议: fetch(config) -> list[NormalizedJob]
│   │   ├── greenhouse.py / lever.py / ashby.py / adzuna.py
│   ├── classify/
│   │   ├── prompts.py           # system prompt(引用 taxonomy.py, 含边界规则)
│   │   └── classifier.py        # batch 提交/回收 + 同步 fallback
│   ├── alerts/
│   │   ├── rules.py
│   │   └── telegram.py
│   └── lifecycle.py             # active/closed 判定, dedupe
├── dashboard/app.py
├── eval/
│   ├── labeled.jsonl
│   ├── run_eval.py
│   └── RESULTS.md
├── data/jobs.db                 # 随 repo 提交
├── tests/                       # adapter 用 fixture JSON 离线测试; db/lifecycle/rules 单测
└── .github/workflows/pipeline.yml
```

## 11. 运行环境与 Secrets

GitHub Actions secrets(本地开发放 `.env`,gitignore):

| 变量 | 用途 |
|---|---|
| `ANTHROPIC_API_KEY` | LLM 分类 |
| `ADZUNA_APP_ID` / `ADZUNA_APP_KEY` | Adzuna API |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alert |

workflow 要点:cron 每日 2 次(如 `0 1,13 * * *`);任一源失败不阻断其他源;
结束后 commit `data/jobs.db` + push(处理并发:pull --rebase 后重试);
pipeline 顶层异常 → 发一条 Telegram 错误通知(系统自监控)。

## 12. 分阶段执行计划

### Phase 1 — 数据打底 `status: todo`
最高优先级,完成后数据即开始积累。
1. repo 脚手架:pyproject、目录、pytest/ruff、README 占位
2. `models.py` + `db.py`(建表、upsert、生命周期)
3. Greenhouse/Lever/Ashby adapter + 离线 fixture 测试
4. 种子公司列表(150-300 家,逐一验证 token,见 §6.5)→ 用户 review
5. Adzuna adapter(限速+配额处理)
6. `run_pipeline.py`(先只 ingest+upsert)+ GitHub Actions workflow + DB 回提交
7. **验收**:Actions 连续 3 天成功运行;jobs 表 ≥ 3000 条;三国都有数据;
   重跑不产生重复;某源故意断网时其余源正常

### Phase 2 — LLM 分类 `status: todo`
1. `taxonomy.py` + 分类 prompt(§3 边界规则写入)
2. classifier(Batch API 主路径 + 同步 fallback + 幂等)
3. 接入 pipeline;对已积累的存量岗位跑一次全量分类
4. 标注 150-200 条 → eval 跑通 → 迭代 prompt 至 tier-1 macro-F1 ≥ 0.85
5. **验收**:eval 达标且结果记录在 RESULTS.md;月成本折算 < $15;分类幂等

### Phase 3 — Telegram Alert `status: todo`
1. bot 创建(用户手动:BotFather 建 bot、获取 chat_id)→ secrets 配置
2. rules 引擎 + 即时推送 + 每日 digest + alerts_sent 幂等 + 失败通知
3. **验收**:构造测试岗位命中规则收到推送;重跑 pipeline 不重复推送

### Phase 4 — Streamlit 看板 `status: todo`
1. 四个页面(§9);实现前加载 dataviz skill
2. 部署到 Streamlit Community Cloud
3. **验收**:公网可访问;筛选/趋势图正确;移动端可读

### Phase 5 — Portfolio 打磨 `status: todo`
1. README:项目动机、架构图(mermaid)、eval 结果表、看板截图、设计取舍
   (为什么 ATS API 而非爬虫、为什么 SQLite-in-repo、为什么 LLM 而非规则)
2. backlog 择优:HN Who's Hiring、模糊去重、周报(LLM 生成市场摘要)、MyCareersFuture
3. **验收**:README 让陌生工程师 10 分钟看懂系统并能本地跑起来

## 13. 工程约定(所有 session 遵守)

- Python 3.11+;类型标注;ruff format/lint;测试跑 `pytest`
- adapter 是纯函数:输入 config,输出 `list[NormalizedJob]`,不碰 DB;网络错误抛
  `IngestError` 由编排层捕获记录
- 所有时间存 UTC ISO 8601 字符串
- LLM 调用:模型固定 `claude-haiku-4-5`;structured outputs;禁止解析自由文本;
  prompt 固定部分启用 prompt caching
- 不硬编码 secrets;不提交 `.env`
- 新增依赖需有明确理由,倾向标准库 + httpx + pydantic 的最小集
- commit 信息英文,常规式(feat:/fix:/chore:);pipeline 自动提交用 `chore(data): daily update`

## 14. 开放问题(不阻塞 Phase 1)

- [ ] 种子公司列表的最终构成 —— Phase 1 生成后用户 review
- [ ] alert_rules 的初始规则 —— Phase 3 前用户确认
- [ ] Adzuna 实际配额 —— 注册后确认并回填 §6.4
- [ ] 新兴 title 检测的具体算法(简单 n-gram 首现 vs 更严谨的统计)—— Phase 4 决定
