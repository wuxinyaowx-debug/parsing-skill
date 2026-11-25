#!/usr/bin/env python3
"""pdf_extract.py — Geometry-aware extraction of scientific PDFs.

Reads the word/line/block bounding boxes produced by `pdftotext -bbox`,
rebuilds the reading order of single- and multi-column pages, strips
running headers/footers, joins lines into paragraphs (with
dehyphenation), and labels blocks by structural kind.

Outputs (into --outdir, default ./work):
  blocks.jsonl   one JSON object per block:
                 {id, page, bbox:[x0,y0,x1,y1], kind, text}
  full.md        the whole document in reading order, Markdown-ish,
                 every block preceded by a provenance comment
                 <!-- p3 b12 paragraph -->
  pages.json     per-page metadata (size, gutter, dropped header/footer)

Dependencies: poppler-utils (pdftotext). Python stdlib only.

Usage:
  python pdf_extract.py <input.pdf> [--outdir DIR] [--lang en|zh]
"""
import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

NS = "{http://www.w3.org/1999/xhtml}"


# ---------------------------------------------------------------- bbox parse
def run_pdftotext_bbox(pdf_path, out_html):
    # -bbox-layout emits the page > flow > block > line > word hierarchy.
    cmd = ["pdftotext", "-bbox-layout", "-enc", "UTF-8", pdf_path, out_html]
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if p.returncode == 0:
        return
    # Retry via ASCII-safe temp paths (some poppler builds cannot open
    # non-ASCII paths on Windows).
    import tempfile
    tmp = tempfile.mkdtemp(prefix="pdfx_")
    tmp_pdf = os.path.join(tmp, "input.pdf")
    tmp_html = os.path.join(tmp, "bbox.html")
    shutil.copyfile(pdf_path, tmp_pdf)
    p2 = subprocess.run(["pdftotext", "-bbox-layout", "-enc", "UTF-8", tmp_pdf, tmp_html],
                        capture_output=True, text=True, errors="replace")
    if p2.returncode != 0:
        raise RuntimeError(f"pdftotext -bbox-layout failed: {p2.stderr.strip() or p.stderr.strip()}")
    shutil.copyfile(tmp_html, out_html)


