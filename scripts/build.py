#!/usr/bin/env python3
"""Draw every image the profile README shows, from live GitHub and LeetCode data.

Standard library only, so the GitHub Action runs it without installing anything.
Words live in data/profile.json, the photo layers in data/portrait.json (made once by
scripts/portrait.py). This file only draws.

    python scripts/build.py

Set GITHUB_TOKEN to avoid the anonymous API rate limit (the workflow does).
Set PROFILE_CACHE=some/dir to cache HTTP responses while iterating locally.
"""
import datetime as dt
import hashlib
import json
import math
import os
import random
import re
import urllib.parse
import urllib.request
from base64 import b64encode
from html import escape, unescape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
CFG = json.loads((ROOT / "data" / "profile.json").read_text(encoding="utf-8"))
LC_CACHE = ROOT / "data" / "leetcode-cache.json"
LOGIN = CFG["login"]
TOKEN = os.environ.get("GITHUB_TOKEN", "")
CACHE = os.environ.get("PROFILE_CACHE")

MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"
DEVA = "'Noto Sans Devanagari','Nirmala UI','Kohinoor Devanagari','Mangal','Noto Sans',sans-serif"
BASE_CSS = (f"text{{font-family:{MONO}}}.s{{font-family:{SANS}}}.dv{{font-family:{DEVA}}}"
            "@media (prefers-reduced-motion:reduce){*{animation:none!important}}")

THEMES = {
    "dark": dict(bg="#0d1117", raised="#161b22", border="#30363d", ink="#e6edf3", muted="#8b949e",
                 faint="#21262d", violet="#a78bfa", cyan="#22d3ee", pink="#f472b6", amber="#fbbf24",
                 green="#34d399", red="#f87171", on="#0d1117",
                 ramp=["#161b22", "#2e1f5e", "#4c2f9e", "#7c5cf0", "#c4b5fd"]),
    "light": dict(bg="#ffffff", raised="#f6f8fa", border="#d0d7de", ink="#1f2328", muted="#59636e",
                  faint="#eaeef2", violet="#7c3aed", cyan="#0891b2", pink="#db2777", amber="#b45309",
                  green="#059669", red="#dc2626", on="#ffffff",
                  ramp=["#ebedf0", "#ddd6fe", "#c4b5fd", "#8b5cf6", "#6d28d9"]),
}
KIND = {   # project status -> (colour key, legend word)
    "team": ("violet", "team project"), "prototype": ("amber", "prototype"), "wip": ("cyan", "in progress"),
    "research": ("pink", "research"), "ongoing": ("green", "ongoing"), "done": ("green", "completed"),
}
LANG_COLOURS = {
    "Python": "#3572A5", "TypeScript": "#3178c6", "JavaScript": "#f1e05a", "HTML": "#e34c26",
    "CSS": "#663399", "C++": "#f34b7d", "Jupyter Notebook": "#DA5B0B", "C": "#555555", "Java": "#b07219",
}


# ================================================================ data

def fetch(url, payload=None, headers=None):
    key = hashlib.sha1((url + (payload or "")).encode()).hexdigest()
    if CACHE and (Path(CACHE) / key).exists():
        return (Path(CACHE) / key).read_text(encoding="utf-8")
    hdrs = {"User-Agent": "profile-readme-build", "Accept": "application/vnd.github+json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=payload.encode() if payload else None, headers=hdrs)
    if TOKEN and "api.github.com" in url:
        req.add_header("Authorization", "Bearer " + TOKEN)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    if CACHE:
        Path(CACHE).mkdir(parents=True, exist_ok=True)
        (Path(CACHE) / key).write_text(body, encoding="utf-8")
    return body


def api(path):
    return json.loads(fetch("https://api.github.com" + path))


def calendar_graphql():
    query = ("query($l:String!){user(login:$l){contributionsCollection{contributionCalendar{"
             "weeks{contributionDays{date contributionCount contributionLevel}}}}}}")
    body = json.loads(fetch("https://api.github.com/graphql",
                            json.dumps({"query": query, "variables": {"l": LOGIN}})))
    levels = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}
    weeks = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    return [(d["date"], d["contributionCount"], levels[d["contributionLevel"]])
            for w in weeks for d in w["contributionDays"]]


def calendar_html():
    page = fetch(f"https://github.com/users/{LOGIN}/contributions")
    counts = {}
    for cell_id, label in re.findall(r'<tool-tip[^>]*?for="([\w-]+)"[^>]*>([^<]*)</tool-tip>', page):
        m = re.match(r"\s*([\d,]+) contribution", unescape(label))
        counts[cell_id] = int(m.group(1).replace(",", "")) if m else 0
    cells = re.findall(r'<td[^>]*?data-date="([\d-]+)"[^>]*?id="([\w-]+)"[^>]*?data-level="(\d)"', page)
    if not cells:
        raise RuntimeError("could not read the contribution calendar")
    return [(date, counts.get(cell_id, 0), int(level)) for date, cell_id, level in cells]


def load_calendar():
    days = None
    if TOKEN:
        try:
            days = calendar_graphql()
        except Exception as err:                      # fall back to the public page
            print("graphql calendar failed, using public page:", err)
    days = sorted(days or calendar_html())
    longest = run = 0
    for _, count, _ in days:
        run = run + 1 if count else 0
        longest = max(longest, run)
    tail = days[:-1] if days[-1][1] == 0 else days    # today may simply not have happened yet
    current = 0
    for _, count, _ in reversed(tail):
        if not count:
            break
        current += 1
    return dict(days=days, total=sum(d[1] for d in days), longest=longest, current=current,
                active=sum(1 for d in days if d[1]), best=max(d[1] for d in days))


def leetcode():
    """Live numbers from LeetCode; the last good answer is kept so a blocked request never
    turns into made-up numbers, and the first-ever fallback is the conservative '300+'."""
    user = CFG["leetcode"]["username"]
    query = ("query($u:String!){matchedUser(username:$u){submitStatsGlobal{acSubmissionNum{difficulty count}}"
             " badges{name} userCalendar{streak totalActiveDays activeYears}}}")
    try:
        body = json.loads(fetch("https://leetcode.com/graphql",
                                json.dumps({"query": query, "variables": {"u": user}}),
                                {"Content-Type": "application/json", "Referer": f"https://leetcode.com/u/{user}/",
                                 "User-Agent": "Mozilla/5.0 (profile-readme-build)"}))
        mu = body["data"]["matchedUser"]
        counts = {x["difficulty"]: x["count"] for x in mu["submitStatsGlobal"]["acSubmissionNum"]}
        cal = mu.get("userCalendar") or {}
        years = cal.get("activeYears") or []
        lc = dict(solved=counts["All"], solved_label=str(counts["All"]), easy=counts.get("Easy"),
                  medium=counts.get("Medium"), hard=counts.get("Hard"), streak=cal.get("streak"),
                  active=cal.get("totalActiveDays"), year=max(years) if years else None,
                  badges=len(mu.get("badges") or []), as_of=dt.date.today().isoformat(), live=True)
        LC_CACHE.write_text(json.dumps(lc, indent=2) + "\n", encoding="utf-8")
        return lc
    except Exception as err:
        print("LeetCode request failed:", err)
    if LC_CACHE.exists():
        lc = json.loads(LC_CACHE.read_text(encoding="utf-8"))
        lc["live"] = False
        return lc
    fb = CFG["leetcode"]["fallback"]
    return dict(solved=fb["solved"], solved_label=fb["solved_label"], easy=None, medium=None, hard=None,
                streak=None, streak_label=fb["streak_label"], active=None, year=None, badges=None,
                as_of=None, live=False)


def load():
    user = api(f"/users/{LOGIN}")
    repos = [r for r in api(f"/users/{LOGIN}/repos?per_page=100&type=owner") if not r["fork"]]
    by_name = {r["name"]: r for r in repos}
    topics = {}
    lc_repo = by_name.get(CFG.get("leetcode_repo", ""))
    if lc_repo:
        tree = api(f"/repos/{LOGIN}/{lc_repo['name']}/git/trees/{lc_repo['default_branch']}?recursive=1")
        for node in tree["tree"]:
            parts = node["path"].split("/")
            if node["type"] == "blob" and len(parts) == 2 and re.match(r"\d+\s*-", parts[1]):
                topics[parts[0]] = topics.get(parts[0], 0) + 1
    langs = {}
    for r in repos:
        for lang, size in api(f"/repos/{LOGIN}/{r['name']}/languages").items():
            langs[lang] = langs.get(lang, 0) + size
    return dict(user=user, repos=by_name, topics=topics, lc_repo_total=sum(topics.values()), langs=langs,
                cal=load_calendar(), lc=leetcode(), stars=sum(r["stargazers_count"] for r in repos))


# ================================================================ drawing helpers

def esc(s):
    return escape(str(s), quote=True)


def num(n):
    return f"{n:,}"


def text(x, y, s, fill, size=12, weight=400, anchor="start", cls="", extra=""):
    c = f' class="{cls}"' if cls else ""
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{fill}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}"{c}{extra}>{esc(s)}</text>')


def svg(w, h, body, label, css=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{esc(label)}"><title>{esc(label)}</title>'
            f'<style>{BASE_CSS}{css}</style>{body}</svg>')


def frame(w, h, c, rx=12):
    return (f'<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="{rx}" fill="{c["bg"]}" '
            f'stroke="{c["border"]}"/>')


def wrap(s, width, max_lines):
    words, lines, line = s.split(), [], ""
    for w in words:
        if len(line) + len(w) + (1 if line else 0) > width:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}" if line else w
    if line:
        lines.append(line)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,") + "…"
    return lines


def write(name, content):
    (ASSETS / name).write_text(content, encoding="utf-8", newline="\n")


def pct(t, total):
    return f"{max(0.0, min(100.0, 100 * t / total)):.3f}%"


def window_kf(name, start, cycle, on, off, fade_at=0.93, ramp=0.04):
    """Keyframes for a looping element that switches on at `start` seconds and stays on until
    `fade_at` of the cycle, so every element of a looped scene resets together."""
    a = pct(start, cycle)
    b = pct(start + ramp * cycle, cycle)
    return (f"@keyframes {name}{{0%,{a}{{{off}}}{b},{fade_at * 100:.2f}%{{{on}}}100%{{{off}}}}}")


def fill_values(widths_at, cycle):
    """SMIL discrete values/keyTimes from [(t, value)], sorted, first at t=0."""
    times = ";".join(f"{min(t / cycle, 1):.5f}" for t, _ in widths_at)
    vals = ";".join(f"{v:.1f}" for _, v in widths_at)
    return vals, times


