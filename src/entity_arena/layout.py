"""网络视图布局（合成剧本轨 + Febrl3 实测案例村轨）。

合成轨三态：原始（来源孤岛）→ 基线组件 → 微调组件，三套坐标两套边
（微调态组件由断言链并查集而来——画的是模型所为，不是剧本真值）。

坑的清单（继承 retrieval-forge ADR 0005 的教训）：
1. 力导向**不做硬 clamp**（clamp 会把节点钉在边界形成点带）；
2. 收敛后做**分位数归一化（2%–98%）**再映射画布；
3. 前端只做位置插值（morph），不重算布局 —— 确定性、零布局抖动。
"""

from __future__ import annotations

import math
import random

from .synth import World

CANVAS_W, CANVAS_H = 1200, 780
MARGIN = 90
TOP = 120  # 顶部预留：前端 .netbar（按钮+状态条）是画布顶部浮层，光环/标签不得进入


def _quantile_norm(points: list[list[float]], lo=0.02, hi=0.98) -> list[list[float]]:
    """分位数归一化到 [MARGIN, W-MARGIN]×[MARGIN, H-MARGIN]，保持纵横比。"""
    if not points:
        return points
    xs = sorted(p[0] for p in points)
    ys = sorted(p[1] for p in points)

    def q(arr: list[float], t: float) -> float:
        i = t * (len(arr) - 1)
        i0 = int(i)
        return arr[i0] + (arr[min(i0 + 1, len(arr) - 1)] - arr[i0]) * (i - i0)

    x0, x1 = q(xs, lo), q(xs, hi)
    y0, y1 = q(ys, lo), q(ys, hi)
    if x1 - x0 < 1e-6:
        x1 = x0 + 1.0
    if y1 - y0 < 1e-6:
        y1 = y0 + 1.0
    span_x, span_y = (CANVAS_W - 2 * MARGIN) / (x1 - x0), (CANVAS_H - MARGIN - TOP) / (y1 - y0)
    scale = min(span_x, span_y)  # 保持纵横比
    off_x = MARGIN + ((CANVAS_W - 2 * MARGIN) - (x1 - x0) * scale) / 2
    off_y = TOP + ((CANVAS_H - MARGIN - TOP) - (y1 - y0) * scale) / 2
    return [[off_x + (p[0] - x0) * scale, off_y + (p[1] - y0) * scale] for p in points]


def _fr_layout(n: int, edges: list[tuple[int, int]], seed: int, iters: int = 340) -> list[list[float]]:
    """Fruchterman-Reingold（纯标准库），无 clamp。"""
    rng = random.Random(seed)
    pts = [[rng.uniform(-1, 1), rng.uniform(-1, 1)] for _ in range(n)]
    if n == 1:
        return [[0.0, 0.0]]
    k = 1.0 / math.sqrt(n)
    t0 = 0.1
    adj: list[list[int]] = [[] for _ in range(n)]
    for a, b in edges:
        adj[a].append(b)
        adj[b].append(a)
    for it in range(iters):
        temp = t0 * (1 - it / iters) ** 1.3 + 1e-4
        disp = [[0.0, 0.0] for _ in range(n)]
        for i in range(n):
            xi, yi = pts[i]
            for j in range(i + 1, n):
                dx, dy = xi - pts[j][0], yi - pts[j][1]
                d2 = dx * dx + dy * dy + 1e-6
                f = k * k / d2
                d = math.sqrt(d2)
                ux, uy = dx / d, dy / d
                disp[i][0] += ux * f
                disp[i][1] += uy * f
                disp[j][0] -= ux * f
                disp[j][1] -= uy * f
            for j in adj[i]:
                dx, dy = pts[j][0] - xi, pts[j][1] - yi
                d = math.sqrt(dx * dx + dy * dy) + 1e-6
                f = d * d / k
                disp[i][0] += dx / d * f
                disp[i][1] += dy / d * f
        for i in range(n):
            dx, dy = disp[i]
            d = math.sqrt(dx * dx + dy * dy) + 1e-9
            step = min(d, temp)
            pts[i][0] += dx / d * step
            pts[i][1] += dy / d * step
    return pts


