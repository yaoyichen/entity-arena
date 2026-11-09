# AGENTS.md

实体归能（Entity Resolution）演示台：**双轨数据集**（Febrl3 实测主轨 + 合成剧本轨，
页面顶部一键切换、四视图跟随）→ 双模型前后对比 → 单文件离线演示页 +
**页内试玩台**（play 视图：任意挑对离线打分，内嵌全量真实分数矩阵）+
MCP 试用通道（connect 视图 + `scripts/er_mcp_server.py`）。
背景与决策见 `docs/adr/`，完整用法见 `README.md`。

## 硬约束（违反即 bug）

1. **零第三方依赖**：运行时纯标准库（Python ≥3.11）。
2. **单文件、零外链**：`build/demo.html` 断网双击能开；唯一允许的 URL 字符串是
   SVG 命名空间常量（有测试锁定）。
3. **确定性**：一切随机走 seed（默认 42）；同 seed 两次生成逐字节一致（有测试）；
   实测轨由已入库分数离线确定性重建（同样双跑锁定）。
4. **失败不静默**：构建带 self-check 标记；数据撞车/引用悬空直接 raise。
5. **诚实标注（按轨声明）**：合成轨 footer 声明 SYNTHETIC DATA、分数是代理评分
   （ADR 0002）；实测轨 footer 声明 Febrl3 公开基准、微调分数=真实模型实测、
   逐对基线=字面相似度代理。三处标注：状态条 / 统计条 / provenance。

## 常用命令

```bash
python3 scripts/make_mock_data.py            # 生成 data/ 合成轨（确定性）
python3 scripts/update_open_benchmark.py     # 生成 data/open_benchmark.json（开源基准轨，从已入库报告解析）
python3 scripts/build_open_cases.py             # 生成 data/open_cases.json（实测主轨全量：网络三态+Arena+指标板+Explain 卡，从已入库分数离线复算）
python3 scripts/build_demo.py --open         # 构建 build/demo.html 并打开（三个数据文件缺一即 raise）
PYTHONPATH=src python3 -m unittest discover -s tests   # 测试（或装 pytest 后 pytest）
```

改了 `synth/simulate/layout` → 必须重跑生成+构建；改了 `page/theme` → 重跑构建；
重新评测了平台模型 → 重跑 `update_open_benchmark.py` + 构建；
重新取样了案例村向量 → 覆盖 `data_open/village_scores.json` 后重跑
`build_open_cases.py` + 构建（village_scores 缺席时构建显式提示，
页面只出合成轨——优雅降级路径保留，平台再故障时用）。

## 代码地图

- `src/entity_arena/synth.py` 核心剧本人物（手工）+ 填充人物（seed 生成）、关系网
- `src/entity_arena/simulate.py` 字段相似度、双模型代理评分、检索评测、指标
- `src/entity_arena/layout.py` 网络两套坐标（FR + 分位归一 + 光环防撞）
- `src/entity_arena/page.py` 演示页模板 + 前端 JS（双语文案、六个视图、深链；
  play=试玩台·页面内离线打分、connect=MCP 接入指南，这两个视图不随双轨切换）
- `src/entity_arena/theme.py` 色板与 CSS
- `scripts/make_mock_data.py` 合成数据入口；`scripts/build_demo.py` 页面构建入口
- `scripts/make_open_dataset.py` 开源基准（Febrl）→ inf-retriever 训练平台上传包；
  源数据在 `data_open/`（随仓库提交）；训练/评测走 MCP `inf-retriever`（token 在
  `~/.claude.json`，不进仓库），流程见 skill `inf-retriever-finetune`
- `scripts/update_open_benchmark.py` 平台评测报告（`data_open/eval_reports/`，随仓库
  提交作溯源）→ `data/open_benchmark.json`；基线口径漂移会 raise。指标板第二板块
  （开源基准轨：真实模型实测）由它供数，双轨 footer 各自诚实标注
- `scripts/pick_open_village.py` 从 Febrl3 测试实体选"案例村"（228 条记录/
  78 实体/1223 影子对，预算 `--budget 230`；**选村对模型分数盲**，规则写在
  provenance 里，避免挑案例之嫌）→ `data_open/village.json`
- `scripts/fetch_village_embeddings.py` 调平台已部署微调服务的 `/v1/embeddings`
  取真实向量（`EMB_TOKEN` 环境变量传令牌；向量只落 `.tmp/`，不入库）
- `scripts/live_pair_score.py` 现场演示打分器：两条记录（案例村实体/rid/自由文本）
  → 部署服务实时余弦 + θ 判定；演示剧本见 `docs/live-demo.md`（深链 `?q=`
  直达 Arena 指定剧本查询）
- `scripts/er_mcp_server.py` **entity-er MCP 服务器**（stdio JSON-RPC，纯标准库）：
  把案例村打分器交给任意 MCP 客户端。三工具 `er_score_pair`/`er_find_records`/
  `er_about`；双模式：rid 对走 `village_scores.json` 离线矩阵（零配置、确定性=
  实时），自由文本走 `EMB_TOKEN` 实时调用（embed_client.py 同源）。仓库根
  `.mcp.json` 已带 Claude Code 项目级注册（无秘钥）；使用文档 `docs/mcp.md`、
  页面「接入」视图（`?view=connect`，命令/示例可一键复制）；行为由
  `tests/test_mcp_server.py` 锁定（离线分数必须等于入库矩阵值）
- `src/entity_arena/embed_client.py` embeddings 客户端共用层（TLS1.2 强制+重试、
  余弦），live_pair_score 与 er_mcp_server 共用，别在两处各写一份