def typing_clip(cid, x, y, width_full, chars, begin_t, per_char, cycle=None, hold=None, h=22, begin=0):
    """A clipPath that reveals a line one character at a time (SMIL, works inside <img>)."""
    cw = width_full / max(chars, 1)
    pts = [(0.0, 0.0)]
    for k in range(1, chars + 1):
        pts.append((begin_t + k * per_char, k * cw))
    if cycle:
        pts.append((begin_t + hold, 0.0))
        vals, times = fill_values(pts, cycle)
        anim = (f'<animate attributeName="width" calcMode="discrete" dur="{cycle}s" values="{vals}" '
                f'keyTimes="{times}" begin="{begin}s" repeatCount="indefinite"/>')
    else:
        total = begin_t + chars * per_char + 0.01
        vals, times = fill_values(pts, total)
        anim = (f'<animate attributeName="width" calcMode="discrete" dur="{total:.3f}s" values="{vals}" '
                f'keyTimes="{times}" fill="freeze"/>')
    return (f'<clipPath id="{cid}"><rect x="{x}" y="{y - h + 6}" width="0" height="{h}">{anim}</rect></clipPath>')


# ================================================================ hero

def hero(d):
    P = json.loads((ROOT / "data" / "portrait.json").read_text(encoding="utf-8"))
    H = CFG["hero"]
    c = THEMES["dark"]
    W, HT = 1000, 500
    vx, vy, vw, vh = 24, 52, 380, 428
    s = 420 / P["h"]
    iw, ih = P["w"] * s, P["h"] * s
    ix, iy = vx + (vw - iw) / 2, vy + vh - ih
    fill = dict(lc_solved=d["lc"]["solved_label"], contrib=num(d["cal"]["total"]))

    def box(b):
        return ix + b[0] * s, iy + b[1] * s, b[2] * s, b[3] * s

    px, py, pw, ph = box(P["person"])
    fx, fy, fw, fh = box(P["face"])
    py = max(py, vy + 26)
    ph = min(ph, vy + vh - 34 - py)

    css = [
        "@keyframes rise{from{opacity:0;transform:translateY(7px)}}",
        "@keyframes fadein{from{opacity:0}}",
        ".r{animation:rise .55s cubic-bezier(.2,.7,.2,1) backwards}",
        "@keyframes blink{50%{opacity:0}}.blink{animation:blink 1.05s steps(1) infinite}",
        # intro: scan reveals Canny edges, the colour frame denoises in, then detection locks on
        f"@keyframes scan1{{0%{{opacity:0;transform:translateY(0)}}8%,90%{{opacity:1}}"
        f"100%{{opacity:0;transform:translateY({vh}px)}}}}",
        ".scan1{opacity:0;animation:scan1 1.1s linear .9s}",
        "@keyframes edgeIntro{0%,70%{opacity:1}100%{opacity:0}}",
        ".edge1{opacity:0;animation:edgeIntro 2.9s linear .9s}",
        "@keyframes phin{from{opacity:0;transform:scale(1.03)}}",
        f".ph{{transform-origin:{ix + iw / 2:.0f}px {iy + ih / 2:.0f}px;"
        "animation:phin .5s cubic-bezier(.2,.7,.2,1) 2s backwards}",
    ]
    for k, (a, op) in enumerate([(2.0, .9), (2.35, .68), (2.7, .45), (3.05, .22)]):
        css.append(f"@keyframes nz{k}{{from,to{{opacity:{op}}}}}.nz{k}{{opacity:0;animation:nz{k} .35s {a}s}}")
    css += [
        "@keyframes draw{from{stroke-dashoffset:100}}",
        ".det{animation:fadein .2s 3.4s backwards}",
        ".det .ln{stroke-dasharray:100;animation:draw .6s cubic-bezier(.3,.7,.2,1) 3.4s backwards}",
        "@keyframes pop{from{opacity:0;transform:scale(.6)}}",
        ".tag{transform-box:fill-box;transform-origin:0 100%;animation:pop .35s cubic-bezier(.3,1.4,.5,1) 3.85s backwards}",
        "@keyframes breathe{0%,100%{opacity:1}50%{opacity:.55}}",
        ".br{animation:breathe 2.6s ease-in-out 4.5s infinite}",
        # loop every 9 s: a re-scan with an edge flash, then a two-frame RGB glitch
        f"@keyframes scan2{{0%{{opacity:0;transform:translateY(0)}}1.5%,13%{{opacity:.9}}"
        f"14.5%,100%{{opacity:0;transform:translateY({vh}px)}}}}",
        ".scan2{opacity:0;animation:scan2 9s linear 6s infinite}",
        "@keyframes edge2{0%,100%{opacity:0}4%,9%{opacity:.55}16%{opacity:0}}",
        ".edge2{opacity:0;animation:edge2 9s linear 6s infinite}",
        "@keyframes gl{0%,90%{opacity:0;transform:none}90.5%{opacity:.9;transform:translate(-6px,0)}"
        "91.6%{opacity:0}93%{opacity:.75;transform:translate(4px,2px)}94%,100%{opacity:0;transform:none}}",
        ".gl{opacity:0;mix-blend-mode:screen;animation:gl 9s steps(1) 6s infinite}",
        "@keyframes glb{0%,90%{opacity:0;transform:none}90.5%{opacity:.85;transform:translate(6px,-1px)}"
        "91.6%{opacity:0}93%{opacity:.7;transform:translate(-3px,0)}94%,100%{opacity:0;transform:none}}",
        ".glb{opacity:0;mix-blend-mode:screen;animation:glb 9s steps(1) 6s infinite}",
        "@keyframes hud{from,to{opacity:1}}",
        ".hf{opacity:0}",
        "@keyframes hudlast{from{opacity:0}}.hl{animation:hudlast .3s 3.4s backwards}",
    ]
    hud_frames = [(0.9, 2.0, "stage 1/3 · edges"), (2.0, 2.35, "stage 2/3 · denoise 1/4"),
                  (2.35, 2.7, "stage 2/3 · denoise 2/4"), (2.7, 3.05, "stage 2/3 · denoise 3/4"),
                  (3.05, 3.4, "stage 2/3 · denoise 4/4")]
    for k, (a, b, _) in enumerate(hud_frames):
        css.append(f".hf{k}{{animation:hud {b - a:.2f}s {a}s}}")

    defs = [
        '<linearGradient id="ng" x1="0" x2="1" spreadMethod="reflect"><stop offset="0" stop-color="#c4b5fd"/>'
        '<stop offset=".5" stop-color="#f472b6"/><stop offset="1" stop-color="#22d3ee"/>'
        '<animateTransform attributeName="gradientTransform" type="translate" values="0 0;1 0;0 0" dur="9s" '
        'repeatCount="indefinite"/></linearGradient>',
        '<radialGradient id="glow" cx=".5" cy=".42" r=".55"><stop offset="0" stop-color="#7c3aed" stop-opacity=".55"/>'
        '<stop offset=".6" stop-color="#7c3aed" stop-opacity=".12"/><stop offset="1" stop-color="#7c3aed" stop-opacity="0"/>'
        '</radialGradient>',
        '<radialGradient id="glow2" cx="1" cy="0" r="1"><stop offset="0" stop-color="#22d3ee" stop-opacity=".10"/>'
        '<stop offset="1" stop-color="#22d3ee" stop-opacity="0"/></radialGradient>',
        '<pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><path d="M20 0H0V20" fill="none" '
        'stroke="#a78bfa" stroke-opacity=".09"/></pattern>',
        '<pattern id="dots" width="16" height="16" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1" '
        'fill="#8b949e" fill-opacity=".10"/></pattern>',
        '<linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#22d3ee" stop-opacity="0"/>'
        '<stop offset=".85" stop-color="#22d3ee" stop-opacity=".35"/><stop offset="1" stop-color="#e0fbff"/></linearGradient>',
        f'<clipPath id="vp"><rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" rx="10"/></clipPath>',
        f'<clipPath id="reveal"><rect x="{vx}" y="{vy}" width="{vw}" height="0">'
        f'<animate attributeName="height" from="0" to="{vh}" begin=".9s" dur="1.1s" fill="freeze"/></rect></clipPath>',
        f'<image id="photo" href="{P["colour"]}" x="{ix:.1f}" y="{iy:.1f}" width="{iw:.1f}" height="{ih:.1f}"/>',
        f'<image id="edges" href="{P["edges"]}" x="{ix:.1f}" y="{iy:.1f}" width="{iw:.1f}" height="{ih:.1f}"/>',
        f'<mask id="sil" maskUnits="userSpaceOnUse" x="{ix:.1f}" y="{iy:.1f}" width="{iw:.1f}" height="{ih:.1f}">'
        f'<image href="{P["silhouette"]}" x="{ix:.1f}" y="{iy:.1f}" width="{iw:.1f}" height="{ih:.1f}"/></mask>',
        '<filter id="cyanf"><feColorMatrix type="matrix" values="0 0 0 0 0  .3 .6 .1 0 .05  .3 .6 .1 0 .12  0 0 0 1 0"/></filter>',
        '<filter id="pinkf"><feColorMatrix type="matrix" values=".35 .6 .1 0 .15  0 0 0 0 0  .3 .5 .1 0 .1  0 0 0 1 0"/></filter>',
    ]
    for k, seed in enumerate([3, 11, 23, 41]):
        defs.append(f'<filter id="nf{k}" x="0" y="0" width="1" height="1"><feTurbulence type="fractalNoise" '
                    f'baseFrequency="{0.95 - k * 0.12:.2f}" numOctaves="2" seed="{seed}"/>'
                    '<feColorMatrix type="matrix" values=".6 .6 0 0 -.12  .4 .4 0 0 -.1  .7 .7 0 0 .02  0 0 0 0 1"/></filter>')

    b = [f'<defs>{"".join(defs)}</defs>',
         f'<rect x="0.5" y="0.5" width="{W - 1}" height="{HT - 1}" rx="14" fill="#0b0e14" stroke="{c["border"]}"/>',
         f'<rect x="1" y="36" width="{W - 2}" height="{HT - 37}" fill="url(#dots)"/>',
         f'<rect x="{W / 2:.0f}" y="36" width="{W / 2 - 1:.0f}" height="{HT - 37}" fill="url(#glow2)"/>',
         f'<path d="M1 36H{W - 1}" stroke="{c["border"]}"/>']
    for k, col in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        b.append(f'<circle cx="{22 + k * 18}" cy="18" r="5.5" fill="{col}"/>')
    b.append(text(W / 2, 22, H["title"], c["muted"], 12, anchor="middle"))
    b.append(f'<g><circle cx="{W - 74}" cy="18" r="4" fill="{c["pink"]}" class="blink"/>'
             + text(W - 64, 22, "live", c["muted"], 11) + '</g>')

    # ---- left: camera viewport
    v = [f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" fill="#0a0d16"/>',
         f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" fill="url(#grid)"/>',
         f'<ellipse cx="{ix + iw / 2:.0f}" cy="{iy + ih * 0.42:.0f}" rx="{iw * 0.62:.0f}" ry="{ih * 0.55:.0f}" fill="url(#glow)"/>',
         f'<g clip-path="url(#reveal)"><use href="#edges" class="edge1"/></g>',
         '<use href="#photo" class="ph"/>',
         '<use href="#photo" filter="url(#cyanf)" class="gl"/>',
         '<use href="#photo" filter="url(#pinkf)" class="glb"/>',
         '<use href="#edges" class="edge2"/>']
    for k in range(4):
        v.append(f'<rect x="{ix:.1f}" y="{iy:.1f}" width="{iw:.1f}" height="{ih:.1f}" filter="url(#nf{k})" '
                 f'mask="url(#sil)" class="nz{k}"/>')
    for cls in ("scan1", "scan2"):
        v.append(f'<g class="{cls}"><rect x="{vx}" y="{vy - 60}" width="{vw}" height="60" fill="url(#beam)"/>'
                 f'<rect x="{vx}" y="{vy - 1.5}" width="{vw}" height="1.5" fill="#e0fbff"/></g>')

    # detection boxes
    def corners(x, y, w, h, col, L=14, sw=3):
        return (f'<path d="M{x} {y + L}V{y}H{x + L}M{x + w - L} {y}H{x + w}V{y + L}M{x + w} {y + h - L}V{y + h}'
                f'H{x + w - L}M{x + L} {y + h}H{x}V{y + h - L}" fill="none" stroke="{col}" stroke-width="{sw}" '
                f'stroke-linecap="round"/>')

    def tag(x, y, label, col, right=False):
        w = len(label) * 6.6 + 14
        x = x - w if right else x
        return (f'<g class="tag"><rect x="{x:.1f}" y="{y - 17:.1f}" width="{w:.1f}" height="17" rx="3" fill="{col}"/>'
                + text(x + 7, y - 5, label, c["on"], 11, 700) + '</g>')

    det = [f'<g class="det"><g class="br">',
           f'<rect class="ln" pathLength="100" x="{px:.1f}" y="{py:.1f}" width="{pw:.1f}" height="{ph:.1f}" '
           f'fill="none" stroke="{c["violet"]}" stroke-width="1.4" stroke-opacity=".8"/>',
           corners(round(px, 1), round(py, 1), round(pw, 1), round(ph, 1), c["violet"]),
           f'<rect class="ln" pathLength="100" x="{fx:.1f}" y="{fy:.1f}" width="{fw:.1f}" height="{fh:.1f}" '
           f'fill="none" stroke="{c["cyan"]}" stroke-width="1.4"/>',
           corners(round(fx, 1), round(fy, 1), round(fw, 1), round(fh, 1), c["cyan"], 9, 2.5),
           '</g>',
           tag(px + 2, py + 19, "vaishnavi 0.99", c["violet"]),
           tag(fx + fw, fy - 2, "face 0.98", c["cyan"], right=True),
           '</g>']
    v += det

    # HUD
    v.append(text(vx + 12, vy + 20, "CAM 01 · 1080p", c["muted"], 10.5))
    v.append(f'<circle cx="{vx + vw - 50}" cy="{vy + 16.5}" r="4" fill="#ff4d6d" class="blink"/>')
    v.append(text(vx + vw - 12, vy + 20, "REC", c["ink"], 10.5, 700, "end"))
    v.append(f'<rect x="{vx}" y="{vy + vh - 26}" width="{vw}" height="26" fill="#0a0d16" fill-opacity=".72"/>')
    v.append(text(vx + 12, vy + vh - 9, "edges → denoise → detect", c["muted"], 10.5))
    for k, (_, _, label) in enumerate(hud_frames):
        v.append(f'<g class="hf hf{k}">' + text(vx + vw - 12, vy + vh - 9, label, c["amber"], 10.5, 400, "end") + '</g>')
    v.append(f'<g class="hl"><circle cx="{vx + vw - 84}" cy="{vy + vh - 12.5}" r="3.5" fill="{c["green"]}" '
             f'class="blink"/>' + text(vx + vw - 12, vy + vh - 9, "tracking", c["green"], 10.5, 400, "end") + '</g>')
    b.append(f'<g clip-path="url(#vp)">{"".join(v)}</g>')
    b.append(f'<rect x="{vx}" y="{vy}" width="{vw}" height="{vh}" rx="10" fill="none" stroke="{c["border"]}"/>')
    b.append(corners(vx - 5, vy - 5, vw + 10, vh + 10, c["violet"], 18, 2.5).replace("<path", '<path opacity=".9"'))

    # ---- right: the run log, then who she is
    x0 = 430
    cmd = H["command"]
    cw = 7.8
    b.append(typing_clip("tc", x0 + 16, 82, len(cmd) * cw + 2, len(cmd), 0.2, 0.017))
    b.append(text(x0, 82, "$", c["violet"], 13, 700))
    b.append(f'<g clip-path="url(#tc)">' + text(x0 + 16, 82, cmd, c["ink"], 13,
                                                 extra=f' textLength="{len(cmd) * cw:.1f}" lengthAdjust="spacing"') + '</g>')
    logs = [("[1/3]", "edges  ", "cv2.Canny(frame)", 0.9), ("[2/3]", "denoise", "steps=4", 2.0),
            ("[3/3]", "detect ", "person · face", 3.4)]
    for k, (stage, name, detail, t) in enumerate(logs):
        y = 108 + k * 19
        b.append(f'<g class="r" style="animation-delay:{t}s">'
                 + text(x0, y, stage, c["muted"], 11.5) + text(x0 + 50, y, name.strip(), c["cyan"], 11.5)
                 + text(x0 + 116, y, detail, c["muted"], 11.5)
                 + text(x0 + 298, y, "ok", c["green"], 11.5, 700) + '</g>')
    b.append(f'<g class="r" style="animation-delay:4.1s">'
             + text(x0 - 2, 206, CFG["name"], "url(#ng)", 42, 800, cls="s",
                    extra=' letter-spacing="-0.5"') + '</g>')
    b.append(f'<g class="r" style="animation-delay:4.3s">' + text(x0, 233, H["role"], c["muted"], 13) + '</g>')
    b.append(f'<path d="M{x0} 252H{W - 26}" stroke="{c["border"]}" stroke-dasharray="3 4" class="r" '
             f'style="animation-delay:4.4s"/>')
    for k, (key, val) in enumerate(H["rows"]):
        y = 280 + k * 24
        b.append(f'<g class="r" style="animation-delay:{4.5 + k * 0.11:.2f}s">'
                 + text(x0, y, key, c["violet"], 12.5, 700) + text(x0 + 104, y, val.format(**fill), c["ink"], 12.5)
                 + '</g>')

    # cycling taglines, typed and erased
    tags = H["taglines"]
    slot, per = 4.4, 0.045
    cycle = slot * len(tags)
    ty = 448
    tx = x0 + 18
    b.append(f'<g class="r" style="animation-delay:5s">' + text(x0, ty, "❯", c["pink"], 13, 700) + '</g>')
    cursor_pts = [(0.0, tx)]
    for k, t in enumerate(tags):
        start = k * slot
        b.append(typing_clip(f"tg{k}", tx, ty, len(t) * cw + 1, len(t), start, per, cycle, slot - 0.5, begin=5.2))
        b.append(f'<g clip-path="url(#tg{k})">' + text(tx, ty, t, c["ink"], 13,
                 extra=f' textLength="{len(t) * cw:.1f}" lengthAdjust="spacing"') + '</g>')
        for j in range(1, len(t) + 1):
            cursor_pts.append((start + j * per, tx + j * cw + 2))
        cursor_pts.append((start + slot - 0.5, tx))
    cursor_pts.sort()
    vals, times = fill_values(cursor_pts, cycle)
    b.append(f'<g class="blink"><rect x="{tx}" y="{ty - 12}" width="8" height="15" fill="{c["violet"]}" '
             f'opacity=".85"><animate attributeName="x" calcMode="discrete" dur="{cycle}s" values="{vals}" '
             f'keyTimes="{times}" begin="5.2s" repeatCount="indefinite"/></rect></g>')
    b.append(text(x0, 478, f"github.com/{LOGIN}  ·  leetcode.com/u/{CFG['leetcode']['username']}  ·  Surat, India",
                  c["muted"], 10.5))

    label = (f"{CFG['name']}: {H['role']}. " + "; ".join(f"{k} {v.format(**fill)}" for k, v in H["rows"])
             + ". Animated portrait: an edge-detection scan, a denoise pass and detection boxes.")
    write("hero.svg", svg(W, HT, "".join(b), label, "".join(css)))


# ================================================================ project cards and their little scenes

def art_wave(c):
    bars, css = [], ["@keyframes wv{0%,100%{transform:scaleY(.35)}50%{transform:scaleY(1)}}",
                     ".wb{transform-box:fill-box;transform-origin:center;animation:wv 1.3s ease-in-out infinite}",
                     "@keyframes ph{from{transform:translateX(0)}to{transform:translateX(116px)}}",
                     ".play{animation:ph 3.2s linear infinite}",
                     "@keyframes fl{0%,100%{opacity:.35}50%{opacity:1}}.flag{animation:fl 1.6s ease-in-out infinite}"]
    for i in range(30):
        h = 8 + 34 * abs(math.sin(i * 0.71) * math.cos(i * 0.23))
        quiet = 18 <= i <= 22
        h = 4 if quiet else h
        x = 2 + i * 3.9
        bars.append(f'<rect class="wb" x="{x:.1f}" y="{46 - h / 2:.1f}" width="2.4" height="{h:.1f}" rx="1.2" '
                    f'fill="{c["violet"] if not quiet else c["pink"]}" style="animation-delay:{i * 0.06:.2f}s"/>')
    pitch = " ".join(f"{2 + i * 4:.0f},{70 - 7 * math.sin(i * 0.5) - 3 * math.cos(i * 1.3):.1f}" for i in range(30))
    return ("".join(css), f'<rect x="70" y="20" width="22" height="52" rx="4" fill="{c["pink"]}" fill-opacity=".12" '
            f'stroke="{c["pink"]}" stroke-dasharray="3 3" class="flag"/>'
            + text(81, 15, "pause", c["pink"], 9, 700, "middle") + "".join(bars)
            + f'<polyline points="{pitch}" fill="none" stroke="{c["cyan"]}" stroke-width="1.3" stroke-opacity=".8"/>'
            + f'<g class="play"><path d="M2 14V82" stroke="{c["ink"]}" stroke-opacity=".55"/></g>'
            + text(60, 96, "pace · pause · pitch · loud", c["muted"], 8.5, anchor="middle"))


def art_verdict(c):
    T = 6.0
    css, out = [], [text(36, 12, "v1", c["muted"], 10, 700, "middle"), text(74, 12, "v2", c["muted"], 10, 700, "middle")]
    v2 = [True, True, False, True, True]
    for i in range(5):
        y = 26 + i * 13
        out.append(text(4, y + 3.5, f"t{i + 1}", c["muted"], 9))
        for col, ok, x in ((0, True, 36), (1, v2[i], 74)):
            name = f"m{i}{col}"
            start = 0.3 + i * 0.32 + col * 0.14
            css.append(window_kf(name, start, T, "opacity:1", "opacity:0") + f".{name}{{animation:{name} {T}s infinite}}")
            colr = c["green"] if ok else c["red"]
            glyph = (f'<path d="M{x - 3} {y}l2.2 2.4 4-4.6" stroke="{c["on"]}" stroke-width="1.6" fill="none" '
                     f'stroke-linecap="round"/>' if ok else
                     f'<path d="M{x - 2.6} {y - 2.6}l5.2 5.2M{x + 2.6} {y - 2.6}l-5.2 5.2" stroke="{c["on"]}" '
                     f'stroke-width="1.6" stroke-linecap="round"/>')
            out.append(f'<g class="{name}"><circle cx="{x}" cy="{y}" r="5.4" fill="{colr}"/>{glyph}</g>')
    css.append(window_kf("vd", 2.4, T, "opacity:1;transform:none", "opacity:0;transform:translateY(4px)")
               + ".vd{animation:vd 6s infinite}")
    out.append(f'<g class="vd"><rect x="2" y="84" width="112" height="17" rx="8.5" fill="{c["amber"]}" '
               f'fill-opacity=".16" stroke="{c["amber"]}"/>' + text(58, 96, "verdict: t3 regressed", c["amber"], 9, 700,
                                                                     "middle") + '</g>')
    return "".join(css), "".join(out)


def art_garden(c):
    T = 6.0
    css, out = [], []
    for i in range(3):
        y = 18 + i * 18
        name = f"ck{i}"
        css.append(window_kf(name, 0.4 + i * 0.55, T, "opacity:1", "opacity:0") + f".{name}{{animation:{name} {T}s infinite}}")
        out.append(f'<rect x="2" y="{y - 6}" width="11" height="11" rx="3" fill="none" stroke="{c["muted"]}"/>')
        out.append(f'<path class="{name}" d="M4.5 {y}l2.2 2.4 4-4.6" stroke="{c["cyan"]}" stroke-width="1.8" '
                   f'fill="none" stroke-linecap="round"/>')
        out.append(f'<rect x="19" y="{y - 2}" width="{36 - i * 6}" height="4" rx="2" fill="{c["muted"]}" fill-opacity=".45"/>')
    css.append(window_kf("stem", 1.9, T, "transform:scaleY(1)", "transform:scaleY(0)", ramp=0.12)
               + ".stem{transform-box:fill-box;transform-origin:50% 100%;animation:stem 6s cubic-bezier(.3,.7,.3,1) infinite}")
    for k, t in enumerate([2.5, 2.8]):
        css.append(window_kf(f"lf{k}", t, T, "transform:scale(1)", "transform:scale(0)", ramp=0.06)
                   + f".lf{k}{{transform-box:fill-box;transform-origin:{'100% 100%' if k == 0 else '0 100%'};"
                     f"animation:lf{k} 6s infinite}}")
    css.append(window_kf("bl", 3.2, T, "transform:scale(1)", "transform:scale(0)", ramp=0.06)
               + ".bl{transform-box:fill-box;transform-origin:center;animation:bl 6s cubic-bezier(.3,1.5,.5,1) infinite}")
    css.append(window_kf("sk", 3.5, T, "opacity:1", "opacity:0") + ".sk{animation:sk 6s infinite}")
    out += [f'<path class="stem" d="M93 82V40" stroke="{c["green"]}" stroke-width="2.4" stroke-linecap="round"/>',
            f'<path class="lf0" d="M93 64c-10 0-15-6-15-12 9 0 15 4 15 12z" fill="{c["green"]}"/>',
            f'<path class="lf1" d="M93 54c9 0 14-6 14-11-8 0-14 4-14 11z" fill="{c["green"]}" fill-opacity=".8"/>',
            f'<g class="bl"><circle cx="93" cy="36" r="6" fill="{c["pink"]}"/><circle cx="93" cy="36" r="2.4" '
            f'fill="{c["amber"]}"/></g>',
            f'<path d="M78 82h30l-4 16H82z" fill="{c["violet"]}" fill-opacity=".85"/>',
            f'<g class="sk">' + text(2, 82, "streak +1", c["amber"], 10, 700) + '</g>',
            text(2, 97, "focus garden", c["muted"], 8.5)]
    return "".join(css), "".join(out)


def art_scale(c):
    T = 5
    angles = [-13, 10, -7, 4.5, -2.5, 1.2, 0, 0]
    times = [0, .14, .28, .42, .56, .7, .84, 1]
    cx, cy = 60, 30
    vals = ";".join(f"{a} {cx} {cy}" for a in angles)
    kt = ";".join(str(t) for t in times)
    spl = ";".join([".4 0 .6 1"] * (len(times) - 1))
    beam = (f'<animateTransform attributeName="transform" type="rotate" values="{vals}" keyTimes="{kt}" '
            f'calcMode="spline" keySplines="{spl}" dur="{T}s" repeatCount="indefinite"/>')

    def pan(x):
        counter = ";".join(f"{-a} {x} {cy}" for a in angles)
        return (f'<g><animateTransform attributeName="transform" type="rotate" values="{counter}" keyTimes="{kt}" '
                f'calcMode="spline" keySplines="{spl}" dur="{T}s" repeatCount="indefinite"/>'
                f'<path d="M{x} {cy}L{x - 13} {cy + 26}M{x} {cy}L{x + 13} {cy + 26}" stroke="{c["muted"]}"/>'
                f'<path d="M{x - 17} {cy + 26}h34a17 9 0 0 1-34 0z" fill="{c["amber"]}" fill-opacity=".9"/></g>')
    css = window_kf("st", 4.2, T, "opacity:1", "opacity:0", fade_at=0.99) + ".st{animation:st 5s infinite}"
    out = (f'<path d="M{cx} {cy}V86M44 92h32l-6-6H50z" stroke="{c["muted"]}" fill="{c["muted"]}" stroke-width="2.5" '
           f'stroke-linejoin="round"/>'
           f'<g>{beam}<path d="M18 {cy}H102" stroke="{c["ink"]}" stroke-width="3" stroke-linecap="round"/>'
           f'{pan(20)}{pan(100)}</g><circle cx="{cx}" cy="{cy}" r="4" fill="{c["violet"]}"/>'
           f'<g class="st"><rect x="34" y="4" width="52" height="15" rx="7.5" fill="{c["green"]}" fill-opacity=".16" '
           f'stroke="{c["green"]}"/>' + text(60, 15, "stable", c["green"], 9, 700, "middle") + '</g>')
    return css, out


def art_scan(c):
    T = 5.0
    css = ["@keyframes sb{0%{transform:translateY(0);opacity:0}6%{opacity:1}55%{transform:translateY(62px);opacity:1}"
           "60%,100%{transform:translateY(62px);opacity:0}}.sb{animation:sb 5s linear infinite}"]
    out = [f'<path d="M22 22l30-12 46 10-30 12z" fill="{c["violet"]}" fill-opacity=".35"/>',
           f'<path d="M22 22v56l46 12V32z" fill="{c["violet"]}" fill-opacity=".22" stroke="{c["violet"]}" stroke-opacity=".6"/>',
           f'<path d="M68 32l30-12v56L68 90z" fill="{c["violet"]}" fill-opacity=".12" stroke="{c["violet"]}" stroke-opacity=".6"/>',
           f'<rect x="28" y="40" width="34" height="34" rx="2" fill="{c["bg"]}" stroke="{c["muted"]}"/>']
    fields = [(31, 45, 22), (31, 53, 28), (31, 61, 16), (31, 68, 25)]
    for k, (x, y, w) in enumerate(fields):
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="3" rx="1.5" fill="{c["muted"]}"/>')
        name = f"fd{k}"
        css.append(window_kf(name, 0.6 + k * 0.55, T, "opacity:1", "opacity:0") + f".{name}{{animation:{name} 5s infinite}}")
        out.append(f'<rect class="{name}" x="{x - 2}" y="{y - 2}" width="{w + 4}" height="7" rx="2" fill="none" '
                   f'stroke="{c["cyan"]}" stroke-dasharray="2 1.5"/>')
    out.append(f'<g class="sb"><rect x="20" y="20" width="82" height="16" fill="{c["cyan"]}" fill-opacity=".12"/>'
               f'<path d="M20 36H102" stroke="{c["cyan"]}" stroke-width="1.5"/></g>')
    css.append(window_kf("ok", 3.1, T, "opacity:1;transform:none", "opacity:0;transform:scale(.5)")
               + ".ok{transform-box:fill-box;transform-origin:center;animation:ok 5s cubic-bezier(.3,1.5,.5,1) infinite}")
    out.append(f'<g class="ok"><circle cx="100" cy="80" r="11" fill="{c["green"]}"/><path d="M94.5 80l3.6 3.8 6.4-7.4" '
               f'stroke="{c["on"]}" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>')
    return "".join(css), "".join(out)


def art_hindi(c):
    T = 6.0
    css = ["@keyframes nl{0%,30%{opacity:1}33%,100%{opacity:0}}.nl{animation:nl 6s infinite}",
           "@keyframes jit{0%,100%{transform:none}25%{transform:translateX(-1px)}75%{transform:translateX(1px)}}",
           ".jit{animation:jit .18s steps(2) infinite}",
           window_kf("wd", 2.0, T, "opacity:1;transform:none", "opacity:0;transform:translateY(-5px)")
           + ".wd{animation:wd 6s infinite}",
           window_kf("dn", 3.0, T, "opacity:1", "opacity:0") + ".dn{animation:dn 6s infinite}"]
    out = [text(24, 34, "वह", c["ink"], 14, 600, "end", "dv"),
           f'<g class="nl"><g class="jit"><rect x="29" y="20" width="36" height="19" rx="4" fill="{c["pink"]}" '
           f'fill-opacity=".14" stroke="{c["pink"]}" stroke-dasharray="3 2"/>'
           + text(47, 33, "NULL", c["pink"], 9, 700, "middle") + '</g></g>',
           f'<g class="wd"><rect x="29" y="20" width="36" height="19" rx="4" fill="{c["green"]}" fill-opacity=".14"/>'
           + text(47, 34, "घर", c["green"], 14, 700, "middle", "dv") + '</g>',
           text(70, 34, "जा रहा है", c["ink"], 14, 600, cls="dv"),
           f'<g class="dn">' + text(118, 34, "।", c["green"], 15, 700, "end", "dv") + '</g>']
    css.append("@keyframes eb{0%,100%{transform:scaleY(.3)}50%{transform:scaleY(1)}}"
               ".eb{transform-box:fill-box;transform-origin:50% 100%;animation:eb 1.8s ease-in-out infinite}")
    for i in range(12):
        h = 10 + 18 * abs(math.sin(i * 1.7))
        colr = [c["violet"], c["cyan"], c["pink"]][i % 3]
        out.append(f'<rect class="eb" x="{8 + i * 9}" y="{82 - h:.1f}" width="5" height="{h:.1f}" rx="2" fill="{colr}" '
                   f'fill-opacity=".8" style="animation-delay:{i * 0.12:.2f}s"/>')
    out.append(text(60, 96, "tokens → embeddings", c["muted"], 8.5, anchor="middle"))
    return "".join(css), "".join(out)


def art_code(c):
    T = 6.0
    css, out = [], [f'<rect x="0" y="0" width="118" height="78" rx="6" fill="{c["raised"]}" stroke="{c["border"]}"/>']
    rows = [(10, 46, "violet"), (18, 62, "cyan"), (18, 40, "ink"), (26, 54, "pink"), (18, 30, "ink"), (10, 18, "violet")]
    for i, (indent, w, col) in enumerate(rows):
        y = 12 + i * 10.5
        out.append(text(7, y + 3, str(i + 1), c["muted"], 7.5, anchor="middle"))
        name = f"cl{i}"
        css.append(window_kf(name, 0.3 + i * 0.3, T, "transform:scaleX(1)", "transform:scaleX(0)", ramp=0.05)
                   + f".{name}{{transform-box:fill-box;transform-origin:0 50%;animation:{name} 6s steps(6) infinite}}")
        out.append(f'<rect class="{name}" x="{indent + 6}" y="{y - 1.5}" width="{w}" height="4" rx="2" '
                   f'fill="{c[col]}" fill-opacity="{.85 if col != "ink" else .5}"/>')
    css.append(window_kf("ac", 2.5, T, "opacity:1;transform:none", "opacity:0;transform:translateY(5px)")
               + ".ac{animation:ac 6s cubic-bezier(.3,1.4,.5,1) infinite}")
    out.append(f'<g class="ac"><rect x="0" y="84" width="74" height="17" rx="8.5" fill="{c["green"]}" fill-opacity=".16" '
               f'stroke="{c["green"]}"/>' + text(37, 96, "✓ Accepted", c["green"], 9.5, 700, "middle") + '</g>')
    out.append(text(118, 96, "C++", c["muted"], 9.5, 700, "end"))
    return "".join(css), "".join(out)


def art_grid(c):
    T = 4.0
    css = [f"@keyframes sh{{from{{transform:translateX(-60px)}}to{{transform:translateX(140px)}}}}"
           ".shine{animation:sh 1.6s linear infinite}"]
    out = [f'<rect x="0" y="0" width="118" height="12" rx="6" fill="{c["raised"]}" stroke="{c["border"]}"/>',
           f'<path d="M6 3.5v5l4.5-2.5z" fill="{c["violet"]}"/>',
           f'<rect x="34" y="3" width="56" height="6" rx="3" fill="{c["muted"]}" fill-opacity=".3"/>',
           '<defs><clipPath id="thumbs">']
    tiles = []
    for r in range(2):
        for k in range(3):
            x, y = k * 40, 18 + r * 40
            tiles.append((x, y))
            out.append(f'<rect x="{x}" y="{y}" width="38" height="22" rx="3"/>')
    out.append('</clipPath><linearGradient id="sg" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
               '<stop offset=".5" stop-color="#fff" stop-opacity=".35"/><stop offset="1" stop-color="#fff" stop-opacity="0"/>'
               '</linearGradient></defs>')
    palette = [c["violet"], c["pink"], c["cyan"], c["amber"], c["green"], c["violet"]]
    for i, (x, y) in enumerate(tiles):
        name = f"tl{i}"
        css.append(window_kf(name, 0.5 + i * 0.22, T, "opacity:.85", "opacity:0") + f".{name}{{animation:{name} 4s infinite}}")
        out.append(f'<rect x="{x}" y="{y}" width="38" height="22" rx="3" fill="{c["muted"]}" fill-opacity=".18"/>')
        out.append(f'<rect class="{name}" x="{x}" y="{y}" width="38" height="22" rx="3" fill="{palette[i]}"/>')
        out.append(f'<rect x="{x}" y="{y + 26}" width="30" height="3" rx="1.5" fill="{c["muted"]}" fill-opacity=".45"/>')
        out.append(f'<rect x="{x}" y="{y + 32}" width="20" height="3" rx="1.5" fill="{c["muted"]}" fill-opacity=".3"/>')
    out.append(f'<path d="M56 25v8l7-4z" fill="#fff" fill-opacity=".9"/>')
    out.append('<g clip-path="url(#thumbs)"><rect class="shine" x="0" y="14" width="50" height="70" fill="url(#sg)"/></g>')
    return "".join(css), "".join(out)


ARTS = {"wave": art_wave, "verdict": art_verdict, "garden": art_garden, "scale": art_scale, "scan": art_scan,
        "hindi": art_hindi, "code": art_code, "grid": art_grid}


def project_cards(d, mode):
    c = THEMES[mode]
    W, H = 480, 200
    for p in CFG["projects"]:
        col = c[KIND[p["kind"]][0]]
        badge = p["badge"].format(lc_repo=d["lc_repo_total"])
        css, art = ARTS[p["art"]](c)
        css += ("@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}.pd{animation:pulse 2s ease-in-out infinite}"
                "@keyframes rise{from{opacity:0;transform:translateY(6px)}}"
                ".r{animation:rise .6s cubic-bezier(.2,.7,.2,1) backwards}")
        b = [f'<defs><linearGradient id="top" x1="0" x2="1"><stop offset="0" stop-color="{col}"/>'
             f'<stop offset="1" stop-color="{col}" stop-opacity="0"/></linearGradient>'
             f'<radialGradient id="halo" cx="1" cy="0" r="1"><stop offset="0" stop-color="{col}" stop-opacity=".10"/>'
             f'<stop offset="1" stop-color="{col}" stop-opacity="0"/></radialGradient></defs>',
             frame(W, H, c),
             f'<rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="11.5" fill="url(#halo)"/>',
             f'<path d="M12 1.2H{W * 0.7:.0f}" stroke="url(#top)" stroke-width="2"/>',
             text(22, 40, p["title"], c["ink"], 18, 700, cls="s")]
        pw = len(badge) * 6.3 + 28
        b.append(f'<g class="r" style="animation-delay:.15s"><rect x="22" y="52" width="{pw:.1f}" height="21" rx="10.5" '
                 f'fill="{col}" fill-opacity=".12" stroke="{col}" stroke-opacity=".45"/>'
                 f'<circle class="pd" cx="34" cy="62.5" r="3.5" fill="{col}"/>'
                 + text(43, 66.5, badge, col, 10.5, 700) + '</g>')
        for i, line in enumerate(wrap(p["about"], 46, 5)):
            b.append(text(22, 98 + i * 17, line, c["ink"], 12.5, cls="s r",
                          extra=f' style="animation-delay:{.25 + i * .06:.2f}s"'))
        b.append(text(22, 184, p["stack"], c["muted"], 10.5))
        link = ("↗ live demo" if "github.io" in (p.get("url") or "") else "↗ repo") if p.get("url") else "no public repo"
        b.append(text(W - 22, 184, link, c["muted"], 10.5, anchor="end"))
        b.append(f'<g transform="translate(346 24)">{art}</g>')
        label = f"{p['title']} ({badge}): {p['about']} Stack: {p['stack']}."
        write(f"card-{p['key']}-{mode}.svg", svg(W, H, "".join(b), label, css))


# ================================================================ DSA: LeetCode card and BFS over topics

def leetcode_card(d, mode):
    c = THEMES[mode]
    lc = d["lc"]
    W, H = 980, 250
    css = ["@keyframes ring{from{stroke-dashoffset:var(--len)}}",
           "@keyframes grow{from{transform:scaleX(0)}}",
           ".bar{transform-box:fill-box;transform-origin:0 50%;animation:grow 1s cubic-bezier(.2,.7,.2,1) backwards}",
           "@keyframes rise{from{opacity:0;transform:translateY(6px)}}.r{animation:rise .6s cubic-bezier(.2,.7,.2,1) backwards}"]
    b = [frame(W, H, c), text(24, 34, "LeetCode", c["ink"], 14, 700),
         text(98, 34, f"· {CFG['leetcode']['username']}", c["muted"], 13)]
    when = dt.date.fromisoformat(lc["as_of"]).strftime("%d %b %Y") if lc.get("as_of") else None
    note = (f"live from leetcode.com · {when}" if lc.get("live") else
            f"last fetched {when}" if when else "self-reported count")
    b.append(text(W - 24, 34, note, c["muted"], 11, anchor="end"))

    # donut
    cx, cy, r = 112, 140, 62
    circ = 2 * math.pi * r
    b.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{c["faint"]}" stroke-width="12"/>')
    parts = [(lc.get("easy"), c["cyan"], "Easy"), (lc.get("medium"), c["amber"], "Medium"), (lc.get("hard"), c["pink"], "Hard")]
    if all(p[0] is not None for p in parts) and lc["solved"]:
        start = 0.0
        for k, (n, col, _) in enumerate(parts):
            frac = n / lc["solved"]
            seg = max(frac * circ - 3, 0.5)
            b.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" stroke-width="12" '
                     f'stroke-linecap="butt" stroke-dasharray="{seg:.1f} {circ:.1f}" '
                     f'transform="rotate({-90 + start * 360:.2f} {cx} {cy})" '
                     f'style="--len:{seg:.1f};animation:ring 1.1s cubic-bezier(.2,.7,.2,1) {0.2 + k * 0.25:.2f}s backwards"/>')
            start += frac
    else:
        b.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{c["violet"]}" stroke-width="12" '
                 f'stroke-dasharray="{circ:.1f}" style="--len:{circ:.1f};animation:ring 1.4s cubic-bezier(.2,.7,.2,1) backwards"/>')
    b.append(text(cx, cy + 6, lc["solved_label"], c["ink"], 34, 800, "middle", "s"))
    b.append(text(cx, cy + 26, "solved", c["muted"], 11.5, anchor="middle"))

    # difficulty + streak
    x1, x2 = 214, 486
    if all(p[0] is not None for p in parts):
        top = max(p[0] for p in parts) or 1
        for k, (n, col, name) in enumerate(parts):
            y = 84 + k * 30
            b.append(f'<circle cx="{x1 + 5}" cy="{y - 4}" r="4.5" fill="{col}"/>')
            b.append(text(x1 + 18, y, name, c["ink"], 12.5))
            b.append(text(x2, y, num(n), c["ink"], 12.5, 700, "end"))
            b.append(f'<rect x="{x1}" y="{y + 7}" width="{x2 - x1}" height="4" rx="2" fill="{c["faint"]}"/>')
            b.append(f'<rect class="bar" x="{x1}" y="{y + 7}" width="{(x2 - x1) * n / top:.1f}" height="4" rx="2" '
                     f'fill="{col}" style="animation-delay:{.3 + k * .15:.2f}s"/>')
    stats = []
    if lc.get("streak"):
        stats.append((f"longest streak · {lc['year']}" if lc.get("year") else "longest streak", f"{lc['streak']} days"))
    elif lc.get("streak_label"):
        stats.append(("longest streak", f"{lc['streak_label']} days"))
    if lc.get("active"):
        stats.append((f"active days · {lc['year']}" if lc.get("year") else "active days", num(lc["active"])))
    if lc.get("badges"):
        stats.append(("badges", str(lc["badges"])))
    for k, (name, val) in enumerate(stats):
        y = 188 + k * 20
        b.append(f'<g class="r" style="animation-delay:{.6 + k * .1:.2f}s">' + text(x1, y, name, c["muted"], 12)
                 + text(x2, y, val, c["violet"], 12, 700, "end") + '</g>')

    # topics from the solutions repo
    tx, bx0, bx1, tx2 = 540, 688, 916, 956
    b.append(f'<path d="M518 60V226" stroke="{c["border"]}"/>')
    b.append(text(tx, 72, f"solutions in {CFG['leetcode_repo']} · {d['lc_repo_total']}", c["ink"], 12, 700))
    top = sorted(d["topics"].items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    most = top[0][1] if top else 1
    for k, (name, n) in enumerate(top):
        y = 98 + k * 17.5
        b.append(text(tx, y, name, c["muted"], 11))
        b.append(f'<rect x="{bx0}" y="{y - 8}" width="{bx1 - bx0}" height="8" rx="4" fill="{c["faint"]}"/>')
        b.append(f'<rect class="bar" x="{bx0}" y="{y - 8}" width="{(bx1 - bx0) * n / most:.1f}" height="8" rx="4" '
                 f'fill="{c["violet"]}" fill-opacity="{1 - k * 0.07:.2f}" style="animation-delay:{.2 + k * .07:.2f}s"/>')
        b.append(text(tx2, y, str(n), c["ink"], 11, 700, "end"))
    label = (f"LeetCode {CFG['leetcode']['username']}: {lc['solved_label']} problems solved"
             + (f" (easy {lc['easy']}, medium {lc['medium']}, hard {lc['hard']})" if lc.get("easy") is not None else "")
             + ". " + "; ".join(f"{a} {v}" for a, v in stats) + ". Top topics in the solutions repository: "
             + ", ".join(f"{n} {k}" for n, k in top))
    write(f"leetcode-{mode}.svg", svg(W, H, "".join(b), label, "".join(css)))


def bfs_card(mode):
    c = THEMES[mode]
    cfg = CFG["dsa_topics"]
    W, H = 980, 384
    levels, edges = cfg["levels"], cfg["edges"]
    pos = {}
    for li, row in enumerate(levels):
        n = len(row)
        for i, name in enumerate(row):
            pos[name] = (490 + (i - (n - 1) / 2) * 188, 94 + li * 68)
    adj = {}
    for a, z in edges:
        adj.setdefault(a, []).append(z)
    root = levels[0][0]
    order, parent, seen, queue, frames = [], {}, {root}, [root], []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for nxt in adj.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                parent[nxt] = node
                queue.append(nxt)
        frames.append(list(queue))
    N = len(order)
    t0, step, hold = 1.0, 0.62, 3.2
    T = round(t0 + N * step + hold, 2)
    css = []
    b = [frame(W, H, c), text(24, 34, "bfs(topics)", c["violet"], 14, 700),
         text(126, 34, "· the DSA topics I have studied and practised, visited breadth-first", c["muted"], 12.5),
         text(W - 24, 34, "C++ · STL", c["muted"], 12, anchor="end")]
    tree_edges = {(parent[n], n) for n in parent}
    for a, z in edges:
        (ax, ay), (zx, zy) = pos[a], pos[z]
        dashed = (a, z) not in tree_edges
        b.append(f'<path d="M{ax:.0f} {ay + 14:.0f}C{ax:.0f} {ay + 44:.0f} {zx:.0f} {zy - 44:.0f} {zx:.0f} {zy - 14:.0f}" '
                 f'fill="none" stroke="{c["border"]}" stroke-width="1.3"'
                 f'{" stroke-dasharray=\"2 5\" stroke-opacity=\".55\"" if dashed else ""}/>')
    for k, node in enumerate(order):
        t = t0 + k * step
        if node in parent:
            a = parent[node]
            (ax, ay), (zx, zy) = pos[a], pos[node]
            name = f"e{k}"
            css.append(f"@keyframes {name}{{0%,{pct(t - 0.35, T)}{{stroke-dashoffset:100;opacity:1}}"
                       f"{pct(t, T)},93%{{stroke-dashoffset:0;opacity:1}}100%{{stroke-dashoffset:0;opacity:0}}}}"
                       f".{name}{{stroke-dasharray:100;animation:{name} {T}s linear infinite}}")
            b.append(f'<path class="{name}" pathLength="100" d="M{ax:.0f} {ay + 14:.0f}C{ax:.0f} {ay + 44:.0f} {zx:.0f} '
                     f'{zy - 44:.0f} {zx:.0f} {zy - 14:.0f}" fill="none" stroke="{c["violet"]}" stroke-width="2.2"/>')
    for k, node in enumerate(order):
        x, y = pos[node]
        w = len(node) * 6.7 + 26
        t = t0 + k * step
        b.append(f'<rect x="{x - w / 2:.1f}" y="{y - 14}" width="{w:.1f}" height="28" rx="14" fill="{c["raised"]}" '
                 f'stroke="{c["border"]}"/>')
        b.append(text(x, y + 4, node, c["muted"], 11.5, anchor="middle"))
        lit, ring = f"n{k}", f"q{k}"
        css.append(window_kf(lit, t, T, "opacity:1", "opacity:0", ramp=0.02) + f".{lit}{{animation:{lit} {T}s infinite}}")
        css.append(f"@keyframes {ring}{{0%,{pct(t, T)}{{opacity:0;transform:scale(1)}}{pct(t + 0.05, T)}{{opacity:1}}"
                   f"{pct(t + 0.9, T)},100%{{opacity:0;transform:scale(1.35)}}}}"
                   f".{ring}{{opacity:0;transform-box:fill-box;transform-origin:center;animation:{ring} {T}s infinite}}")
        b.append(f'<rect class="{ring}" x="{x - w / 2:.1f}" y="{y - 14}" width="{w:.1f}" height="28" rx="14" fill="none" '
                 f'stroke="{c["pink"]}" stroke-width="2"/>')
        b.append(f'<g class="{lit}"><rect x="{x - w / 2:.1f}" y="{y - 14}" width="{w:.1f}" height="28" rx="14" '
                 f'fill="{c["violet"]}" fill-opacity=".16" stroke="{c["violet"]}" stroke-width="1.6"/>'
                 + text(x, y + 4, node, c["ink"], 11.5, 700, "middle")
                 + f'<text x="{x - w / 2 + 2:.1f}" y="{y - 18}" fill="{c["violet"]}" font-size="9.5" '
                   f'font-weight="700">#{k + 1}</text></g>')
    # queue strip
    y = H - 30
    b.append(f'<path d="M24 {y - 22}H{W - 24}" stroke="{c["border"]}" stroke-dasharray="3 4"/>')
    b.append(text(24, y, "queue", c["pink"], 12, 700))
    for k, q in enumerate(frames):
        t = t0 + k * step
        end = t0 + (k + 1) * step if k < N - 1 else T * 0.93
        name = f"f{k}"
        css.append(f"@keyframes {name}{{0%,{pct(t, T)}{{opacity:0}}{pct(t + 0.01, T)},{pct(end, T)}{{opacity:1}}"
                   f"{pct(end + 0.01, T)},100%{{opacity:0}}}}.{name}{{opacity:0;animation:{name} {T}s steps(1) infinite}}")
        shown = q[:4]
        more = f"  +{len(q) - 4}" if len(q) > 4 else ""
        qtext = ("[ " + "  ·  ".join(shown) + more + " ]") if q else "[ ]  done: every topic visited"
        b.append(f'<g class="{name}">' + text(76, y, qtext, c["ink"], 12)
                 + text(W - 24, y, f"visited {k + 1}/{N}", c["muted"], 12, anchor="end") + '</g>')
    label = ("Breadth-first search over the DSA topics I have studied, starting from " + root + ". Visit order: "
             + ", ".join(order))
    write(f"bfs-{mode}.svg", svg(W, H, "".join(b), label, "".join(css)))


# ================================================================ hackathons and the learning log

def hackathon_card(mode):
    c = THEMES[mode]
    items = CFG["hackathons"]
    W = 980
    row = 84
    top = 76
    H = top + row * len(items) - 10
    css = ["@keyframes rise{from{opacity:0;transform:translateX(-8px)}}.r{animation:rise .6s cubic-bezier(.2,.7,.2,1) backwards}",
           f"@keyframes travel{{from{{transform:translateY(0);opacity:0}}10%,85%{{opacity:1}}"
           f"to{{transform:translateY({row * (len(items) - 1)}px);opacity:0}}}}",
           ".dot{animation:travel 4.5s cubic-bezier(.5,0,.5,1) 1s infinite}",
           "@keyframes ping{0%{transform:scale(1);opacity:.7}100%{transform:scale(2.6);opacity:0}}",
           ".ping{transform-box:fill-box;transform-origin:center;animation:ping 2.4s ease-out infinite}"]
    b = [frame(W, H, c), text(24, 34, "$ git log --team --hackathons", c["muted"], 12.5),
         f'<path d="M44 {top}V{top + row * (len(items) - 1)}" stroke="{c["border"]}" stroke-width="2"/>',
         f'<circle class="dot" cx="44" cy="{top}" r="4" fill="{c["cyan"]}"/>']
    cols = [c["violet"], c["cyan"], c["green"], c["pink"]]
    for k, it in enumerate(items):
        y = top + k * row
        col = cols[k % len(cols)]
        b.append(f'<circle class="ping" cx="44" cy="{y}" r="7" fill="none" stroke="{col}" style="animation-delay:{k * .6:.1f}s"/>')
        b.append(f'<circle cx="44" cy="{y}" r="7" fill="{c["bg"]}" stroke="{col}" stroke-width="2.4"/>')
        b.append(f'<circle cx="44" cy="{y}" r="2.6" fill="{col}"/>')
        g = [text(72, y + 5, it["title"], c["ink"], 16, 700, cls="s")]
        tw = len(it["tag"]) * 6.4 + 22
        g.append(f'<rect x="{W - 24 - tw:.1f}" y="{y - 10}" width="{tw:.1f}" height="20" rx="10" fill="{col}" '
                 f'fill-opacity=".12" stroke="{col}" stroke-opacity=".5"/>')
        g.append(text(W - 24 - tw / 2, y + 4, it["tag"], col, 10.5, 700, "middle"))
        g.append(text(72, y + 27, it["line"], c["muted"], 12))
        cx = 72
        for chip in it["chips"]:
            cw = len(chip) * 6.2 + 16
            g.append(f'<rect x="{cx}" y="{y + 36}" width="{cw:.1f}" height="18" rx="5" fill="{c["raised"]}" stroke="{c["border"]}"/>')
            g.append(text(cx + cw / 2, y + 48.5, chip, c["ink"], 10.5, anchor="middle"))
            cx += cw + 7
        b.append(f'<g class="r" style="animation-delay:{.2 + k * .18:.2f}s">{"".join(g)}</g>')
    label = "Hackathons and activities: " + "; ".join(f"{it['title']} ({it['tag']}): {it['line']}" for it in items)
    write(f"hackathons-{mode}.svg", svg(W, H, "".join(b), label, "".join(css)))


def learning_card(mode):
    c = THEMES[mode]
    tracks = CFG["learning"]
    W = 980
    top = 116
    H = top + 21 * len(tracks) + 40
    css = ["@keyframes rise{from{opacity:0;transform:translateY(5px)}}.r{animation:rise .5s cubic-bezier(.2,.7,.2,1) backwards}",
           "@keyframes spin{to{transform:rotate(360deg)}}",
           ".sp{transform-box:fill-box;transform-origin:center;animation:spin 1.1s linear infinite}",
           "@keyframes blink{50%{opacity:0}}.blink{animation:blink 1.05s steps(1) infinite}"]
    b = [frame(W, H, c), f'<path d="M1 34H{W - 1}" stroke="{c["border"]}"/>']
    for k, col in enumerate(["#ff5f57", "#febc2e", "#28c840"]):
        b.append(f'<circle cx="{20 + k * 17}" cy="17" r="5" fill="{col}"/>')
    b.append(text(W / 2, 21, "train.py — interests.yaml", c["muted"], 11.5, anchor="middle"))
    cmd = "python train.py --tracks interests.yaml --patience inf"
    cw = 7.8
    b.append(typing_clip("tc", 42, 66, len(cmd) * cw + 2, len(cmd), 0.2, 0.02))
    b.append(text(24, 66, "$", c["violet"], 13, 700))
    b.append('<g clip-path="url(#tc)">' + text(42, 66, cmd, c["ink"], 13,
                                                extra=f' textLength="{len(cmd) * cw:.1f}" lengthAdjust="spacing"') + '</g>')
    b.append(f'<g class="r" style="animation-delay:1.3s">'
             + text(24, 90, f"loaded {len(tracks)} tracks · mode=continuous · these are areas I am learning, "
                            "not skills I claim yet", c["muted"], 11.5) + '</g>')
    for k, (key, title) in enumerate(tracks):
        y = top + k * 21
        dots = "." * max(3, 76 - len(title))
        g = [text(24, y, f"[{k + 1:02d}/{len(tracks)}]", c["muted"], 12),
             text(104, y, key, c["cyan"], 12),
             text(276, y, f"{title} {dots}", c["ink"], 12),
             f'<circle class="sp" cx="{W - 116}" cy="{y - 4}" r="5" fill="none" stroke="{c["violet"]}" stroke-width="2" '
             f'stroke-dasharray="20 12" style="animation-delay:{-k * .13:.2f}s"/>',
             text(W - 104, y, "training", c["violet"], 12, 700)]
        b.append(f'<g class="r" style="animation-delay:{1.5 + k * .12:.2f}s">{"".join(g)}</g>')
    y = top + 21 * len(tracks) + 14
    last = "early stopping: disabled — still learning"
    b.append(f'<g class="r" style="animation-delay:{1.6 + len(tracks) * .12:.2f}s">'
             + text(24, y, last, c["green"], 12, extra=f' textLength="{len(last) * 7.2:.1f}" lengthAdjust="spacing"')
             + f'<rect class="blink" x="{24 + len(last) * 7.2 + 5:.0f}" y="{y - 11}" width="8" height="14" fill="{c["green"]}"/></g>')
    label = "Learning log, areas I am actively exploring: " + "; ".join(t for _, t in tracks)
    write(f"learning-{mode}.svg", svg(W, H, "".join(b), label, "".join(css)))


# ================================================================ GitHub stats, languages, contribution city

def stats_card(d, mode):
    c = THEMES[mode]
    cal, user = d["cal"], d["user"]
    W, H = 480, 200
    since = dt.datetime.fromisoformat(user["created_at"].replace("Z", "+00:00")).strftime("%b %Y")
    tiles = [(num(cal["total"]), "contributions", "last 12 months", c["violet"]),
             (num(cal["active"]), "active days", "last 12 months", c["cyan"]),
             (f"{cal['longest']}d", "longest streak", "of daily commits", c["pink"]),
             (str(user["public_repos"]), "public repos", "on GitHub", c["amber"]),
             (f"{cal['current']}d", "current streak", "ending today", c["green"]),
             (since, "on GitHub since", "", c["muted"])]
    css = "@keyframes rise{from{opacity:0;transform:translateY(6px)}}.r{animation:rise .6s cubic-bezier(.2,.7,.2,1) backwards}"
    b = [frame(W, H, c), text(22, 32, "GitHub at a glance", c["ink"], 13, 700)]
    for k, (val, name, sub, col) in enumerate(tiles):
        x, y = 22 + (k % 3) * 150, 58 + (k // 3) * 70
        b.append(f'<g class="r" style="animation-delay:{.1 + k * .08:.2f}s">'
                 f'<rect x="{x}" y="{y}" width="138" height="60" rx="8" fill="{c["raised"]}" stroke="{c["border"]}"/>'
                 f'<rect x="{x}" y="{y + 12}" width="3" height="36" rx="1.5" fill="{col}"/>'
                 + text(x + 14, y + 29, val, c["ink"], 20 if len(val) < 7 else 15, 800, cls="s")
                 + text(x + 14, y + 47, name, c["muted"], 10.5) + '</g>')
    label = "GitHub statistics: " + "; ".join(f"{n} {v}" for v, n, _, _ in tiles)
    write(f"stats-{mode}.svg", svg(W, H, "".join(b), label, css))


def langs_card(d, mode):
    c = THEMES[mode]
    W, H = 480, 200
    total = sum(d["langs"].values()) or 1
    top = sorted(d["langs"].items(), key=lambda kv: -kv[1])
    shown = top[:7]
    other = total - sum(v for _, v in shown)
    if other > 0:
        shown.append(("Other", other))
    css = ("@keyframes grow{from{transform:scaleX(0)}}.seg{transform-box:fill-box;transform-origin:0 50%;"
           "animation:grow .8s cubic-bezier(.2,.7,.2,1) backwards}")
    b = [frame(W, H, c), text(22, 32, "Languages", c["ink"], 13, 700),
         text(W - 22, 32, "by bytes, public repos", c["muted"], 10.5, anchor="end"),
         '<defs><clipPath id="barclip"><rect x="22" y="48" width="436" height="12" rx="6"/></clipPath></defs>']
    x = 22.0
    segs = []
    for k, (name, v) in enumerate(shown):
        w = 436 * v / total
        col = LANG_COLOURS.get(name, c["muted"])
        segs.append(f'<rect class="seg" x="{x:.2f}" y="48" width="{w + 0.5:.2f}" height="12" fill="{col}" '
                    f'style="animation-delay:{k * .08:.2f}s"/>')
        x += w
    b.append(f'<rect x="22" y="48" width="436" height="12" rx="6" fill="{c["faint"]}"/><g clip-path="url(#barclip)">{"".join(segs)}</g>')
    for k, (name, v) in enumerate(shown):
        col = LANG_COLOURS.get(name, c["muted"])
        x, y = 22 + (k // 4) * 222, 92 + (k % 4) * 26
        b.append(f'<circle cx="{x + 5}" cy="{y - 4}" r="5" fill="{col}"/>')
        b.append(text(x + 18, y, name, c["ink"], 12))
        b.append(text(x + 200, y, f"{100 * v / total:.1f}%", c["muted"], 12, anchor="end"))
    label = "Languages by share of bytes: " + ", ".join(f"{n} {100 * v / total:.1f}%" for n, v in shown)
    write(f"langs-{mode}.svg", svg(W, H, "".join(b), label, css))


def city_card(d, mode):
    c = THEMES[mode]
    cal = d["cal"]
    days = cal["days"]
    weeks = [days[i:i + 7] for i in range(0, len(days), 7)]
    totals = [sum(x[1] for x in w) for w in weeks]
    W, H = 980, 300
    ground = 252
    most = max(totals) or 1
    rnd = random.Random(LOGIN + mode)
    dark = mode == "dark"
    sky = ("#0b0f1e", "#1e1446") if dark else ("#f5f3ff", "#fce7f3")
    css = ["@keyframes up{from{transform:scaleY(0)}}",
           ".bd{transform-box:fill-box;transform-origin:50% 100%;animation:up .9s cubic-bezier(.2,.8,.2,1) backwards}",
           "@keyframes tw{0%,100%{opacity:.15}50%{opacity:1}}.star{animation:tw 3s ease-in-out infinite}",
           "@keyframes fk{0%,100%{opacity:1}45%{opacity:1}50%{opacity:.15}55%{opacity:1}}.fk{animation:fk 5s steps(1) infinite}"]
    b = [f'<defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{sky[0]}"/>'
         f'<stop offset="1" stop-color="{sky[1]}"/></linearGradient>'
         f'<linearGradient id="beam" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="{c["violet"]}" stop-opacity=".45"/>'
         f'<stop offset="1" stop-color="{c["violet"]}" stop-opacity="0"/></linearGradient>'
         f'<clipPath id="win"><rect x="1" y="1" width="{W - 2}" height="{H - 2}" rx="11.5"/></clipPath></defs>',
         f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="url(#sky)" stroke="{c["border"]}"/>',
         '<g clip-path="url(#win)">']
    if dark:
        for _ in range(60):
            x, y = rnd.uniform(10, W - 10), rnd.uniform(44, 170)
            b.append(f'<circle class="star" cx="{x:.0f}" cy="{y:.0f}" r="{rnd.choice([.8, 1, 1.3]):.1f}" fill="#e9e5ff" '
                     f'style="animation-delay:{rnd.uniform(0, 3):.2f}s"/>')
        b.append('<circle cx="120" cy="84" r="20" fill="#f5f3ff"/><circle cx="130" cy="78" r="18" fill="#130f2e"/>')
    else:
        b.append(f'<circle cx="120" cy="86" r="28" fill="{c["pink"]}" fill-opacity=".18"/>'
                 f'<circle cx="120" cy="86" r="17" fill="{c["pink"]}" fill-opacity=".4"/>')
    tallest = max(range(len(totals)), key=lambda i: totals[i])
    n = len(totals)
    bw, gap = 14, (W - 80 - n * 14) / max(n - 1, 1)
    xs = [40 + i * (bw + gap) for i in range(n)]
    tx = xs[tallest] + bw / 2
    th = 12 + 170 * math.sqrt(totals[tallest] / most)
    b.append(f'<g><animateTransform attributeName="transform" type="rotate" values="-28 {tx:.0f} {ground - th:.0f};'
             f'28 {tx:.0f} {ground - th:.0f};-28 {tx:.0f} {ground - th:.0f}" dur="9s" repeatCount="indefinite"/>'
             f'<path d="M{tx:.0f} {ground - th:.0f}L{tx - 34:.0f} 0H{tx + 34:.0f}z" fill="url(#beam)"/></g>')
    lit_col = "#fde68a" if dark else "#ffffff"
    for i, t in enumerate(totals):
        x = xs[i]
        h = 6 if t == 0 else 12 + 170 * math.sqrt(t / most)
        level = 0 if t == 0 else min(4, 1 + int(3.999 * t / most))
        body = [f'<rect x="{x:.1f}" y="{ground - h:.1f}" width="{bw}" height="{h:.1f}" rx="1.5" fill="{c["ramp"][level] if t else c["faint"]}"/>']
        if t:
            if h > 60 and i % 3 == 0:
                body.append(f'<path d="M{x + bw / 2:.1f} {ground - h:.1f}v-9" stroke="{c["ramp"][level]}" stroke-width="1.5"/>'
                            f'<circle class="star" cx="{x + bw / 2:.1f}" cy="{ground - h - 9:.1f}" r="1.6" fill="{c["pink"]}" '
                            f'style="animation-delay:{rnd.uniform(0, 3):.2f}s"/>')
            p = 0.25 + 0.55 * t / most
            yy = ground - h + 6
            while yy < ground - 6:
                for col in (x + 3, x + 8):
                    if rnd.random() < p:
                        flick = (f' class="fk" style="animation-delay:{rnd.uniform(0, 5):.2f}s"'
                                 if rnd.random() < 0.08 else "")
                        body.append(f'<rect{flick} x="{col:.1f}" y="{yy:.1f}" width="3" height="3" fill="{lit_col}" '
                                    f'fill-opacity="{.9 if dark else .8}"/>')
                yy += 7
        b.append(f'<g class="bd" style="animation-delay:{i * 0.018:.3f}s">{"".join(body)}</g>')
    b.append(f'<rect x="0" y="{ground}" width="{W}" height="{H - ground}" fill="{c["bg"]}" fill-opacity="{.85 if dark else .6}"/>')
    b.append(f'<path d="M0 {ground}H{W}" stroke="{c["violet"]}" stroke-opacity=".6"/>')
    last_month = None
    for i, w in enumerate(weeks):
        m = w[0][0][:7]
        if m != last_month and i < n - 2:
            if last_month is not None:
                b.append(text(xs[i], ground + 22, dt.date.fromisoformat(w[0][0]).strftime("%b"), c["muted"], 10.5))
            last_month = m
    b.append('</g>')
    b.append(text(24, 34, "contribution city", c["ink"], 13, 700))
    b.append(text(170, 34, f"· one tower per week · {num(cal['total'])} contributions in the last year · "
                           f"busiest week {totals[tallest]}", c["muted"], 12))
    label = (f"Contribution city: one building per week for the last year, height by contributions. "
             f"{cal['total']} contributions in total, busiest week {totals[tallest]}.")
    write(f"city-{mode}.svg", svg(W, H, "".join(b), label, "".join(css)))


def footer_wave():
    W, H = 980, 130
    css = ["@keyframes wave{to{transform:translateX(-480px)}}", ".w{animation:wave linear infinite}"]
    defs, b = [], []
    for k, (col, op, y, amp, dur) in enumerate([("#a78bfa", .55, 74, 16, 11), ("#f472b6", .45, 84, 13, 15),
                                                  ("#22d3ee", .40, 94, 11, 8)]):
        defs.append(f'<linearGradient id="g{k}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{col}" '
                    f'stop-opacity="{op}"/><stop offset="1" stop-color="{col}" stop-opacity="0"/></linearGradient>')
        d = f"M0 {y}"
        for i in range(0, 13):
            x = i * 120
            d += f" Q{x + 30} {y - amp} {x + 60} {y} T{x + 120} {y}"
        d += f" V{H} H0z"
        b.append(f'<path class="w" d="{d}" fill="url(#g{k})" style="animation-duration:{dur}s"/>')
    b.insert(0, f'<defs>{"".join(defs)}</defs>')
    b.append(text(W / 2, 36, "thanks for stopping by · keep building, keep learning", "#8b949e", 13, anchor="middle"))
    write("footer.svg", svg(W, H, "".join(b), "Animated waves: thanks for stopping by", "".join(css)))


def snake_placeholder():
    for mode in THEMES:
        path = ASSETS / f"snake-{mode}.svg"
        if not path.exists():
            c = THEMES[mode]
            write(path.name, svg(880, 140, frame(880, 140, c) + text(440, 76, "the contribution snake appears after the "
                                                                    "first GitHub Action run", c["muted"], 13, anchor="middle"),
                                 "Contribution snake placeholder"))


# ================================================================ README sections

ICONS = {   # 24x24 line icons for skills that have no brand logo
    "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/>'
                '<path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    "arrows": '<path d="M4 8h15l-4-4M20 16H5l4 4"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 12.5l9 5 9-5"/><path d="M3 17l9 5 9-5"/>',
    "shield": '<path d="M12 2.5l8 3v6.5c0 5-3.5 8.3-8 9.5-4.5-1.2-8-4.5-8-9.5V5.5z"/><path d="M8.5 12l2.5 2.5 4.5-5"/>',
    "lock": '<rect x="4.5" y="10.5" width="15" height="11" rx="2"/><path d="M8 10.5V7a4 4 0 0 1 8 0v3.5"/>',
    "tree": '<circle cx="12" cy="4.5" r="2.5"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>'
            '<path d="M10.8 6.7L6.7 16.3M13.2 6.7l4.1 9.6"/>',
    "cube": '<path d="M12 2.5l8.5 4.75v9.5L12 21.5l-8.5-4.75v-9.5z"/><path d="M3.5 7.25L12 12l8.5-4.75M12 12v9.5"/>',
    "stack": '<rect x="3" y="3" width="18" height="4.5" rx="1.2"/><rect x="3" y="9.75" width="18" height="4.5" rx="1.2"/>'
             '<rect x="3" y="16.5" width="18" height="4.5" rx="1.2"/>',
    "code": '<path d="M8.5 6L2.5 12l6 6M15.5 6l6 6-6 6"/>',
}


def badge_url(name, logo, color):
    label = urllib.parse.quote(name.replace("-", "--").replace("_", "__"), safe="")
    url = f"https://img.shields.io/badge/{label}-161b22?style=for-the-badge"
    if logo.startswith("icon:"):
        icon = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#{color}" '
                f'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">{ICONS[logo[5:]]}</svg>')
        data = "data:image/svg+xml;base64," + b64encode(icon.encode()).decode()
        return url + "&logo=" + urllib.parse.quote(data, safe="")
    return url + f"&logo={logo}&logoColor={color}"


def skills_block():
    rows = []
    for group in CFG["skills"]:
        cells = "\n".join(f'<a href="{esc(it["url"])}"><img src="{esc(badge_url(it["name"], it["logo"], it["color"]))}" '
                          f'alt="{esc(it["name"])}" height="28"></a>' for it in group["items"])
        rows.append(f'<tr>\n<td><b>{escape(group["group"])}</b></td>\n<td>\n{cells}\n</td>\n</tr>')
    return "<table>\n" + "\n".join(rows) + "\n</table>"


def projects_block():
    # GitHub hides a link whose address ends in #gh-dark-mode-only / #gh-light-mode-only when the viewer's
    # theme does not match, so a linked card is two links, one per theme. Unlinked cards use <picture>.
    lines = ['<div align="center">']
    for p in CFG["projects"]:
        alt = esc(f"{p['title']}: {p['about']}")
        if p.get("url"):
            lines.append("".join(f'<a href="{esc(p["url"])}#gh-{m}-mode-only"><img src="assets/card-{p["key"]}-{m}.svg" '
                                 f'width="49%" alt="{alt}"></a>' for m in ("dark", "light")))
        else:
            lines.append(f'<picture><source media="(prefers-color-scheme: dark)" srcset="assets/card-{p["key"]}-dark.svg">'
                         f'<source media="(prefers-color-scheme: light)" srcset="assets/card-{p["key"]}-light.svg">'
                         f'<img src="assets/card-{p["key"]}-dark.svg" width="49%" alt="{alt}"></picture>')
    lines.append("</div>")
    legend = " &nbsp;·&nbsp; ".join(f"{dot} {word}" for dot, word in
                                    [("🟣", "team project"), ("🟡", "hackathon prototype"), ("🔵", "in progress"),
                                     ("🩷", "research"), ("🟢", "completed / ongoing")])
    lines.append(f'\n<p align="center"><sub>{legend}</sub></p>')
    return "\n".join(lines)


def update_readme():
    path = ROOT / "README.md"
    readme = original = path.read_text(encoding="utf-8")
    for name, block in (("skills", skills_block()), ("projects", projects_block())):
        start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
        if start in readme and end in readme:
            a, z = readme.index(start) + len(start), readme.index(end)
            readme = readme[:a] + "\n\n" + block + "\n\n" + readme[z:]
    if readme != original:
        path.write_text(readme, encoding="utf-8", newline="\n")
        print("README.md sections updated")


# ================================================================ main

def main():
    ASSETS.mkdir(exist_ok=True)
    d = load()
    hero(d)
    footer_wave()
    snake_placeholder()
    for mode in THEMES:
        project_cards(d, mode)
        leetcode_card(d, mode)
        bfs_card(mode)
        hackathon_card(mode)
        learning_card(mode)
        stats_card(d, mode)
        langs_card(d, mode)
        city_card(d, mode)
    update_readme()
    cal, lc = d["cal"], d["lc"]
    print(f"contributions={cal['total']} active={cal['active']} longest={cal['longest']} current={cal['current']} "
          f"leetcode={lc['solved_label']} live={lc['live']} repo_solutions={d['lc_repo_total']}")
    print("topics:", sorted(d["topics"].items(), key=lambda kv: -kv[1]))
    print("langs:", sorted(d["langs"].items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    main()
