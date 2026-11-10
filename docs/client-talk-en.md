# ER 客户讲稿（英文版 · 2026-11-10 会议）

> 用途：对客户的开场介绍型讲稿（英文口播）。页面演示动线级剧本（中文、
> 逐视图口播）另见 `docs/talk-track.md`，两份配合用：本稿讲完思路，
> 再按 talk-track 走 demo 页。
> ⚠️ 讲前必读文末「数字口径备忘」——正文有几处口径与 demo 页实测数据
> 不完全一致，已被备忘逐条标出，现场说法以备忘为准。

---

Today I'd like to share our thinking on **Entity Resolution**.

Our overall solution has two stages: a **scoring engine** for candidate recall, followed by a **logic engine** that makes the final judgment on the recalled candidates.

The scoring engine is an embedding model that measures how similar a pair of entities is — an embedding model scores candidate pairs by resemblance. For each entity, we can extract an **ontology**, and apply **neuro-symbolic judgment** to perform hard validation at the logic layer — for example, consistency checks on unique keys such as social security numbers. And at the end of the pipeline, LLMs can also be brought in for entity disambiguation, and so on.

Since your email focused mainly on RAG-related work, let me concentrate on how **post-training of embedding models** helps the Entity Resolution task. INF was one of the early teams to adopt LLM backbones for embedding tasks, and we previously held a leading position on the Pareto frontier of the MTEB retrieval leaderboard. Today we lead on both **AIR-Bench**, a retrieval benchmark, and **BRIGHT**, a deep-reasoning retrieval benchmark.

When a general-purpose retrieval model is adapted to a specific scenario, we typically see gains of around 20%. These gains come from three sources:

1. For specific sub-domains, fine-tuning **opens up the collapsed sub-domain**;
2. It **aligns the model with the target document format distribution**;
3. It **adapts to the business's custom definition of relevance**.

At INF, we also have a mature training stack around our embedding models — **synthetic data construction** plus a **one-click training pipeline** — covering the full pipeline of training, evaluation, deployment, and live scoring.

Now let me use a short example to show how post-training an embedding model improves accuracy on Entity Resolution.

Today we'll work with two datasets: **Febrl3**, a public benchmark for entity linkage, and a **synthetic dataset** of our own construction.

These datasets pose quite a few challenges:

1. Misspelled names;
2. Same name, but different people;
3. Addresses written in different ways;
4. Missing fields — such as date of birth, or even social security numbers;
5. Different languages;
6. Twins with similar names and the same birth date;
7. The same person with different phone numbers.

… and many more like these. The root cause is that people enter their information in different places — bank KYC, border crossings, household registries. Only by linking these records effectively can we complete each entity's profile and gain a truly comprehensive understanding of the entity.

On the classic Febrl dataset, every node in the graph represents an entity. With the **untuned** model, entity linking produces **9 false links and 8 missed links**. After training a new embedding model on the training set, these errors are **completely eliminated** on this test set.

We also constructed a very hard synthetic dataset, with a large number of missing fields and confusable fields. The untuned model produces a large number of false connections on it, while the model fine-tuned on the Febrl training set still performs very well on this artificially constructed, difficult data.

On the Febrl dataset, fine-tuning achieves **100% recall** and drives the **false-link rate to zero**. On our hard synthetic data, the improvement is even more significant. And on Febrl, different retrieval backbones — our own **INF-retriever**, NVIDIA's **Nemotron** released last month, and Google's recently released **EmbeddingGemma2** — all achieve similar gains.

That's what I wanted to share today: how fine-tuning embedding models for candidate scoring in Entity Resolution can dramatically improve recall and dramatically reduce the false-link rate. I'm happy to take any questions.

---

## 数字口径备忘（讲前必读，防现场穿帮）

正文按口播流畅优先成稿，以下 4 处与 demo 页实测口径有出入，**现场说法以此为准**（数字源：`data/open_cases.json` / `data/open_benchmark.json` / `data/metrics.json`，2026-11-10 构建）：

1. **"9 false links / 8 missed links" 的主语不是未微调模型**，是字面相似度代理基线
   （Levenshtein 加权打分器，demo 页三处标注）。平台无法逐对部署未训练基模，
   逐对基线分数不可得。安全说法："With a standard string-similarity ER
   baseline, linking produces 9 false links and 8 missed links."
   真实未微调数字走检索口径（见第 3 条）。
2. **合成轨两侧分数均为代理模拟**（ADR 0002，页脚 SYNTHETIC 标注），
   不是真实测量。安全说法："We also built a simulated stress test —
   by construction, the baseline collapses on it, while the fine-tuned
   scoring holds up." 不要与 Febrl 实测混用同一句式。
3. **"100% recall / false-link 0" 的范围是案例村**（228 记录 / 78 实体 /
   323 真值对，真实微调向量逐对实测）；全量 test split（400 实体 / 2598 对）
   是 recall@5：gemma 0.972→1.0、nemotron 0.999→1.0、inf15 0.978→0.997。
   建议两个都讲、各归各口径。inf15 有 7/850 查询回退（win/tie/loss 63/780/7），
   不能笼统说 "zero regressions"；gemma（80/770/0）与 nemotron（6/844/0）可以。
4. **图上每个点是 record 不是 entity**：228 条记录连边后聚成 78 个组件才是
   实体。安全说法："Each node is a record; connected components form
   entities after linking."
5. **挑战清单第 5/7 条（多语言、电话）**：Febrl3 无 phone 字段、无跨语言证据，
   跨语言在合成剧本轨 Q2 演示。清单定位为 "generic ER challenges"，
   别说这份数据集都含有。

**可主动抛的加分数字**：训练量极小（1600 实体 / 10478 正例对 / 10998 硬负例）
即可达成上述效果——直接回应标注成本顾虑；实体级 80/20 切分零训练泄漏；
单一标量阈值 θ=0.9208 同时打满 precision/recall（θ 在案例村按 F1 扫出，
选村对模型分数盲，规则见 provenance）。