- `scripts/build_open_cases.py` 案例村 + 真实成对分数 → `data/open_cases.json`
  （θ 扫描 F1 最大化 → 断言链 → 组件纯度；溯源分数写 `data_open/village_scores.json`
  入库，默认从该文件离线复算）。**实测主轨全量数据都由它供**：
  ① 网络三态：原始=影子关系纠缠簇 → 基线=字面相似度**代理**打分（平台无法逐对
  部署基模，权重/口径写进 provenance，误连红线+漏连红虚线⊘+混合组件红光环）→ 微调=真实断言
  组件；② Arena 六查询 F1–F6（六类极值规则：大家族/缺失/影子纠缠/基线误连/
  基线漏连/错字风暴，规则全文进 `provenance.arena_rule`，类别池空则缺席）；
  ③ 指标板（方向性口径与 simulate.py 逐条对齐，五分桶
  missing>conflict>typo_storm>typo>lookalike，空池诚实置 null 渲染"—"，
  recall@5=1.0 照登不隐藏、口径文本说明，规则进 `provenance.metrics_rule`）；
  ④ Explain 六张实测卡 R1–R6（极值规则挑选、距 θ ≥0.01 防 GPU 抖动翻判定，
  对叙事盲；R5/R6 注脚带基线代理分数）；⑤ 试玩台 play 矩阵：全量对按 1e-4
  量化（u16 小端 b64，rid 排序上三角行主序）入 `open_cases.json`，构建期逐对
  断言量化判定与全精度一致（量化翻判定即 raise），完整性由
  `tests/test_playground.py` 锁定（与 village_scores 逐对相等、判定集==links）
- `src/entity_arena/febrl.py` Febrl 数据共用工具（实体切分/序列化/索引）
- **部署要点（2026-11-08 实测）**：平台 serving 当日整体故障过一轮（新产物
  `train`/`pipeline`、旧产物一律 ~4min FAILED；`deploy_model` 起的服务全挂），
  后经**手动在控制台创建服务**（`embedding-febrl3-er-gemma`）恢复并完成取样。
  再遇部署故障：先查 Deployed Services 有没有现成可用服务，别急着重训。
  另两个坑：FAILED 部署留僵尸 serving 名（重试换新名）；平台网关掐 OpenSSL 3.x
  默认 ClientHello（后量子密钥共享），`fetch_village_embeddings.py` 已强制
  TLS1.2 + 重试，别改回去。

## 视觉语言（继承 retrieval-forge ADR 0005 · 2026-11-09 精修深空 + 角色色全过六查）

- 深空底 `#0c1117`，靠**明度**分层，重要的更亮；token 化排版见 `theme.py`
  （字号阶 11–28、8px 网格、圆角 4/8/16、`--shadow-1/2`、`.eyebrow` 小眉标）；
- 全场只有两个角色色：**cyan `#10a6ad` = 微调模型/正确**，**violet `#787bd2` = baseline**；
  红 `#cf6d58` 是批注红，只给**错误断言**，不做警报色滥用。角色三色经
  dataviz 六查（OKLab ΔE + Machado CVD：dark 带内、彩度 ≥0.10、相邻 CVD ≥8、
  正常视觉 ≥15、对底/面板 ≥3:1），由 `tests/test_theme_palette.py` 锁定，
  **改色先跑该测试**；彩色文字用亮阶 `cyan-b/violet-b/red-b/cyan-l`（面板 ≥4.5:1）；
- **平台家族签名**：135° 青→紫渐变 `--sig`（`#22d3ee→#a78bfa`，取自 INF 控制台
  styles.css 同款），只做界面铬件——logo/导航激活态/双轨切换器/卡片左线 `.gline`
  /玻璃上缘高光；**绝不做数据编码**，数据标记永远用实色角色色
  （chrome-only 约定由 test_theme_palette.py 锁定）；
- 图表按 dataviz 标记规格：细柱（≤24px）、数据端 4px 圆角基线端方、发丝实线
  网格、值只标关键列（其余进 tooltip/表格）、文字穿文字色不穿系列色、
  ≥2 系列必带图例（`chartLegend()`）；
- 任何新 UI 先截图过一遍涉及视图（`?view=` 深链直达，六视图见 page.py）再合入
  （headless 截图记得
  `--virtual-time-budget=3000`，否则 `.view.active` 入场动画会被拍在半途）。

## 数据契约（换真模型时）

合成轨：`results.json`：`{models: {baseline: {qid: [{r, s}×5]}, tuned: …}}`
（**key 是 record_id**，不是 query_id——适配层按 `q.record_id` 查）；
`metrics.json`：overall + by_scenario。替换这两个文件，页面零改动。
开源基准轨：`open_benchmark.json`（由 `data_open/eval_reports/` 报告重建）——
重新评测后用新报告覆盖该目录再跑 `update_open_benchmark.py` 即可。
实测主轨：`open_cases.json`（由 `data_open/village.json` + `village_scores.json`
重建；village_scores 缺席时优雅降级为仅合成轨、切换器隐藏）——重新取样向量后
覆盖 `village_scores.json` 再跑 `build_open_cases.py` 即可。必备键含
`arena`（F1–F6 查询）与 `metrics`（方向性指标+分桶），缺即 raise（防半新半旧
数据进页面）。深链：`?track=open|synth` 全局双轨、`?q=F1|Q2` / `?case=R3|P2`
按前缀自动切轨、`?pair=ridA,ridB` 试玩台直达指定对。
