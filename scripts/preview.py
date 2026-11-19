#!/usr/bin/env python3
"""Render README.md into preview.html, styled like a GitHub profile README, for local checks.

    python scripts/preview.py
    python -m http.server 8765      # then open http://127.0.0.1:8765/preview.html

Handles the subset of Markdown this README uses: ## headings, paragraphs, - lists, **bold**,
[links](url) and raw HTML blocks. Dark/light follows the system setting, like GitHub.
"""
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CSS = """
:root{--bg:#fff;--ink:#1f2328;--muted:#59636e;--border:#d1d9e0;--link:#0969da}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--ink:#f0f6fc;--muted:#9198a1;--border:#3d444d;--link:#4493f8}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 -apple-system,"Segoe UI","Noto Sans",Helvetica,Arial,sans-serif}
.box{max-width:846px;margin:24px auto;padding:24px 32px;border:1px solid var(--border);border-radius:6px}
h2{font-size:1.5em;font-weight:600;padding-bottom:.3em;border-bottom:1px solid var(--border);margin:24px 0 16px}
a{color:var(--link);text-decoration:none} img{max-width:100%;height:auto;box-sizing:content-box;vertical-align:middle}
table{border-collapse:collapse;display:block;max-width:100%;overflow:auto;width:max-content}
td{border:1px solid var(--border);padding:6px 13px} sub{font-size:75%;color:var(--muted)}
ul{padding-left:2em} li+li{margin-top:.25em} p{margin:0 0 16px}
@media (prefers-color-scheme:dark){[href$='#gh-light-mode-only']{display:none}}
@media (prefers-color-scheme:light){[href$='#gh-dark-mode-only']{display:none}}
"""


def inline(s):
    s = html.escape(s, quote=False)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)


def render(md):
    out, para, items = [], [], []

    def flush():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()
        if items:
            out.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()

    for line in md.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
        elif stripped.startswith("<") or stripped.startswith("<!--"):
            flush()
            out.append(line)
        elif stripped.startswith("## "):
            flush()
            out.append(f"<h2>{inline(stripped[3:])}</h2>")
        elif stripped.startswith("- "):
            items.append(stripped[2:])
        else:
            para.append(stripped)
    flush()
    return "\n".join(out)


def main():
    body = render((ROOT / "README.md").read_text(encoding="utf-8"))
    page = (f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>Profile preview</title><style>{CSS}</style><div class=\"box\">{body}</div>")
    (ROOT / "preview.html").write_text(page, encoding="utf-8")
    print("wrote preview.html")


if __name__ == "__main__":
    main()
