#!/usr/bin/env python3
"""self_check.py — Consistency checks after extraction, before answering.

Compares the raw pdftotext character count with the text kept in
blocks.jsonl so dropped pages/regions surface as numbers, and validates
result.json against the output contract.

Usage:
  python self_check.py <input.pdf> [--workdir work] [--result /app/output/result.json]
"""
import argparse
import json
import os
import re
import subprocess
import sys

REQUIRED_RESULT_KEYS = ["case_id", "status", "result", "sources", "uncertainty"]
VALID_STATUS = {"success", "partial", "cannot-determine", "refused", "failed"}


def char_count(s):
    return len(re.sub(r"\s", "", s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--workdir", default="work")
    ap.add_argument("--result", default=None)
    args = ap.parse_args()
    report = {"checks": [], "ok": True}

    def check(name, ok, detail):
        report["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        report["ok"] = report["ok"] and bool(ok)

    # 1. coverage: raw pdftotext vs kept blocks
    p = subprocess.run(["pdftotext", "-enc", "UTF-8", args.pdf, "-"],
                       capture_output=True, text=True, errors="replace")
    raw_chars = char_count(p.stdout)
    blocks_path = os.path.join(args.workdir, "blocks.jsonl")
    kept_chars = 0
    pages_seen = set()
    if os.path.exists(blocks_path):
        for line in open(blocks_path, encoding="utf-8"):
            b = json.loads(line)
            kept_chars += char_count(b["text"])
            pages_seen.add(b["page"])
    dropped = 1.0 - kept_chars / raw_chars if raw_chars else 0.0
    check("coverage", dropped < 0.25,
          f"raw={raw_chars} chars, kept={kept_chars}, dropped={dropped:.1%} "
          f"(header/footer removal typically drops 2-8%; >25% means content loss)")

    # 2. page presence (page count from form feeds; works without pdfinfo)
    npages = len(p.stdout.split("\f"))
    if npages and not p.stdout.split("\f")[-1].strip():
        npages -= 1
    missing = sorted(set(range(1, npages + 1)) - pages_seen)
    check("page-presence", not missing,
          f"{npages} pages; pages with no extracted block: {missing or 'none'}")

    # 3. result.json contract
    if args.result:
        if not os.path.exists(args.result):
            check("result-json", False, f"{args.result} not found")
        else:
            try:
                r = json.load(open(args.result, encoding="utf-8"))
                missing_keys = [k for k in REQUIRED_RESULT_KEYS if k not in r]
                ok = not missing_keys and r.get("status") in VALID_STATUS
                check("result-json", ok,
                      f"missing keys: {missing_keys or 'none'}; status={r.get('status')}")
            except Exception as e:
                check("result-json", False, f"invalid JSON: {e}")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
