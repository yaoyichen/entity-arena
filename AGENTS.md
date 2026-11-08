# AGENTS.md

实体归能（Entity Resolution）演示台：合成数据 → 双模型前后对比 → 单文件离线演示页。
背景与决策见 `docs/adr/`，完整用法见 `README.md`。

## 硬约束（违反即 bug）

1. **零第三方依赖**：运行时纯标准库（Python ≥3.11）。
2. **单文件、零外链**：`build/demo.html` 断网双击能开；唯一允许的 URL 字符串是
   SVG 命名空间常量（有测试锁定）。
3. **确定性**：一切随机走 seed（默认 42）；同 seed 两次生成逐字节一致（有测试）。
4. **失败不静默**：构建带 self-check 标记；数据撞车/引用悬空直接 raise。
5. **诚实标注**：页面 footer 必须声明 SYNTHETIC DATA；分数是代理评分（ADR 0002）。

## 常用命令

```bash
python3 scripts/make_mock_data.py            # 生成 data/ 合成轨（确定性）
python3 scripts/update_open_benchmark.py     # 生成 data/open_benchmark.json（开源基准轨，从已入库报告解析）
python3 scripts/build_open_cases.py --scores # 生成 data/open_cases.json（网络实测轨，从已入库分数重建）
python3 scripts/build_demo.py --open         # 构建 build/demo.html 并打开（三个数据文件缺一即 raise）
PYTHONPATH=src python3 -m unittest discover -s tests   # 测试（或装 pytest 后 pytest）
```

改了 `synth/simulate/layout` → 必须重跑生成+构建；改了 `page/theme` → 重跑构建；
重新评测了平台模型 → 重跑 `update_open_benchmark.py` + 构建；
重新取样了案例村向量 → 覆盖 `data_open/village_scores.json` 后重跑
`build_open_cases.py --scores` + 构建（village_scores 缺席时构建显式提示，
页面只出合成轨——优雅降级路径保留，平台再故障时用）。

## 代码地图

- `src/entity_arena/synth.py` 核心剧本人物（手工）+ 填充人物（seed 生成）、关系网
- `src/entity_arena/simulate.py` 字段相似度、双模型代理评分、检索评测、指标
- `src/entity_arena/layout.py` 网络两套坐标（FR + 分位归一 + 光环防撞）
- `src/entity_arena/page.py` 演示页模板 + 前端 JS（双语文案、四个视图、深链）
- `src/entity_arena/theme.py` 色板与 CSS
- `scripts/make_mock_data.py` 合成数据入口；`scripts/build_demo.py` 页面构建入口
- `scripts/make_open_dataset.py` 开源基准（Febrl）→ inf-retriever 训练平台上传包；
  源数据在 `data_open/`（随仓库提交）；训练/评测走 MCP `inf-retriever`（token 在
  `~/.claude.json`，不进仓库），流程见 skill `inf-retriever-finetune`
- `scripts/update_open_benchmark.py` 平台评测报告（`data_open/eval_reports/`，随仓库
  提交作溯源）→ `data/open_benchmark.json`；基线口径漂移会 raise。指标板第二板块
  （开源基准轨：真实模型实测）由它供数，双轨 footer 各自诚实标注
- `scripts/pick_open_village.py` 从 Febrl3 测试实体选"案例村"（60 条记录/影子对，
  **选村对模型分数盲**，规则写在 provenance 里，避免挑案例之嫌）→ `data_open/village.json`
- `scripts/fetch_village_embeddings.py` 调平台已部署微调服务的 `/v1/embeddings`
  取真实向量（`EMB_TOKEN` 环境变量传令牌；向量只落 `.tmp/`，不入库）
- `scripts/build_open_cases.py` 案例村 + 真实成对分数 → `data/open_cases.json`
  （θ 扫描 F1 最大化 → 断言链 → 组件纯度；溯源分数写 `data_open/village_scores.json`
  入库，`--scores` 模式离线复算）。网络视图第二轨道（Febrl3 实测）由它供数：
  before=影子关系纠缠簇，after=微调模型断言组件（红光环=混入异实体）
- `src/entity_arena/febrl.py` Febrl 数据共用工具（实体切分/序列化/索引）
- **部署要点（2026-11-08 实测）**：平台 serving 当日整体故障过一轮（新产物
  `train`/`pipeline`、旧产物一律 ~4min FAILED；`deploy_model` 起的服务全挂），
  后经**手动在控制台创建服务**（`embedding-febrl3-er-gemma`）恢复并完成取样。
  再遇部署故障：先查 Deployed Services 有没有现成可用服务，别急着重训。
  另两个坑：FAILED 部署留僵尸 serving 名（重试换新名）；平台网关掐 OpenSSL 3.x
  默认 ClientHello（后量子密钥共享），`fetch_village_embeddings.py` 已强制
  TLS1.2 + 重试，别改回去。

## 视觉语言（继承 retrieval-forge ADR 0005）

- 深空底 `#0c1117`，靠**明度**分层，重要的更亮；
- 全场只有两个角色色：**cyan = 微调模型/正确**，**violet = baseline**；
- 红 `#d97862` 是批注红，只给**错误断言**，不做警报色滥用；
- 任何新 UI 先截图四视图（`?view=` 深链直达）再合入。

## 数据契约（换真模型时）

`results.json`：`{models: {baseline: {qid: [{r, s}×5]}, tuned: …}}`；
`metrics.json`：overall + by_scenario。替换这两个文件，页面零改动。
开源基准轨：`open_benchmark.json`（由 `data_open/eval_reports/` 报告重建）——
重新评测后用新报告覆盖该目录再跑 `update_open_benchmark.py` 即可。
网络实测轨：`open_cases.json`（由 `data_open/village.json` + `village_scores.json`
重建；village_scores 缺席时优雅降级为仅合成轨）——重新取样向量后覆盖
`village_scores.json` 再跑 `build_open_cases.py --scores` 即可。