def _halo_radius(size: int) -> float:
    """与前端 halo R = 30 + 4*size 保持一致（扩村到 ~78 组件后缩小半径，
    否则光环总面积超过可布面积的 8 成，物理上无法互不压叠）。"""
    return 30 + 4 * size


def _separate_halos(pts: list[list[float]], sizes: list[int], iters: int = 240) -> None:
    """光环防撞：实体中心两两推开（半径和 + 间隙），并用软墙留在画布内。

    在 FR 收敛之后运行 —— FR 负责"关系近的靠在一起"，这一步只保证光环不互相压叠
    （影子人/双胞胎必须一眼能看出是分开的两个人）。
    """
    n = len(pts)
    radii = [_halo_radius(s) for s in sizes]
    for _ in range(iters):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                dx = pts[j][0] - pts[i][0]
                dy = pts[j][1] - pts[i][1]
                d = math.hypot(dx, dy)
                need = radii[i] + radii[j] + 14
                if d < need:
                    push = (need - d) / 2
                    ux, uy = dx / (d + 1e-6), dy / (d + 1e-6)
                    pts[i][0] -= ux * push
                    pts[i][1] -= uy * push
                    pts[j][0] += ux * push
                    pts[j][1] += uy * push
                    moved = True
        for i in range(n):
            pts[i][0] = min(max(pts[i][0], MARGIN), CANVAS_W - MARGIN)
            pts[i][1] = min(max(pts[i][1], TOP), CANVAS_H - MARGIN)  # 顶部给 .netbar 浮层让位
        if not moved:
            break


