#!/usr/bin/env python3
"""pdf_tables.py — Page-level table region detection and grid rebuild.

Reads the work directory produced by pdf_extract.py (_bbox.html,
blocks.jsonl, pages.json). Detection is page-level, not block-level:
rows are clustered from word y-centers across the whole page, and a
table region is a run of >=3 consecutive rows sharing consistent
internal whitespace gaps (column boundaries). This catches tables whose
cells pdftotext splits into many small blocks, including full-width
tables on two-column pages.

Output: <workdir>/tables.md and <workdir>/tables.json, each table with
page + bbox + optional caption, confidence="heuristic": merged cells
and multi-line headers can be misassigned, so every table MUST be
checked against a rendered page image before its values are quoted
(references/method.md section 4).

Usage:
  python pdf_tables.py [--workdir work]
"""
import argparse
import json
import os
import re
import xml.etree.ElementTree as ET

NS = "{http://www.w3.org/1999/xhtml}"
GAP_MIN = 14.0      # pt; region-detection gap threshold (> word spacing)
ROW_TOL = 4.0       # pt; y tolerance for word->row clustering
BOUND_TOL = 12.0    # pt; tolerance for gap-position consensus (right-aligned columns drift)
MIN_ROWS = 3        # rows required for a table region
MIN_GAPS = 2        # column boundaries required for a valid table
MIN_CONSENSUS = 0.6  # fraction of rows that must share a boundary
FINE_CONSENSUS = 0.4  # for fine (in-region) column bounds, dense numeric tables