def parse_bbox(html_path):
    """Return list of pages; each page: {width,height,blocks:[{bbox,lines:[{bbox,words:[{bbox,text}]}]}]}"""
    with open(html_path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    # Sanitize: pdftotext may emit control chars or bare '&' that break XML.
    raw = "".join(c for c in raw if c in "\t\n\r" or ord(c) >= 0x20)
    raw = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", raw)
    root = ET.fromstring(raw)
    pages = []
    for pel in root.iter(NS + "page"):
        page = {
            "width": float(pel.get("width")),
            "height": float(pel.get("height")),
            "blocks": [],
        }
        for bel in pel.iter(NS + "block"):
            block = {"bbox": [float(bel.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")],
                     "lines": []}
            for lel in bel.iter(NS + "line"):
                line = {"bbox": [float(lel.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")],
                        "words": []}
                for wel in lel.iter(NS + "word"):
                    txt = "".join(wel.itertext())
                    line["words"].append({
                        "bbox": [float(wel.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")],
                        "text": txt,
                    })
                block["lines"].append(line)
            page["blocks"].append(block)
        if not page["blocks"]:
            # Fallback for poppler builds whose bbox output has words only:
            # cluster words into lines by y-center, one block per line.
            words = []
            for wel in pel.iter(NS + "word"):
                words.append({"bbox": [float(wel.get(k)) for k in ("xMin", "yMin", "xMax", "yMax")],
                              "text": "".join(wel.itertext())})
            words.sort(key=lambda w: ((w["bbox"][1] + w["bbox"][3]) / 2, w["bbox"][0]))
            for w in words:
                cy = (w["bbox"][1] + w["bbox"][3]) / 2
                for blk in page["blocks"]:
                    l0 = blk["lines"][0]
                    lcy = (l0["bbox"][1] + l0["bbox"][3]) / 2
                    if abs(lcy - cy) <= 3.0:
                        l0["words"].append(w)
                        l0["bbox"] = [min(l0["bbox"][0], w["bbox"][0]),
                                      min(l0["bbox"][1], w["bbox"][1]),
                                      max(l0["bbox"][2], w["bbox"][2]),
                                      max(l0["bbox"][3], w["bbox"][3])]
                        break
                else:
                    page["blocks"].append({"bbox": list(w["bbox"]),
                                           "lines": [{"bbox": list(w["bbox"]), "words": [w]}]})
            for blk in page["blocks"]:
                blk["bbox"] = list(blk["lines"][0]["bbox"])
        pages.append(page)
    return pages


# ------------------------------------------------------------- text helpers
def line_text(line):
    return " ".join(w["text"] for w in line["words"]).strip()


def block_text(block):
    return "\n".join(line_text(l) for l in block["lines"] if line_text(l))


def normalize(s):
    return re.sub(r"\s+", " ", s).strip().lower()


# ------------------------------------------------------ header/footer strip
def strip_headers_footers(pages):
    """Drop blocks near page edges that repeat across pages (running heads,
    page numbers). Returns (pages, dropped_report)."""
    n = len(pages)
    candidates = {}  # normalized text -> count
    for page in pages:
        h = page["height"]
        seen = set()
        for b in page["blocks"]:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            if y1 < 0.08 * h or y0 > 0.93 * h:
                t = normalize(block_text(b))
                if t and t not in seen:
                    candidates[t] = candidates.get(t, 0) + 1
                    seen.add(t)
    thresh = max(3, int(0.4 * n)) if n >= 5 else 3
    repeated = {t for t, c in candidates.items() if c >= thresh}

    dropped = []
    for pi, page in enumerate(pages, 1):
        h = page["height"]
        kept = []
        for b in page["blocks"]:
            y0, y1 = b["bbox"][1], b["bbox"][3]
            t = normalize(block_text(b))
            edge = y1 < 0.08 * h or y0 > 0.93 * h
            # bare page numbers sit lower than running heads; a block that
            # is only a page-number token below 90% page height is always safe to drop
            is_pagenum = (edge or y0 > 0.90 * h) and \
                re.fullmatch(r"[\divxlcdm\-–—. ]{1,12}", t or "")
            if (edge and t in repeated) or is_pagenum:
                dropped.append({"page": pi, "text": block_text(b)})
                continue
            kept.append(b)
        page["blocks"] = kept
    return pages, dropped


# ------------------------------------------------------------ column logic
def find_gutter(page):
    """Find the column gutter: the widest x-run in the central band crossed
    by almost no text line. Full-width elements (title, spanning figures)
    cross any single x but are few, so a coverage threshold is used instead
    of demanding zero crossings. Returns (gutter_center, gutter_width)."""
    W = page["width"]
    all_lines = [l["bbox"] for b in page["blocks"] for l in b["lines"]]
    if len(all_lines) < 8:
        return None
    wide = [l for l in all_lines if (l[2] - l[0]) > 0.55 * W]
    # Single-column layout: most lines are wide -> there is no gutter.
    if len(wide) > 0.7 * len(all_lines):
        return None
    # Two-column pages: full-width lines (title, spanning tables) legitimately
    # cross any gutter; they must not vote against it.
    lines = [l for l in all_lines if (l[2] - l[0]) <= 0.55 * W]
    if len(lines) < 8:
        return None
    lo, hi = 0.30 * W, 0.70 * W
    step = max(1.0, W / 300.0)
    max_cross = max(3, int(0.04 * len(lines)))
    best = None
    x = lo
    while x < hi:
        x2 = x
        while x2 < hi and sum(1 for l in lines if l[0] < x2 < l[2]) <= max_cross:
            x2 += step
        width = x2 - x
        if width >= 0.015 * W and (best is None or width > best[1]):
            best = ((x + x2) / 2.0, width)
        x = x2 + step
    if best is None:
        return None
    # Verify against ALL lines: in a real two-column gutter only full-width
    # elements cross it (title, spanning tables) — at most ~25% of lines.
    # In a single-column layout the body text crosses everywhere.
    g = best[0]
    crossings = sum(1 for l in all_lines if l[0] < g < l[2])
    if crossings > 0.25 * len(all_lines):
        return None
    return best


def order_blocks(page):
    """Reading order for one page. Banding: full-width blocks split the page
    into horizontal bands; inside a band, left column top-down, then right."""
    gutter = find_gutter(page)
    page["gutter"] = gutter
    blocks = page["blocks"]
    if not gutter:
        return sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    g, gw = gutter
    pad = max(4.0, gw / 4.0)
    full, left, right = [], [], []
    for b in blocks:
        x0, _, x1, _ = b["bbox"]
        spans = x0 < g - pad and x1 > g + pad
        wide = (x1 - x0) > 0.62 * page["width"]
        # centered blocks spanning the gutter (title, authors, affiliation)
        # are full-width even when narrower than 62% of the page
        cx = (x0 + x1) / 2
        centered = abs(cx - page["width"] / 2) < 0.12 * page["width"] \
            and (x1 - x0) > 0.25 * page["width"]
        if spans and (wide or centered):
            full.append(b)
        elif x1 <= g + pad:
            left.append(b)
        else:
            right.append(b)
    full.sort(key=lambda b: b["bbox"][1])
    # bands between consecutive full-width blocks
    bands = []  # (y_lo, y_hi, full_block_or_None)
    edges = [(-1.0, None)]
    for fb in full:
        edges.append((fb["bbox"][1], fb))
    edges.append((float("inf"), None))
    ordered = []
    for i in range(len(edges) - 1):
        y_lo = edges[i][0]
        y_hi = edges[i + 1][0]
        # the full-width block starting this band (except the virtual first)
        if edges[i][1] is not None:
            ordered.append(edges[i][1])
        band_left = sorted([b for b in left if y_lo <= b["bbox"][1] < y_hi],
                           key=lambda b: (b["bbox"][1], b["bbox"][0]))
        band_right = sorted([b for b in right if y_lo <= b["bbox"][1] < y_hi],
                            key=lambda b: (b["bbox"][1], b["bbox"][0]))
        ordered.extend(band_left)
        ordered.extend(band_right)
    return ordered


# ---------------------------------------------------------- kind labelling
RE_SECTION = re.compile(r"^(\d+\.?\d*\.?\d*\.?|[IVX]+\.|[A-Z]\.)\s+\S")
RE_CAPTION = re.compile(r"^(figure|fig\.|table|algorithm|listing)\s*\d+", re.I)
RE_EQNUM = re.compile(r"\((\d{1,2}|[a-z]?\d+[a-z]?)\)\s*$")
RE_REFS = re.compile(r"^(references|bibliography|参考文献)\s*$", re.I)
RE_ABSTRACT = re.compile(r"^(abstract|摘要)[.:\-—]?", re.I)


def title_like(text):
    """Lexical guard for heading candidates: short noun phrase, not a
    sentence/footnote/axis-label/URL. `text` keeps original casing."""
    body = re.sub(r"^\d+(\.\d+)*\.?\s*", "", text)
    low = text.lower()
    return (len(text) < 90 and len(text.split()) <= 8
            and not text.endswith(".")
            and "(" not in text and "http" not in low and "%" not in text
            and (body[:1].isupper() or bool(re.match(r"^[一-鿿]", body))))


def classify(block, in_refs, med_h=10.0):
    txt = block_text(block)
    flat = normalize(txt)
    if not flat:
        return "empty", in_refs
    lines = [line_text(l) for l in block["lines"] if line_text(l)]
    nlines = len(lines)
    if RE_REFS.match(flat):
        return "heading", True
    if in_refs:
        return "reference", True
    if RE_CAPTION.match(flat):
        return "caption", in_refs
    if RE_ABSTRACT.match(flat):
        return "abstract", in_refs
    raw = re.sub(r"\s+", " ", txt).strip()
    if (RE_SECTION.match(flat) and nlines <= 2 and title_like(raw)
            and len(re.sub(r"[^a-zA-Z一-鿿]", "", flat)) >= 2):
        return "heading", in_refs
    if RE_EQNUM.search(flat) and nlines <= 4:
        return "equation", in_refs
    if _looks_like_table(block):
        return "table-candidate", in_refs
    return "paragraph", in_refs


def _looks_like_table(block):
    """Heuristic: >=3 lines, most containing >=2 wide internal gaps."""
    lines = block["lines"]
    if len(lines) < 3:
        return False
    hits = 0
    for l in lines:
        ws = l["words"]
        if len(ws) < 3:
            continue
        gaps = 0
        for a, b in zip(ws, ws[1:]):
            if b["bbox"][0] - a["bbox"][2] > 14.0:
                gaps += 1
        if gaps >= 2:
            hits += 1
    return hits >= max(2, len(lines) - 1)


# ------------------------------------------------------------- paragraphs
def join_paragraph(block):
    """Join lines of a block into one paragraph with dehyphenation."""
    parts = []
    for l in block["lines"]:
        t = line_text(l)
        if not t:
            continue
        if parts and parts[-1].endswith("-") and t[:1].islower():
            parts[-1] = parts[-1][:-1] + t
        else:
            parts.append(t)
    return " ".join(parts)


# ------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--outdir", default="work")
    ap.add_argument("--keep-headers", action="store_true")
    args = ap.parse_args()

    if shutil.which("pdftotext") is None:
        sys.exit("pdftotext not found on PATH; install poppler-utils")

    os.makedirs(args.outdir, exist_ok=True)
    bbox_html = os.path.join(args.outdir, "_bbox.html")
    run_pdftotext_bbox(args.pdf, bbox_html)
    pages = parse_bbox(bbox_html)

    dropped = []
    if not args.keep_headers:
        pages, dropped = strip_headers_footers(pages)

    def split_heading(b):
        """Some poppler blocks merge a section heading with the following
        paragraph. Split a heading-like first line into its own block."""
        if len(b["lines"]) < 2:
            return [b]
        t0 = normalize(line_text(b["lines"][0]))
        t0_raw = re.sub(r"\s+", " ", line_text(b["lines"][0])).strip()
        if (RE_SECTION.match(t0) and title_like(t0_raw)
                and len(re.sub(r"[^a-zA-Z一-鿿]", "", t0)) >= 2
                and not RE_CAPTION.match(t0)):
            head = {"bbox": list(b["lines"][0]["bbox"]), "lines": [b["lines"][0]]}
            rest_lines = b["lines"][1:]
            rest = {"bbox": [
                min(l["bbox"][0] for l in rest_lines),
                min(l["bbox"][1] for l in rest_lines),
                max(l["bbox"][2] for l in rest_lines),
                max(l["bbox"][3] for l in rest_lines)], "lines": rest_lines}
            return [head, rest]
        return [b]

    # median line height across the document: proxy for font size, used to
    # tell real section headings (tall lines) from axis labels / footnotes
    all_heights = [l["bbox"][3] - l["bbox"][1]
                   for p in pages for b in p["blocks"] for l in b["lines"]]
    all_heights.sort()
    med_h = all_heights[len(all_heights) // 2] if all_heights else 10.0

    def line_height(b):
        return max(l["bbox"][3] - l["bbox"][1] for l in b["lines"])

    def merge_split_numbers(ordered):
        """Some styles (e.g. NeurIPS) set the section number and its title
        as two adjacent one-line blocks. Merge `2` + `Background`."""
        out = []
        i = 0
        while i < len(ordered):
            b = ordered[i]
            t = normalize(block_text(b))
            if (re.fullmatch(r"\d+(\.\d+)*\.?", t or "") and i + 1 < len(ordered)
                    and len(ordered[i + 1]["lines"]) <= 2):
                tn = normalize(block_text(ordered[i + 1]))
                tn_raw = re.sub(r"\s+", " ", block_text(ordered[i + 1])).strip()
                if (title_like(tn_raw)
                        and len(re.sub(r"[^a-zA-Z一-鿿]", "", tn)) >= 2
                        and not RE_CAPTION.match(tn)):
                    nb = ordered[i + 1]
                    merged = {"bbox": [
                        min(b["bbox"][0], nb["bbox"][0]),
                        min(b["bbox"][1], nb["bbox"][1]),
                        max(b["bbox"][2], nb["bbox"][2]),
                        max(b["bbox"][3], nb["bbox"][3])],
                        "lines": b["lines"] + nb["lines"],
                        "_merged_heading": True}
                    out.append(merged)
                    i += 2
                    continue
            out.append(b)
            i += 1
        return out

    blocks_out = []
    bid = 0
    in_refs = False
    for pi, page in enumerate(pages, 1):
        for b in merge_split_numbers(order_blocks(page)):
            for b in split_heading(b):
                kind, in_refs = classify(b, in_refs, med_h)
                if kind == "empty":
                    continue
                text = block_text(b) if kind in ("equation", "table-candidate") else join_paragraph(b)
                bid += 1
                blocks_out.append({
                    "id": f"b{bid}",
                    "page": pi,
                    "bbox": [round(v, 1) for v in b["bbox"]],
                    "kind": kind,
                    "text": text,
                })

    with open(os.path.join(args.outdir, "blocks.jsonl"), "w", encoding="utf-8") as f:
        for b in blocks_out:
            f.write(json.dumps(b, ensure_ascii=False) + "\n")

    with open(os.path.join(args.outdir, "full.md"), "w", encoding="utf-8") as f:
        f.write(f"<!-- source: {os.path.basename(args.pdf)} | pages: {len(pages)} | "
                f"blocks: {len(blocks_out)} -->\n\n")
        for b in blocks_out:
            f.write(f"<!-- p{b['page']} {b['id']} {b['kind']} bbox={b['bbox']} -->\n")
            if b["kind"] == "heading":
                f.write(f"\n## {b['text']}\n\n")
            else:
                f.write(b["text"] + "\n\n")

    meta = {
        "source": args.pdf,
        "pages": [{
            "page": i + 1,
            "width": p["width"], "height": p["height"],
            "gutter": p.get("gutter"),
            "n_blocks": len(p["blocks"]),
        } for i, p in enumerate(pages)],
        "dropped_headers_footers": dropped,
        "n_blocks": len(blocks_out),
        "kinds": {k: sum(1 for b in blocks_out if b["kind"] == k)
                  for k in sorted({b["kind"] for b in blocks_out})},
    }
    with open(os.path.join(args.outdir, "pages.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "blocks": len(blocks_out),
        "kinds": meta["kinds"],
        "dropped_headers_footers": len(dropped),
        "outdir": os.path.abspath(args.outdir),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