def build_open_network(records: list[dict], links: list[tuple[str, str]],
                       shadow_pairs: list[tuple[str, str]], seed: int = 42) -> dict:
    """Febrl3 案例村两套坐标（shape 与 build_network 一致，前端直接复用）。

    before：影子关系（同姓/同址/同生日的异实体纠缠 + 同实体的天然重复）作边跑 FR
            ——呈现"这些人看起来像一伙的"诱惑态；
    after：断言链（模型分数 ≥ θ）的连通分量为"模型声称的实体"，组件级 FR + 光环
            防撞，成员绕组件中心排环。组件纯度由调用方标色（纯=青 / 混=红）。
    """
    rids = [r["rid"] for r in records]
    idx_of = {rid: i for i, rid in enumerate(rids)}

    # ---- before：影子关系聚簇
    shadow_edges = [(idx_of[a], idx_of[b]) for a, b in shadow_pairs
                    if a in idx_of and b in idx_of]
    pts_before = _quantile_norm(_fr_layout(len(rids), shadow_edges, seed + 11))
    # 松弛防重叠（同 build_network）
    for _ in range(60):
        for i in range(len(pts_before)):
            for j in range(i + 1, len(pts_before)):
                dx, dy = pts_before[j][0] - pts_before[i][0], pts_before[j][1] - pts_before[i][1]
                d = math.hypot(dx, dy)
                if d < 26:
                    push = (26 - d) / 2
                    ux, uy = (dx / (d + 1e-6)), (dy / (d + 1e-6))
                    pts_before[i][0] -= ux * push
                    pts_before[i][1] -= uy * push
                    pts_before[j][0] += ux * push
                    pts_before[j][1] += uy * push

    # ---- after：断言组件光环
    parent = list(range(len(rids)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in links:
        ra, rb = find(idx_of[a]), find(idx_of[b])
        if ra != rb:
            parent[ra] = rb
    comp_ids = sorted({find(i) for i in range(len(rids))})
    comp_idx = {c: i for i, c in enumerate(comp_ids)}
    members: list[list[str]] = [[] for _ in comp_ids]
    for i, rid in enumerate(rids):
        members[comp_idx[find(i)]].append(rid)

    # 组件间无边：纯斥力散开 + 光环防撞
    comp_pts = _quantile_norm(_fr_layout(len(comp_ids), [], seed + 13))
    _separate_halos(comp_pts, [len(m) for m in members])

    pts_after: list[list[float]] = [[0.0, 0.0] for _ in rids]
    for ci, mems in enumerate(members):
        cx, cy = comp_pts[ci]
        mems_sorted = sorted(mems)
        radius = 12 + 4 * len(mems_sorted)   # 成员环：节点外缘 19+4n，稳落光环 30+4n 内
        for k, rid in enumerate(mems_sorted):
            ang = -math.pi / 2 + 2 * math.pi * k / len(mems_sorted)
            pts_after[idx_of[rid]] = [cx + radius * math.cos(ang), cy + radius * math.sin(ang)]

    # 组件标签锚点：单记录组件放光环内（不与邻标碰撞），多记录放光环上方；
    # 上方标签横向投影重叠时纵向推开（与 _separate_halos 同款松弛）。
    label_pts: list[list[float]] = []
    for ci, (cx, cy) in enumerate(comp_pts):
        r = _halo_radius(len(members[ci]))
        label_pts.append([cx, cy + 4.5] if len(members[ci]) == 1 else [cx, cy - r - 8])
    _LW = 64  # 标签宽度估计（"E1191 ×5" 级别，12.5px 字号）
    for _ in range(80):
        moved = False
        for i in range(len(label_pts)):
            for j in range(i + 1, len(label_pts)):
                xi, yi = label_pts[i]
                xj, yj = label_pts[j]
                if abs(xi - xj) < _LW and abs(yi - yj) < 15:
                    push = (15 - abs(yi - yj)) / 2
                    uy = -push if yi <= yj else push
                    label_pts[i][1] = yi + uy
                    label_pts[j][1] = yj - uy
                    moved = True
        for p in label_pts:  # 软墙：标签留在画布内、避开顶部 .netbar 浮层
            p[1] = min(max(p[1], 54.0), CANVAS_H - 26.0)
        if not moved:
            break

    return {
        "canvas": {"w": CANVAS_W, "h": CANVAS_H},
        "nodes_before": [
            {"r": rid, "x": round(x, 1), "y": round(y, 1)}
            for rid, (x, y) in zip(rids, pts_before)
        ],
        "nodes_after": [
            {"r": rid, "x": round(x, 1), "y": round(y, 1)}
            for rid, (x, y) in zip(rids, pts_after)
        ],
        "entities": [
            {"e": f"comp-{ci}", "x": round(c[0], 1), "y": round(c[1], 1),
             "lx": round(label_pts[ci][0], 1), "ly": round(label_pts[ci][1], 1),
             "size": len(members[ci])}
            for ci, c in enumerate(comp_pts)
        ],
        "edges_before": [
            {"a": rids[a], "b": rids[b], "rel": "lookalike"} for a, b in shadow_edges
        ],
        "edges_after": [],
        "comp_members": {f"comp-{ci}": members[ci] for ci in range(len(members))},
    }


def _relax_points(pts: list[list[float]], min_d: float = 26, iters: int = 60) -> None:
    """松弛：推开过近的点对（原地修改）。"""
    for _ in range(iters):
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                dx = pts[j][0] - pts[i][0]
                dy = pts[j][1] - pts[i][1]
                d = math.hypot(dx, dy)
                if d < min_d:
                    push = (min_d - d) / 2
                    ux, uy = (dx / (d + 1e-6)), (dy / (d + 1e-6))
                    pts[i][0] -= ux * push
                    pts[i][1] -= uy * push
                    pts[j][0] += ux * push
                    pts[j][1] += uy * push


def _comp_layout(comp_meta: list[dict], seed: int,
                 edges: list[tuple[str, str]] | None = None
                 ) -> tuple[list[dict], dict[str, list[list[float]]]]:
    """组件级布局：FR（可带组件间关系边）→ 分位归一 → 光环防撞 → 标签防撞。

    返回 (entities, member_pos)：entities 与 build_open_network 同 shape
    （e/x/y/lx/ly/size）；member_pos = comp_id → [[x, y], ...]（成员环，rid 排序序）。"""
    ids = [c["id"] for c in comp_meta]
    idx_of = {cid: i for i, cid in enumerate(ids)}
    ent_edges = [(idx_of[a], idx_of[b]) for a, b in (edges or []) if a in idx_of and b in idx_of]
    pts = _quantile_norm(_fr_layout(len(ids), ent_edges, seed))
    _separate_halos(pts, [c["size"] for c in comp_meta])

    label_pts: list[list[float]] = []
    for ci, (cx, cy) in enumerate(pts):
        r = _halo_radius(comp_meta[ci]["size"])
        label_pts.append([cx, cy + 4.5] if comp_meta[ci]["size"] == 1 else [cx, cy - r - 8])
    _LW = 72  # 标签宽度估计（"MIXED ×5" / 姓名 ×n 级别，12.5px 字号）
    for _ in range(80):
        moved = False
        for i in range(len(label_pts)):
            for j in range(i + 1, len(label_pts)):
                xi, yi = label_pts[i]
                xj, yj = label_pts[j]
                if abs(xi - xj) < _LW and abs(yi - yj) < 15:
                    push = (15 - abs(yi - yj)) / 2
                    uy = -push if yi <= yj else push
                    label_pts[i][1] = yi + uy
                    label_pts[j][1] = yj - uy
                    moved = True
        for p in label_pts:  # 软墙：标签留在画布内、避开顶部 .netbar 浮层
            p[1] = min(max(p[1], 54.0), CANVAS_H - 26.0)
        if not moved:
            break

    member_pos: dict[str, list[list[float]]] = {}
    for ci, c in enumerate(comp_meta):
        cx, cy = pts[ci]
        n = max(c["size"], 1)
        radius = 12 + 4 * n   # 成员环：节点外缘 19+4n，稳落光环 30+4n 内
        member_pos[c["id"]] = [
            [cx + radius * math.cos(-math.pi / 2 + 2 * math.pi * k / n),
             cy + radius * math.sin(-math.pi / 2 + 2 * math.pi * k / n)]
            for k in range(n)
        ]
    entities = [
        {"e": c["id"], "x": round(pts[ci][0], 1), "y": round(pts[ci][1], 1),
         "lx": round(label_pts[ci][0], 1), "ly": round(label_pts[ci][1], 1),
         "size": c["size"]}
        for ci, c in enumerate(comp_meta)
    ]
    return entities, member_pos


def build_network(world: World, seed: int = 42) -> dict:
    """合成剧本轨网络：原始（来源孤岛）→ 基线打分 → 微调打分，三套坐标两套边。

    after 态不再是"剧本真值"而是**微调模型断言出的组件**（诚实口径：图上画的是
    模型所为，不是作者所知——微调漏连的真对以红虚线 ⊘ 亮出）；基线态 = 字面
    相似度代理的断言组件（误连红线 / 漏连红虚线 / 混合组件红光环，与实测轨同语言）。
    """
    from . import simulate  # 局部导入：simulate 只依赖 synth，无循环依赖

    rng = random.Random(seed + 2)
    records = sorted(world.all_records(), key=lambda r: r.record_id)

    # ---- before：按来源聚簇（每个系统各自一份"孤岛"记录）
    n_src = 5
    anchors = [
        (CANVAS_W / 2 + 3.05 * 130 * math.cos(a), CANVAS_H / 2 + 1.55 * 130 * math.sin(a))
        for a in [i * 2 * math.pi / n_src - math.pi / 2 for i in range(n_src)]
    ]
    src_idx = {s: i for i, s in enumerate(
        ["household_registry", "border_crossing", "bank_kyc", "telecom", "hotel_checkin"])}
    pts_before: list[list[float]] = []
    jitter_by_src: dict[int, list[list[float]]] = {}
    for r in records:
        si = src_idx[r.source]
        jitter_by_src.setdefault(si, []).append(
            [rng.gauss(0, 52), rng.gauss(0, 40)])
    counters = {i: 0 for i in range(n_src)}
    for r in records:
        si = src_idx[r.source]
        j = jitter_by_src[si][counters[si]]
        counters[si] += 1
        pts_before.append([anchors[si][0] + j[0], anchors[si][1] + j[1]])
    _relax_points(pts_before)

    # ---- 双模型断言组件（分数与 results.json 同源：pair_matrix 共用同 rng 流）
    theta = simulate.THRESHOLD
    und = simulate.undirected_scores(world, seed)
    blocks: dict[str, dict] = {}
    for model in ("baseline", "tuned"):
        links, comp_of, comp_meta = simulate.link_components(world, und[model], theta)
        stats, false_links, missed = simulate.link_stats(world, links, comp_of, comp_meta)
        blocks[model] = {"links": links, "comp_of": comp_of, "comp_meta": comp_meta,
                         "stats": stats, "false_links": false_links, "missed_pairs": missed}

    # 关系证据聚合到微调组件对（微调态组件间连线；基线态不画关系线，只看断言/光环）
    rel_agg: dict[tuple[str, str], list[str]] = {}
    for rel in world.relations:
        ca = blocks["tuned"]["comp_of"][rel.a]
        cb = blocks["tuned"]["comp_of"][rel.b]
        if ca == cb:
            continue
        key = tuple(sorted((ca, cb)))
        rel_agg.setdefault(key, []).append(rel.rel)

    # ---- 布局：微调组件带关系边（家族相邻），基线组件纯斥力（与实测轨同式样）
    tu_ents, tu_pos = _comp_layout(blocks["tuned"]["comp_meta"], seed + 3,
                                   edges=list(rel_agg))
    bl_ents, bl_pos = _comp_layout(blocks["baseline"]["comp_meta"], seed + 17)

    def node_rows(model: str, pos: dict[str, list[list[float]]]) -> list[dict]:
        b = blocks[model]
        rows = []
        for c in b["comp_meta"]:
            for k, rid in enumerate(sorted(c["members"])):
                x, y = pos[c["id"]][k]
                rows.append({"r": rid, "x": round(x, 1), "y": round(y, 1)})
        return rows

    tu = blocks["tuned"]
    bl = blocks["baseline"]
    return {
        "canvas": {"w": CANVAS_W, "h": CANVAS_H},
        "theta": theta,
        "nodes_before": [
            {"r": r.record_id, "e": r.entity_id, "x": round(x, 1), "y": round(y, 1)}
            for r, (x, y) in zip(records, pts_before)
        ],
        # after（微调态）坐标 = 微调组件成员环
        "nodes_after": node_rows("tuned", tu_pos),
        "entities": tu_ents,
        "comp_members": {c["id"]: c["members"] for c in tu["comp_meta"]},
        "components": tu["comp_meta"],
        "comp_of": tu["comp_of"],
        "stats": tu["stats"],
        "links": tu["links"],
        "false_links": tu["false_links"],
        "missed_pairs": tu["missed_pairs"],
        "edges_before": [
            {"a": rel.a, "b": rel.b, "rel": rel.rel, "note": rel.note}
            for rel in world.relations
        ],
        "edges_after": [
            {"a": a, "b": b, "rels": sorted(set(v)), "evidence": len(v)}
            for (a, b), v in sorted(rel_agg.items())
        ],
        # 基线态（"微调前"）：shape 与 open_cases.baseline 逐字段一致，前端共用一套渲染
        "baseline": {
            "kind": "proxy",
            "theta": theta,
            "stats": bl["stats"],
            "links": bl["links"],
            "false_links": bl["false_links"],
            "missed_pairs": bl["missed_pairs"],
            "components": bl["comp_meta"],
            "comp_of": bl["comp_of"],
            "network": {
                "nodes_after": node_rows("baseline", bl_pos),
                "entities": bl_ents,
                "comp_members": {c["id"]: c["members"] for c in bl["comp_meta"]},
            },
        },
    }