def load_bbox_pages(html_path):
    with open(html_path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    raw = "".join(c for c in raw if c in "\t\n\r" or ord(c) >= 0x20)
    raw = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", raw)
    root = ET.fromstring(raw)
    pages = []
    for pel in root.iter(NS + "page"):
        words = []
        for wel in pel.iter(NS + "word"):
            words.append({
                "bbox": [float(wel.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")],
                "text": "".join(wel.itertext()),
            })
        pages.append({"width": float(pel.get("width")),
                      "height": float(pel.get("height")),
                      "words": words})
    return pages


def cluster_rows(words):
    rows = []
    for w in sorted(words, key=lambda w: ((w["bbox"][1] + w["bbox"][3]) / 2, w["bbox"][0])):
        cy = (w["bbox"][1] + w["bbox"][3]) / 2
        for row in rows:
            if abs(row["cy"] - cy) <= ROW_TOL:
                row["words"].append(w)
                row["cy"] = (row["cy"] * (len(row["words"]) - 1) + cy) / len(row["words"])
                break
        else:
            rows.append({"cy": cy, "words": [w]})
    rows.sort(key=lambda r: r["cy"])
    for r in rows:
        r["words"].sort(key=lambda w: w["bbox"][0])
    return rows


def row_gaps(row, gap_min=GAP_MIN):
    """Internal gap centers (pt) wider than gap_min."""
    gaps = []
    for a, b in zip(row["words"], row["words"][1:]):
        g = b["bbox"][0] - a["bbox"][2]
        if g > gap_min:
            gaps.append((a["bbox"][2] + b["bbox"][0]) / 2)
    return gaps


def cluster_values(values, tol=BOUND_TOL):
    values = sorted(values)
    groups = []
    for v in values:
        if groups and v - groups[-1][-1] <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def consensus_bounds(run, gap_min=GAP_MIN, frac=MIN_CONSENSUS):
    votes = []
    for r in run:
        votes.extend(row_gaps(r, gap_min))
    return [sum(g) / len(g) for g in cluster_values(votes)
            if len(g) >= frac * len(run)]


def matched(row, bounds, gap_min=GAP_MIN):
    gs = row_gaps(row, gap_min)
    return sum(1 for b in bounds if any(abs(g - b) <= 1.3 * BOUND_TOL for g in gs))


def find_regions(rows):
    """Maximal runs of rows with consistent internal gaps. Rows with a
    single gap (e.g. header rows over an empty corner cell) may join a
    run, but a valid region needs >=MIN_GAPS consensus bounds and at
    least two rows matching >=MIN_GAPS of them."""
    regions = []
    i = 0
    while i < len(rows):
        if not row_gaps(rows[i]):
            i += 1
            continue
        run = [rows[i]]
        j = i + 1
        while j < len(rows):
            cand = run + [rows[j]]
            # looser consensus while growing (frac 0.4), strict check at the end
            bounds = consensus_bounds(cand, frac=0.4)
            if (len(bounds) >= MIN_GAPS
                    and all(matched(r, bounds) >= 1 for r in cand)):
                run.append(rows[j])
                j += 1
            else:
                break
        if len(run) >= MIN_ROWS:
            bounds = consensus_bounds(run)
            strong = sum(1 for r in run if matched(r, bounds) >= MIN_GAPS)
            if len(bounds) >= MIN_GAPS and strong >= max(2, (len(run) + 1) // 2):
                regions.append((run, bounds))
        i = max(j, i + 1)
    return regions


def build_grid(run, bounds):
    def col_of(w):
        cx = (w["bbox"][0] + w["bbox"][2]) / 2
        c = 0
        for bd in bounds:
            if cx > bd:
                c += 1
        return c
    grid = []
    for r in run:
        cells = {}
        for w in r["words"]:
            cells.setdefault(col_of(w), []).append(w["text"])
        grid.append([" ".join(cells.get(i, [])) for i in range(len(bounds) + 1)])
    return grid


def to_markdown(g):
    if not g:
        return ""
    width = max(len(r) for r in g)
    rows = [r + [""] * (width - len(r)) for r in g]
    header, body = rows[0], rows[1:]
    md = ["| " + " | ".join(c.replace("|", "\\|") for c in header) + " |",
          "|" + "---|" * width]
    md += ["| " + " | ".join(c.replace("|", "\\|") for c in r) + " |" for r in body]
    return "\n".join(md)


def region_bbox(run):
    ws = [w for r in run for w in r["words"]]
    return [round(min(w["bbox"][0] for w in ws), 1),
            round(min(w["bbox"][1] for w in ws), 1),
            round(max(w["bbox"][2] for w in ws), 1),
            round(max(w["bbox"][3] for w in ws), 1)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default="work")
    args = ap.parse_args()

    bbox_path = os.path.join(args.workdir, "_bbox.html")
    blocks_path = os.path.join(args.workdir, "blocks.jsonl")
    if not (os.path.exists(bbox_path) and os.path.exists(blocks_path)):
        raise SystemExit("run pdf_extract.py first (needs _bbox.html and blocks.jsonl)")

    pages = load_bbox_pages(bbox_path)
    blocks = [json.loads(l) for l in open(blocks_path, encoding="utf-8")]
    gutters = {}
    pages_meta = os.path.join(args.workdir, "pages.json")
    if os.path.exists(pages_meta):
        meta = json.load(open(pages_meta, encoding="utf-8"))
        for p in meta["pages"]:
            gutters[p["page"]] = p.get("gutter")
    captions = [b for b in blocks if b["kind"] == "caption"
                and re.match(r"(?i)^table\s*\d+", b["text"])]

    def page_median_gap(words):
        gaps = []
        ws = sorted(words, key=lambda w: (w["bbox"][1], w["bbox"][0]))
        for r in cluster_rows(ws):
            gaps.extend(b["bbox"][0] - a["bbox"][2]
                        for a, b in zip(r["words"], r["words"][1:]))
        gaps = [g for g in gaps if g >= 0]
        if not gaps:
            return 3.0
        gaps.sort()
        return gaps[len(gaps) // 2]

    def detect(words):
        out = []
        if len(words) < 12:
            return out
        med = page_median_gap(words)
        rows = cluster_rows(words)
        for run, bounds in find_regions(rows):
            # fine column bounds: dense numeric tables separate columns
            # by gaps far below GAP_MIN
            fine = consensus_bounds(run, gap_min=max(4.0, 1.4 * med),
                                    frac=FINE_CONSENSUS)
            use = fine if len(fine) >= len(bounds) else bounds
            grid = build_grid(run, use)
            if len(grid) < MIN_ROWS or len(grid[0]) < MIN_GAPS + 1:
                continue
            out.append({"run": run, "bbox": region_bbox(run),
                        "grid": grid})
        return out

    def prose_fraction(reg):
        n = 0
        for row in reg["grid"]:
            if any(len(c.split()) >= 6 for c in row):
                n += 1
        return n / len(reg["grid"]) if reg["grid"] else 0.0

    tables = []
    for pi, page in enumerate(pages, 1):
        words = page["words"]
        gutter = gutters.get(pi)
        col_regions = []
        if gutter:
            g = gutter[0]
            for side in (lambda w: w["bbox"][0] + w["bbox"][2] < 2 * g,
                         lambda w: w["bbox"][0] + w["bbox"][2] >= 2 * g):
                col_regions.extend(detect([w for w in words if side(w)]))
        full_regions = detect(words)
        if gutter:
            # Drop full-width regions that mostly restate column regions
            # (body prose merged across the gutter, figure diagrams, ...).
            def overlap_words(fr):
                n, hit = 0, 0
                for r in fr["run"]:
                    for w in r["words"]:
                        n += 1
                        cx = (w["bbox"][0] + w["bbox"][2]) / 2
                        cy = (w["bbox"][1] + w["bbox"][3]) / 2
                        for cr in col_regions:
                            b = cr["bbox"]
                            if b[0] - 2 <= cx <= b[2] + 2 and b[1] - 2 <= cy <= b[3] + 2:
                                hit += 1
                                break
                return hit / n if n else 0.0
            regions = col_regions + [
                fr for fr in full_regions
                if overlap_words(fr) < 0.5
                and prose_fraction(fr) < 0.4
                and (fr["bbox"][2] - fr["bbox"][0]) > 0.55 * page["width"]]
        else:
            regions = full_regions

        for reg in regions:
            grid, bbox = reg["grid"], reg["bbox"]
            cap = None
            for c in captions:
                if c["page"] != pi:
                    continue
                cy0, cy1 = c["bbox"][1], c["bbox"][3]
                if 0 <= bbox[1] - cy1 <= 60 or 0 <= cy0 - bbox[3] <= 60:
                    cap = c
                    break
            # prose-heavy regions without a caption are almost always
            # body text misread as a grid; captioned ones are kept and
            # must be render-checked (confidence=heuristic)
            if cap is None and prose_fraction(reg) >= 0.4:
                continue
            tables.append({
                "page": pi, "bbox": bbox,
                "caption": cap["text"] if cap else None,
                "caption_block_id": cap["id"] if cap else None,
                "n_rows": len(grid), "n_cols": len(grid[0]),
                "confidence": "heuristic",
                "grid": grid, "markdown": to_markdown(grid),
            })

    with open(os.path.join(args.workdir, "tables.json"), "w", encoding="utf-8") as f:
        json.dump(tables, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.workdir, "tables.md"), "w", encoding="utf-8") as f:
        for t in tables:
            cap = f" caption={t['caption']!r}" if t["caption"] else ""
            f.write(f"<!-- table p{t['page']} bbox={t['bbox']} confidence=heuristic{cap} -->\n"
                    f"{t['markdown']}\n\n")
    print(json.dumps({"tables": len(tables),
                      "pages_with_tables": sorted({t["page"] for t in tables}),
                      "out": os.path.abspath(args.workdir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
