# 现场演示剧本（客户会 · 约 20 分钟动线）

> 配套：`docs/talk-track.md`（逐句口播）、`build/demo.html`（断网可开的单文件演示页）、
> `scripts/live_pair_score.py`（调部署服务的现场打分器）。
> 全程口径：合成轨分数=代理评分（页面已标注）；实测轨与 live 分数=真实模型输出。

## 会前 30 分钟清单

1. **服务在线**：控制台看状态 ONLINE（`https://agent-inf-retriever-train-service.openapi-inspire.inf.tech/#/services/sv-15c7bc49-f87f-4d37-bab4-6847fead45eb`）；
2. **live 命令跑通一遍**（令牌在 `.tmp/emb_token.txt`，不进幻灯/屏幕共享）：
   ```bash
   export EMB_TOKEN=$(cat .tmp/emb_token.txt)
   python3 scripts/live_pair_score.py --pair 476        # 期望 ~0.945 LINKED
   ```
3. **演示页**：`python3 scripts/build_demo.py --open`（或直接双击 `build/demo.html`）；
4. **兜底预演**：拔网线页面照样能讲（单文件零外链）；服务挂了 → 场景 5 的入库版照讲（见该场景"降级"行）。

## 场景动线

### 场景 1 · 开场 60 秒——先建立信任
`demo.html?view=arena&lang=zh`
断网双击可开、单文件无外链；今天看两层数据：合成剧本（可控叙事）+ Febrl3 开源基准
（真实模型实测）。诚实声明在页脚：SYNTHETIC DATA、代理评分口径。

### 场景 2 · Arena 三连击（三个最痛的用例）
| 深链 | 讲什么 | 数字 |
|---|---|---|
| `?view=arena&q=Q2` | 跨文字音译：Wong Sau Ying ↔ 王秀英 | 0.34 → 0.88 |
| `?view=arena&q=Q4` | 影子人：两个张伟，错连会捏造不存在的人 | baseline 误连率 0.60 |
| `?view=arena&q=Q1` | 信息缺失：光秃入住单也能锁定 | — |

讲点：θ=0.55 判定线；红=误连，青=真身，虚线=候选不敢断言。

### 场景 3 · Network 合成轨——孤岛变光环
`?view=network&resolve=1`
五数据源孤岛 → 点「执行归并」看动画 → 47 条记录收进 19 个实体光环；
双胞胎/影子人肉眼可分（两个光环绝不粘连）。

### 场景 4 · Network Febrl3 实测轨——真模型拆毛线球
| 深链 | 看什么 |
|---|---|
| `?view=network&track=open` | before：102 条记录被同姓/同址/同生日的**影子关系**缠成毛线球 |
| `?view=network&track=open&resolve=1` | after：微调模型断言出 33 个组件光环，147/147 真值对、零误连零漏连 |
| `…&focus=rec-476-dup-0` | 点任意节点看档案卡：字段、组件纯度、**真实分数 top-3 + θ 判定** |

讲点：before 是"这些人看起来像一伙的"诱惑态——传统规则打分在这里必栽；
微调模型把 660 条影子对全部拒掉、147 条真对全部抓住。

### 场景 5 · LIVE 现场断言（高潮，2 分钟）
```bash
export EMB_TOKEN=$(cat .tmp/emb_token.txt)
# ① 连对：同一人，姓名错字风暴 + 生日缺失 + 地址全是 typo
python3 scripts/live_pair_score.py --pair 476
#   ⇒ ~0.945 LINKED（超 θ +0.012）

# ② 拒连：同名（green）、同州、长得像——但是两个人
python3 scripts/live_pair_score.py --rids rec-1191-dup-0,rec-85-dup-3
#   ⇒ ~0.910 REFUSED（距 θ -0.023）
```

**核心台词**：拒连的相似对（0.91）排在连对的乱码对（0.95）之下——模型不是在比字面，
是在嵌入空间里理解"这是同一个人的两种潦草写法 / 这是两个都叫 green 的陌生人"。

*降级*：服务掉线 → 同一组数字已锁档在实测轨页面里，
开 `?view=network&track=open&resolve=1&focus=rec-476-dup-0` 照讲不误。

### 场景 6 · LIVE 客户出题（互动，最能记住的两分钟）
请客户口述"两条记录该怎么判"，现场拼成行式文本：
```bash
python3 scripts/live_pair_score.py \
  --text-a "name: spencer green  dob: 1942-03-13  address: 24 yarra street" \
  --text-b "name: spencxr royans  address: 24 yarra srteet"
```
预设桥段：把 B 的地址改成完全不同的地址 → 分数当场掉下 θ →
"证据被拿走，模型立刻不敢断言——它没有背答案"。

### 场景 7 · Scoreboard——数字收口 + 诚实边界
`?view=scoreboard`
三个真实基模（inf-1.5 / nvidia / gemma）微调前后 × Febrl3 实测指标；
合成轨指标锁定 seed=42。讲清三个口径：代理评分、检索评测、θ 判定线（案例村 F1 最优、选村对分数盲）。

### 场景 8 · 平台背书（可选，技术听众）
打开 Deployed Services 控制台页（链接见会前清单 1）：服务在线、请求量/用量实时可见——
"刚才两条 live 命令就是打在这个服务上的"。

## Q&A 弹药

| 问题 | 回答要点 |
|---|---|
| 分数能信吗？ | live 分数=部署服务真实输出；全测试集 NDCG@10=1.000（评测报告入库可查）；θ 由 F1 扫描选定、选村规则写死在 provenance |
| 为什么 baseline 不 live？ | 平台 `deploy_model` 仅支持已训练模型，基模无法部署；baseline 的真实评测数字在记分牌（同集离线评测，公平） |
| 换成我们的数据？ | 数据契约：`results.json`/`metrics.json` 同构替换，页面零改动；开源轨三条命令重跑全链路 |
| 误连代价高怎么办？ | θ 可调：调高换精度（演示页口径 θ 的选择过程全公开）；记分牌有 false_link_rate 分场景指标 |
| 现场 FP 抖动？ | GPU 推理逐批有 ~0.005 内的浮点抖动（0.9457 入库 vs 0.9451 现场），判定不变——正好证明是真实服务不是录像 |

## 风险预案

- **会场断网**：页面零外链照常演示；live 场景改讲入库版（场景 5 降级行）；
- **服务掉线**：同上；会前清单已含在线检查；
- **分数抖动跨界**？：476（+0.012）与拒连对（-0.023）的边界余量都选过，抖动 <0.005 不会翻判定；
- **时间不够**：砍场景 3（合成轨网络）与场景 8，保留 1/2/4/5/7。
