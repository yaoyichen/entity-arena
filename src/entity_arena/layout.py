"""网络视图布局：before / after 两套坐标（合成剧本轨 + Febrl3 实测案例村轨）。

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
    span_x, span_y = (CANVAS_W - 2 * MARGIN) / (x1 - x0), (CANVAS_H - 2 * MARGIN) / (y1 - y0)
    scale = min(span_x, span_y)  # 保持纵横比
    off_x = MARGIN + ((CANVAS_W - 2 * MARGIN) - (x1 - x0) * scale) / 2
    off_y = MARGIN + ((CANVAS_H - 2 * MARGIN) - (y1 - y0) * scale) / 2
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
    """与前端 halo r = 24 + 7*(size+1) + 12 保持一致。"""
    return 43 + 7 * size


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
            pts[i][1] = min(max(pts[i][1], MARGIN), CANVAS_H - MARGIN)
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
        radius = 24 + 7 * len(mems_sorted)
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
        for p in label_pts:  # 软墙：标签留在画布内
            p[1] = min(max(p[1], 26.0), CANVAS_H - 26.0)
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


def build_network(world: World, seed: int = 42) -> dict:
    """产出 before/after 两套节点坐标 + 两套边。"""
    rng = random.Random(seed + 2)
    records = sorted(world.all_records(), key=lambda r: r.record_id)
    entities = sorted(world.entities, key=lambda e: e.entity_id)
    rid_to_idx = {r.record_id: i for i, r in enumerate(records)}
    eid_to_idx = {e.entity_id: i for i, e in enumerate(entities)}

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
    # 松弛：推开过近的点对
    for _ in range(60):
        for i in range(len(pts_before)):
            for j in range(i + 1, len(pts_before)):
                dx = pts_before[j][0] - pts_before[i][0]
                dy = pts_before[j][1] - pts_before[i][1]
                d = math.hypot(dx, dy)
                if d < 26:
                    push = (26 - d) / 2
                    ux, uy = (dx / (d + 1e-6)), (dy / (d + 1e-6))
                    pts_before[i][0] -= ux * push
                    pts_before[i][1] -= uy * push
                    pts_before[j][0] += ux * push
                    pts_before[j][1] += uy * push

    # ---- after：实体级 FR 布局，成员绕实体中心排环
    agg: dict[tuple[str, str], list[str]] = {}
    for rel in world.relations:
        ea = world.record_map()[rel.a].entity_id
        eb = world.record_map()[rel.b].entity_id
        if ea == eb:
            continue
        key = tuple(sorted((ea, eb)))
        agg.setdefault(key, []).append(rel.rel)
    ent_edges = [(eid_to_idx[a], eid_to_idx[b]) for a, b in agg]
    ent_pts = _quantile_norm(_fr_layout(len(entities), ent_edges, seed + 3))
    _separate_halos(ent_pts, [len(e.records) for e in entities])

    pts_after: list[list[float]] = []
    ent_members: dict[str, list[str]] = {e.entity_id: [] for e in entities}
    for r in records:
        ent_members[r.entity_id].append(r.record_id)
    member_ring: dict[str, list[str]] = {}
    for eid, mems in ent_members.items():
        member_ring[eid] = sorted(mems)
    used = {eid: 0 for eid in ent_members}
    for r in records:
        eid = r.entity_id
        mems = member_ring[eid]
        i = used[eid]
        used[eid] += 1
        cx, cy = ent_pts[eid_to_idx[eid]]
        radius = 24 + 7 * len(mems)
        ang = -math.pi / 2 + 2 * math.pi * i / max(len(mems), 1)
        pts_after.append([cx + radius * math.cos(ang), cy + radius * math.sin(ang)])

    nodes_before = [
        {"r": r.record_id, "e": r.entity_id, "x": round(x, 1), "y": round(y, 1)}
        for r, (x, y) in zip(records, pts_before)
    ]
    nodes_after = [
        {"r": r.record_id, "e": r.entity_id, "x": round(x, 1), "y": round(y, 1)}
        for r, (x, y) in zip(records, pts_after)
    ]
    ent_nodes = [
        {
            "e": e.entity_id,
            "x": round(ent_pts[eid_to_idx[e.entity_id]][0], 1),
            "y": round(ent_pts[eid_to_idx[e.entity_id]][1], 1),
            "size": len(e.records),
        }
        for e in entities
    ]
    edges_before = [
        {"a": rel.a, "b": rel.b, "rel": rel.rel, "note": rel.note}
        for rel in world.relations
    ]
    edges_after = [
        {"a": a, "b": b, "rels": sorted(set(v)), "evidence": len(v)}
        for (a, b), v in sorted(agg.items())
    ]
    return {
        "canvas": {"w": CANVAS_W, "h": CANVAS_H},
        "nodes_before": nodes_before,
        "nodes_after": nodes_after,
        "entities": ent_nodes,
        "edges_before": edges_before,
        "edges_after": edges_after,
    }
