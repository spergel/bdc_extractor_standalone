#!/usr/bin/env python3
"""
Build an HTML page from a detect file (e.g. CSWC_detect_2026-02-02.txt) so you can
see each selected table in a readable, table-like layout.

Usage (from project root):
  python scripts/build_detect_tables_html.py output/CSWC_detect_2026-02-02.txt
  python scripts/build_detect_tables_html.py output/CSWC_detect_2026-02-02.txt -o output/CSWC_detect_2026-02-02.html
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def parse_detect_file(path: Path):
    """Parse detect file into list of {table_num, table_id, length, body}."""
    text = path.read_text(encoding="utf-8")
    tables = []
    first = text.split("\n")[0]
    header = first.strip()
    blocks = re.split(r"\[TABLE\s+(\d+)\]\s*\n", text)
    i = 1
    while i + 1 < len(blocks):
        num = blocks[i].strip()
        content = blocks[i + 1]
        table_id = ""
        length = ""
        body_lines = []
        for line in content.split("\n"):
            if line.startswith("table_id="):
                table_id = line.replace("table_id=", "").strip()
            elif line.startswith("length="):
                length = line.replace("length=", "").strip()
            elif line.strip() and not line.startswith("table_") and not line.startswith("length="):
                body_lines.append(line.strip())
        body = " ".join(body_lines) if body_lines else content.strip()
        tables.append({"num": num, "table_id": table_id, "length": length, "body": body})
        i += 2
    return header, tables


def build_html(header: str, tables: list, out_path: Path):
    """Write an HTML file with each table in a card and an optional grid view."""
    rows = []
    rows.append("<!DOCTYPE html>")
    rows.append("<html lang='en'><head><meta charset='utf-8'><title>Detect tables</title>")
    rows.append("<style>")
    rows.append("body { font-family: system-ui, sans-serif; margin: 1rem; background: #1a1a2e; color: #eee; }")
    rows.append("h1 { font-size: 1.2rem; margin-bottom: 0.5rem; }")
    rows.append(".meta { font-size: 0.85rem; color: #888; margin-bottom: 1rem; }")
    rows.append("section { margin-bottom: 2rem; border: 1px solid #333; border-radius: 8px; padding: 1rem; background: #16213e; }")
    rows.append("section h2 { font-size: 1rem; margin: 0 0 0.5rem 0; color: #7fdbff; }")
    rows.append("pre.raw { white-space: pre-wrap; word-break: break-word; font-size: 0.75rem; line-height: 1.3; margin: 0; overflow-x: auto; }")
    rows.append(".raw-note { font-size: 0.8rem; color: #888; margin-top: 0.25rem; }")
    rows.append(".toc { margin-bottom: 2rem; } .toc a { color: #7fdbff; }")
    rows.append("</style></head><body>")
    rows.append(f"<h1>{header}</h1>")
    rows.append("<div class='toc'><strong>Tables:</strong> ")
    rows.append(" ".join([f"<a href='#t{n}'>[{n}]</a>" for t in tables for n in [t["num"]]]))
    rows.append("</div>")

    for t in tables:
        num, table_id, length, body = t["num"], t["table_id"], t["length"], t["body"]
        rows.append(f"<section id='t{num}'>")
        rows.append(f"<h2>Table {num}</h2>")
        rows.append(f"<div class='meta'>id: {table_id} &middot; length: {length}</div>")
        rows.append("<pre class='raw'>")
        rows.append(body.replace("<", "&lt;").replace(">", "&gt;"))
        rows.append("</pre>")
        rows.append("<p class='raw-note'>Raw text as extracted from the filing (this is what gets sent to the LLM for CSV extraction).</p>")
        rows.append("</section>")

    rows.append("</body></html>")
    out_path.write_text("\n".join(rows), encoding="utf-8")


def main():
    import argparse
    p = argparse.ArgumentParser(description="Build HTML view of detect file tables")
    p.add_argument("detect_file", type=Path, help="e.g. output/CSWC_detect_2026-02-02.txt")
    p.add_argument("-o", "--output", type=Path, default=None, help="Output HTML path (default: same name with .html)")
    args = p.parse_args()

    path = args.detect_file
    if not path.is_absolute():
        path = REPO_ROOT / path
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    out = args.output
    if out is None:
        out = path.with_suffix(".html")
    if not out.is_absolute():
        out = REPO_ROOT / out

    header, tables = parse_detect_file(path)
    build_html(header, tables, out)
    print(f"Wrote {len(tables)} tables to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
