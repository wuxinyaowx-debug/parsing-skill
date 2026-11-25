#!/usr/bin/env python3
"""pdf_probe.py — Inspect a PDF before parsing.

Reports (JSON on stdout):
  - page count, page size(s) (when pdfinfo is available)
  - encryption / permission flags (when pdfinfo is available)
  - text-layer coverage per page (chars extracted by pdftotext)
  - classification: "digital" | "scanned" | "mixed" | "encrypted" | "empty"
  - a recommended processing route

Dependency: poppler-utils (pdftotext required; pdfinfo optional).
Python stdlib only.

Usage:
  python pdf_probe.py <input.pdf>
"""
import json
import re
import shutil
import subprocess
import sys


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return p.returncode, p.stdout, p.stderr


def parse_pdfinfo(text):
    info = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            info[k.strip()] = v.strip()
    return info


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"error": "usage: pdf_probe.py <input.pdf>"}))
        return 2
    pdf = sys.argv[1]

    if shutil.which("pdftotext") is None:
        print(json.dumps({"error": "pdftotext not found on PATH; install poppler-utils"}))
        return 3

    info = {}
    if shutil.which("pdfinfo"):
        rc, out, err = run(["pdfinfo", pdf])
        if rc == 0:
            info = parse_pdfinfo(out)
        elif not run(["pdftotext", "-l", "1", pdf, "-"])[0] == 0:
            print(json.dumps({
                "file": pdf,
                "classification": "encrypted-or-corrupt",
                "route": "try `pdftotext -upw '' <pdf>` or qpdf --decrypt; if it still fails, mark status=unreadable and stop",
                "pdfinfo_error": (err or out).strip(),
            }, ensure_ascii=False, indent=2))
            return 0

    # One pass over the whole document; form feed (\f) separates pages.
    rc, full, err = run(["pdftotext", "-enc", "UTF-8", pdf, "-"])
    if rc != 0:
        print(json.dumps({
            "file": pdf,
            "classification": "encrypted-or-corrupt",
            "route": "try `pdftotext -upw '' <pdf>` or qpdf --decrypt; if it still fails, mark status=unreadable and stop",
            "pdftotext_error": err.strip(),
        }, ensure_ascii=False, indent=2))
        return 0

    page_texts = full.split("\f")
    if page_texts and not page_texts[-1].strip():
        page_texts.pop()  # trailing form feed
    pages = int(info.get("Pages", "0") or 0) or len(page_texts)
    encrypted = info.get("Encrypted", "no").lower().startswith("yes")
    page_chars = [len(re.sub(r"\s", "", t)) for t in page_texts]

    nonempty = [c for c in page_chars if c >= 50]
    if pages == 0:
        cls = "empty"
    elif encrypted and not any(page_chars):
        cls = "encrypted"
    elif not nonempty:
        cls = "scanned"
    elif len(nonempty) < max(1, pages // 2):
        cls = "mixed"
    else:
        cls = "digital"

    routes = {
        "digital": "run pdf_extract.py (text layer is present); OCR not needed",
        "mixed": "run pdf_extract.py; for pages with <50 chars also run the OCR fallback (references/method.md section 5) and merge",
        "scanned": "no usable text layer; use the OCR fallback route (pdftoppm 300dpi + tesseract), see references/method.md section 5",
        "encrypted": "decrypt first (qpdf --decrypt or -upw); if impossible, report status=unreadable",
        "empty": "no pages detected; verify the file is a valid PDF",
    }

    result = {
        "file": pdf,
        "pages": pages,
        "page_size": info.get("Page size", "unknown (pdfinfo unavailable)"),
        "encrypted": encrypted,
        "producer": info.get("Producer", ""),
        "chars_per_page": page_chars,
        "classification": cls,
        "route": routes[cls],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
