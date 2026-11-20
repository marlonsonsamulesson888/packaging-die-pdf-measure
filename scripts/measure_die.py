#!/usr/bin/env python3
"""刀模 PDF 测量与盒型识别（只读，只给数据，不判定可生产）。

按描边颜色把线段分成切线/压痕/其他，按切线连通性拆出部件，
以部件中心为基准找底面压痕框，沿四个方向列出各面宽度链，
识别外墙、双压痕、内折墙，计算天地盖配合量与成盒外尺寸，
检查是否画了出血线，并与声称尺寸比对。

依赖：PyMuPDF（pip install pymupdf）。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

PT2MM = 25.4 / 72.0


def hex_color(rgb):
    if not rgb:
        return None
    return "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in rgb[:3])


def parse_hex(s):
    s = s.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"颜色要写成 #rrggbb：{s}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def color_dist(a, b):
    pa, pb = parse_hex(a), parse_hex(b)
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(pa, pb)))


def load_segments(pdf_path, page_no=0, scale=1.0):
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore
    doc = fitz.open(str(pdf_path))
    if page_no >= len(doc):
        raise ValueError(f"PDF 只有 {len(doc)} 页")
    page = doc[page_no]
    k = PT2MM * scale
    segs, curves, unstroked = [], 0, 0
    for d in page.get_drawings():
        col = hex_color(d.get("color"))
        if col is None:
            unstroked += 1
            continue
        for it in d["items"]:
            op = it[0]
            if op == "l":
                a, b = it[1], it[2]
                segs.append((col, a.x * k, a.y * k, b.x * k, b.y * k))
            elif op == "re":
                r = it[1]
                x0, y0, x1, y1 = r.x0 * k, r.y0 * k, r.x1 * k, r.y1 * k
                segs += [(col, x0, y0, x1, y0), (col, x1, y0, x1, y1),
                         (col, x1, y1, x0, y1), (col, x0, y1, x0, y0)]
            elif op == "c":
                curves += 1
                a, b = it[1], it[4]
                segs.append((col, a.x * k, a.y * k, b.x * k, b.y * k))
            elif op == "qu":
                q = it[1]
                pts = [q.ul, q.ur, q.lr, q.ll, q.ul]
                for p0, p1 in zip(pts, pts[1:]):
                    segs.append((col, p0.x * k, p0.y * k, p1.x * k, p1.y * k))
    boxes = {}
    for name in ("mediabox", "cropbox", "bleedbox", "trimbox", "artbox"):
        r = getattr(page, name, None)
        if r is not None:
            boxes[name] = [round(v * k, 2) for v in (r.x0, r.y0, r.x1, r.y1)]
    info = {
        "page_size_mm": [round(page.rect.width * k, 2), round(page.rect.height * k, 2)],
        "boxes_mm": boxes,
        "curve_items_approximated": curves,
        "fill_only_drawings_ignored": unstroked,
        "fonts_on_page": len(page.get_fonts()),
    }
    doc.close()
    return [s for s in segs if math.hypot(s[3] - s[1], s[4] - s[2]) > 1e-3], info


def classify_colors(segs, cut=None, crease=None):
    by = {}
    for s in segs:
        by.setdefault(s[0], []).append(s)
    colors = sorted(by)
    if not colors:
        raise ValueError("页面里没有带描边颜色的线段")
    confirmed = cut is not None and bool(crease)
    method = "参数指定（颜色角色仍以人工确认依据为准）" if confirmed else "颜色未完整指定，候选推断需人工确认"
    if cut is None:
        # 推断：整体外包框最大的颜色当切线；面积相近时取更红的颜色
        def area(c):
            xs = [v for s in by[c] for v in (s[1], s[3])]
            ys = [v for s in by[c] for v in (s[2], s[4])]
            return (max(xs) - min(xs)) * (max(ys) - min(ys))
        best = max(area(c) for c in colors)
        cands = [c for c in colors if area(c) >= best * 0.98]
        cut = min(cands, key=lambda c: color_dist(c, "#ff0000"))
        method = "推断（外包框最大/偏红），需人工确认"
    else:
        cut = "#" + cut.strip().lstrip("#").lower()
        if cut not in by:
            raise ValueError(f"指定切线颜色 {cut} 不存在；不自动替换为近似颜色")
    if crease is None:
        rest = [c for c in colors if c != cut]
        crease_set = set(rest)
    else:
        crease_set = set()
        for want in crease:
            want = "#" + want.strip().lstrip("#").lower()
            if want not in by or want == cut:
                raise ValueError(f"指定压痕颜色 {want} 不存在或与切线相同；请核实")
            crease_set.add(want)
        crease_set.discard(cut)
    other = [c for c in colors if c != cut and c not in crease_set]
    return {
        "cut": cut,
        "crease": sorted(crease_set),
        "other": other,
        "method": method,
        "roles_explicit": confirmed,
        "segment_counts": {c: len(by[c]) for c in colors},
    }


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def components(segs, tol=0.5):
    n = len(segs)
    dsu = DSU(n)
    grid = {}
    for i, s in enumerate(segs):
        for x, y in ((s[1], s[2]), (s[3], s[4])):
            key = (int(x // tol), int(y // tol))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j, (xx, yy) in grid.get((key[0] + dx, key[1] + dy), []):
                        if abs(xx - x) <= tol and abs(yy - y) <= tol:
                            dsu.union(i, j)
            grid.setdefault(key, []).append((i, (x, y)))
    groups = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(segs[i])
    out = []
    for g in groups.values():
        xs = [v for s in g for v in (s[1], s[3])]
        ys = [v for s in g for v in (s[2], s[4])]
        out.append({"segs": g, "bbox": [min(xs), min(ys), max(xs), max(ys)]})
    return out


def axis_lines(segs, axis_tol=0.05, merge_gap=0.6):
    """返回 (水平线 [(y, x0, x1)], 竖直线 [(x, y0, y1)])，合并共线且相接的线段。"""
    hs, vs = [], []
    for _, x0, y0, x1, y1 in segs:
        if abs(y1 - y0) <= axis_tol and abs(x1 - x0) > axis_tol:
            hs.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))
        elif abs(x1 - x0) <= axis_tol and abs(y1 - y0) > axis_tol:
            vs.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))

    def merge(lines):
        lines.sort()
        out = []
        for c, a, b in lines:
            for m in out:
                if abs(m[0] - c) <= 0.1 and a <= m[2] + merge_gap and b >= m[1] - merge_gap:
                    m[1], m[2] = min(m[1], a), max(m[2], b)
                    break
            else:
                out.append([c, a, b])
        return [tuple(m) for m in out]

    return merge(hs), merge(vs)


def crossings(lines, at, lo=None, hi=None):
    """直线族中跨过坐标 at 的线的位置（排序、去重 0.15mm）。"""
    pos = sorted(c for c, a, b in lines if a - 0.3 <= at <= b + 0.3
                 and (lo is None or c >= lo) and (hi is None or c <= hi))
    out = []
    for p in pos:
        if not out or p - out[-1] > 0.15:
            out.append(p)
    return out


def cut_hits(segs, axis, at):
    """切线与 x=at（axis='v'）或 y=at（axis='h'）的交点坐标。"""
    hits = []
    for _, x0, y0, x1, y1 in segs:
        if axis == "h":  # 水平扫描线 y=at，返回 x
            if (y0 - at) * (y1 - at) <= 0 and abs(y1 - y0) > 1e-6:
                t = (at - y0) / (y1 - y0)
                hits.append(x0 + t * (x1 - x0))
        else:
            if (x0 - at) * (x1 - at) <= 0 and abs(x1 - x0) > 1e-6:
                t = (at - x0) / (x1 - x0)
                hits.append(y0 + t * (y1 - y0))
    return sorted(hits)


def chain(start, creases, edge, direction):
    """从底面边 start 往外，经过各压痕到切线边 edge 的宽度链。"""
    stops = [start] + ([c for c in creases if c < start - 0.15][::-1] if direction < 0
                       else [c for c in creases if c > start + 0.15]) + [edge]
    return [round(abs(b - a), 2) for a, b in zip(stops, stops[1:])], stops


def interpret(widths, double_max):
    """把宽度链解释成 外墙 / 双压痕 / 内折墙 / 其他。"""
    r = {"outer_wall_mm": None, "double_crease_gap_mm": None, "inner_wall_mm": None,
         "extra_panels_mm": [], "raw_chain_mm": widths}
    if not widths:
        return r
    r["outer_wall_mm"] = widths[0]
    rest = widths[1:]
    if rest and rest[0] <= double_max and len(rest) >= 2:
        r["double_crease_gap_mm"] = rest[0]
        r["inner_wall_mm"] = rest[1]
        r["extra_panels_mm"] = rest[2:]
    else:
        r["extra_panels_mm"] = rest
    return r


def analyse_piece(idx, comp, inner_cuts, h_cr, v_cr, cut_segs, double_max):
    x0, y0, x1, y1 = comp["bbox"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    piece = {
        "id": idx,
        "bbox_mm": [round(v, 2) for v in comp["bbox"]],
        "flat_size_mm": [round(x1 - x0, 2), round(y1 - y0, 2)],
        "crease_lines": {"horizontal": len(h_cr), "vertical": len(v_cr)},
        "internal_cut_outlines": [],
    }
    for ic in inner_cuts:
        a, b, c, d = ic["bbox"]
        piece["internal_cut_outlines"].append({"bbox_mm": [round(v, 2) for v in ic["bbox"]],
                                               "size_mm": [round(c - a, 2), round(d - b, 2)]})
    vx = crossings(v_cr, cy, x0, x1)
    hy = crossings(h_cr, cx, y0, y1)
    left = [x for x in vx if x < cx]
    right = [x for x in vx if x > cx]
    top = [y for y in hy if y < cy]
    bot = [y for y in hy if y > cy]
    if not (left and right and top and bot):
        piece["structure"] = "未识别为四边起墙结构（中心四周不全是压痕），只给线条清单"
        return piece
    bx0, bx1, by0, by1 = max(left), min(right), max(top), min(bot)
    piece["bottom_panel_mm"] = {"x": [round(bx0, 2), round(bx1, 2)], "y": [round(by0, 2), round(by1, 2)],
                                "width": round(bx1 - bx0, 2), "length": round(by1 - by0, 2)}
    hx = cut_hits(cut_segs, "h", cy)
    hv = cut_hits(cut_segs, "v", cx)
    edges = {
        "left": min([h for h in hx if h < bx0] or [x0]),
        "right": max([h for h in hx if h > bx1] or [x1]),
        "top": min([h for h in hv if h < by0] or [y0]),
        "bottom": max([h for h in hv if h > by1] or [y1]),
    }
    sides = {}
    for name, start, cr, edge, dirn in (
        ("left", bx0, vx, edges["left"], -1), ("right", bx1, vx, edges["right"], 1),
        ("top", by0, hy, edges["top"], -1), ("bottom", by1, hy, edges["bottom"], 1)):
        widths, stops = chain(start, [c for c in cr if (c > edge if dirn < 0 else c < edge)], edge, dirn)
        sides[name] = interpret(widths, double_max)
        fam = v_cr if name in ("left", "right") else h_cr
        at = cy if name in ("left", "right") else cx
        lens = []
        for st in stops[:-1]:
            ln = [b - a for c, a, b in fam if abs(c - st) <= 0.2 and a - 0.3 <= at <= b + 0.3]
            lens.append(round(max(ln), 2) if ln else None)
        sides[name]["crease_lengths_mm"] = lens
    piece["sides"] = sides
    walls = [s["outer_wall_mm"] for s in sides.values() if s["outer_wall_mm"]]
    doubles = [s for s in sides.values() if s["double_crease_gap_mm"] is not None]
    piece["wall_height_mm"] = round(sum(walls) / len(walls), 2) if walls else None
    piece["wall_height_spread_mm"] = round(max(walls) - min(walls), 2) if walls else None
    piece["double_wall_sides"] = len(doubles)
    piece["structure"] = ("四边起墙，%d 边双层内折" % len(doubles)) if walls else "未识别"
    return piece


def parse_dims(s):
    parts = [float(p) for p in s.lower().replace("×", "x").replace("*", "x").split("x") if p.strip()]
    if len(parts) != 3:
        raise ValueError("尺寸写成 长x宽x高，如 200x47x235")
    return parts


def measure(pdf, page=0, cut=None, crease=None, scale=1.0, double_max=6.0,
            min_piece_mm=30.0, claimed=None, claim_tol=2.0, bleed=3.0):
    segs, info = load_segments(pdf, page, scale)
    cls = classify_colors(segs, cut, crease)
    cut_segs = [s for s in segs if s[0] == cls["cut"]]
    crease_segs = [s for s in segs if s[0] in cls["crease"]]
    other_segs = [s for s in segs if s[0] in cls["other"]]
    comps = components(cut_segs)
    big = [c for c in comps if (c["bbox"][2] - c["bbox"][0]) >= min_piece_mm
           and (c["bbox"][3] - c["bbox"][1]) >= min_piece_mm]
    # 被别的大部件包住的切线轮廓当作内部切口（开窗、扎带孔等）
    def inside(a, b):
        return a["bbox"][0] >= b["bbox"][0] - 0.1 and a["bbox"][1] >= b["bbox"][1] - 0.1 and \
            a["bbox"][2] <= b["bbox"][2] + 0.1 and a["bbox"][3] <= b["bbox"][3] + 0.1 and a is not b
    pieces_c = [c for c in big if not any(inside(c, o) for o in big)]
    pieces_c.sort(key=lambda c: (c["bbox"][0], c["bbox"][1]))
    hs, vs = axis_lines(crease_segs)
    pieces = []
    for i, pc in enumerate(pieces_c, 1):
        bx = pc["bbox"]
        inner = [c for c in comps if c is not pc and inside(c, pc)]
        h_in = [l for l in hs if bx[1] - 0.5 <= l[0] <= bx[3] + 0.5 and l[2] >= bx[0] - 0.5 and l[1] <= bx[2] + 0.5]
        v_in = [l for l in vs if bx[0] - 0.5 <= l[0] <= bx[2] + 0.5 and l[2] >= bx[1] - 0.5 and l[1] <= bx[3] + 0.5]
        pieces.append(analyse_piece(i, pc, inner, h_in, v_in, pc["segs"], double_max))

    result = {
        "file": Path(pdf).name,
        "units": "mm（PDF pt × 25.4/72 × scale）",
        "scale": scale,
        "pdf": info,
        "colors": cls,
        "piece_count": len(pieces),
        "pieces": pieces,
        "warnings": ([] if cls["roles_explicit"] else ["切线和压痕颜色未同时明确指定：部件和尺寸仅为候选，不用于成盒尺寸结论"]),
        "measurement_status": "roles-specified-review-required" if cls["roles_explicit"] else "provisional",
        "verdict": "仅数据，不判定可生产；尺寸以刀模厂/师傅确认版和白样为准",
    }
    if info["curve_items_approximated"]:
        result["warnings"].append(f"有 {info['curve_items_approximated']} 段曲线按弦长近似，圆角处尺寸有偏差")
    # 出血：其他颜色线条形成比部件外扩约 bleed 的框才算画了出血线
    bleed_found = []
    if cls["roles_explicit"] and other_segs:
        for oc in components(other_segs):
            ob = oc["bbox"]
            for p in pieces:
                b = p["bbox_mm"]
                off = [b[0] - ob[0], b[1] - ob[1], ob[2] - b[2], ob[3] - b[3]]
                if all(0.5 <= o <= bleed + 2.5 for o in off):
                    bleed_found.append({"piece": p["id"], "offset_mm": [round(o, 2) for o in off]})
    result["bleed_line"] = {
        "found": bool(bleed_found), "found_semantics": "兼容字段：仅表示候选外扩框检出，不证明出血存在或缺失", "status": "candidate" if bleed_found else "unknown",
        "matches": bleed_found,
        "note": ("检测到候选外扩线框，尚不能确认是出血线" if bleed_found else
                 "未识别到候选出血线；不能据此判定未画出血，须结合图层、标注及人工核查"),
    }
    result["warnings"].append(result["bleed_line"]["note"])

    trays = [p for p in pieces if p.get("bottom_panel_mm") and p.get("wall_height_mm")]
    assembly = None
    if len(trays) >= 2:
        trays_sorted = sorted(trays, key=lambda p: p["bottom_panel_mm"]["width"] * p["bottom_panel_mm"]["length"], reverse=True)
        lid, base = trays_sorted[0], trays_sorted[1]
        lb, bb = lid["bottom_panel_mm"], base["bottom_panel_mm"]
        lw, ll = sorted([lb["width"], lb["length"]])
        bw, bl = sorted([bb["width"], bb["length"]])
        full = lid["wall_height_mm"] >= base["wall_height_mm"] - 5
        height = max(lid["wall_height_mm"], base["wall_height_mm"]) if full else base["wall_height_mm"]
        assembly = {
            "type": "天地盖（两件，较大者为天盖）",
            "lid_piece": lid["id"], "base_piece": base["id"],
            "lid_bottom_mm": [lw, ll], "base_bottom_mm": [bw, bl],
            "clearance_mm": {"width": round(lw - bw, 2), "length": round(ll - bl, 2),
                             "height": round(lid["wall_height_mm"] - base["wall_height_mm"], 2)},
            "clearance_mm_semantics": "兼容字段：width/length 是面板压痕中心尺寸差，height 是墙高差；不是装配净间隙",
            "lid_depth": "全盖（天盖墙高≈地盖墙高）" if full else "浅盖/半盖",
            "closed_outer_mm_approx": [round(ll, 1), round(lw, 1), round(height, 1)],
            "note": "成盒外尺寸按天盖底面与较高墙高近似；内尺寸要减纸厚，纸厚未知时不写死",
        }
        if len(trays) > 2:
            result["warnings"].append(f"识别到 {len(trays)} 个起墙部件，只按最大两个配对，其余请人工归类")
    elif len(trays) == 1:
        t = trays[0]
        w, l = sorted([t["bottom_panel_mm"]["width"], t["bottom_panel_mm"]["length"]])
        assembly = {"type": "单件起墙盒/托", "closed_outer_mm_approx": [round(l, 1), round(w, 1), t["wall_height_mm"]]}
    result["assembly"] = assembly if cls["roles_explicit"] else None
    if not cls["roles_explicit"]:
        result["assembly_candidate"] = assembly
        assembly = None

    if claimed:
        want = parse_dims(claimed)
        cmp = {"claimed_mm": want}
        if assembly:
            got = assembly["closed_outer_mm_approx"]
            ws, gs = sorted(want), sorted(got)
            diffs = [round(g - w, 2) for g, w in zip(gs, ws)]
            shared = sorted({w for w in want for g in got if abs(w - g) <= claim_tol})
            cmp.update({"measured_mm": got, "sorted_diff_mm": diffs,
                        "match": all(abs(d) <= claim_tol for d in diffs),
                        "shared_values_mm": shared, "tolerance_mm": claim_tol})
            if not cmp["match"]:
                result["warnings"].append(f"刀模成盒约 {got} 与声称 {want} 不符，交老板/客户确认用哪个")
        else:
            cmp["match"] = None
        result["claimed_size_check"] = cmp
    return result


def to_markdown(r):
    L = [f"# 刀模测量记录：{r['file']}", "", f"> {r['verdict']}。AI/脚本数据，待人工确认。", ""]
    c = r["colors"]
    L += [f"- 版面：{r['pdf']['page_size_mm'][0]}×{r['pdf']['page_size_mm'][1]} mm；比例系数 {r['scale']}",
          f"- 切线颜色 {c['cut']}；压痕颜色 {', '.join(c['crease']) or '无'}；其他 {', '.join(c['other']) or '无'}（{c['method']}）",
          f"- 测量状态：{r['measurement_status']}；候选部件数：{r['piece_count']}",
          f"- 出血线：{r['bleed_line']['note']}", ""]
    for p in r["pieces"]:
        L.append(f"## 部件 {p['id']}：展开 {p['flat_size_mm'][0]}×{p['flat_size_mm'][1]} mm，{p['structure']}")
        if p.get("bottom_panel_mm"):
            b = p["bottom_panel_mm"]
            L.append(f"- 底面（压痕中心）{b['width']}×{b['length']} mm；平均墙高 {p['wall_height_mm']} mm（四边差 {p['wall_height_spread_mm']}）")
            for k, s in p["sides"].items():
                L.append(f"  - {k}: 外墙 {s['outer_wall_mm']}，双压痕间距 {s['double_crease_gap_mm']}，内折墙 {s['inner_wall_mm']}，其余 {s['extra_panels_mm']}；原始链 {s['raw_chain_mm']}；各压痕线长 {s.get('crease_lengths_mm')}")
        for ic in p["internal_cut_outlines"]:
            L.append(f"- 内部切口 {ic['size_mm'][0]}×{ic['size_mm'][1]} mm（开窗/孔，需人工确认用途）")
        L.append("")
    a = r.get("assembly")
    if a:
        L.append(f"## 成盒：{a['type']}")
        L.append(f"- 成盒外尺寸约 {a['closed_outer_mm_approx']} mm")
        if "clearance_mm" in a:
            cl = a["clearance_mm"]
            L.append(f"- 天盖底 {a['lid_bottom_mm']}、地盖底 {a['base_bottom_mm']}；面板压痕中心尺寸差（非装配净间隙）宽 {cl['width']} / 长 {cl['length']} / 墙高差 {cl['height']}；{a['lid_depth']}")
        L.append(f"- {a.get('note', '')}")
        L.append("")
    if "claimed_size_check" in r:
        k = r["claimed_size_check"]
        L.append(f"## 与声称尺寸比对：{k['claimed_mm']} → {'一致' if k.get('match') else '不一致' if k.get('match') is False else '无法比对'}")
        if k.get("measured_mm"):
            L.append(f"- 实测约 {k['measured_mm']}，排序后差值 {k['sorted_diff_mm']}，相同的数 {k['shared_values_mm']}")
        L.append("")
    if r["warnings"]:
        L.append("## 提示")
        L += [f"- {w}" for w in r["warnings"]]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="刀模 PDF 测量与盒型识别（只读）")
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=0, help="页码，从 0 开始")
    ap.add_argument("--cut-color", help="切线颜色 #rrggbb；不填则推断")
    ap.add_argument("--crease-color", action="append", help="压痕颜色 #rrggbb，可重复；不填则其余颜色仅作压痕候选，成盒结论暂缓")
    ap.add_argument("--scale", type=float, default=1.0, help="图纸不是 1:1 时的放大系数")
    ap.add_argument("--double-max", type=float, default=6.0, help="两条压痕间距不大于此值视为双压痕（mm）")
    ap.add_argument("--min-piece", type=float, default=30.0, help="部件最小边长（mm），更小的切线组视为碎线/孔")
    ap.add_argument("--claimed", help="声称成品尺寸，如 200x47x235")
    ap.add_argument("--claim-tol", type=float, default=2.0)
    ap.add_argument("--bleed", type=float, default=3.0, help="候选外扩框搜索参数（mm），不是工厂出血要求")
    ap.add_argument("--json", help="JSON 输出路径；不填打印到标准输出")
    ap.add_argument("--md", help="Markdown 报告输出路径")
    a = ap.parse_args(argv)
    try:
        r = measure(a.pdf, a.page, a.cut_color, a.crease_color, a.scale, a.double_max,
                    a.min_piece, a.claimed, a.claim_tol, a.bleed)
    except Exception as e:  # noqa: BLE001
        print(f"错误：{e}", file=sys.stderr)
        return 2
    text = json.dumps(r, ensure_ascii=False, indent=2)
    if a.json:
        Path(a.json).write_text(text, encoding="utf-8")
    else:
        print(text)
    if a.md:
        Path(a.md).write_text(to_markdown(r), encoding="utf-8")
    return 1 if r["warnings"] else 0


if __name__ == "__main__":
    sys.exit(main())
