#!/usr/bin/env python3
"""OSYR — fills the web/ templates with what is published in releases/.

Usage: scripts/render-web.py RELEASES OUTPUT
Reads osyr.img.xz and osyr.ova with their .sha256 and .info (left there by
scripts/publish.sh) and writes to OUTPUT the PAGES (with canonical, Open Graph
and Twitter tags; see social_meta) and og.png.
The version is the v* tag of the published image's commit
(scripts/publish.sh img vX.Y.Z creates it).
If the .ova is still from a different version than the image (it is published
later), the page says so.
"""
import html
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = ["index.html", "about.html", "help.html", "changelog.html", "releases/index.html"]


def info(path):
    try:
        return dict(l.strip().split("=", 1) for l in open(path) if "=" in l)
    except OSError:
        return {}


def md_html(md):
    """help.md (headings, lists, tables, `code`) -> HTML; the same help as osyr-help."""
    def inline(t):
        return re.sub(r"`([^`]*)`", r"<code>\1</code>", html.escape(t, quote=False))
    out, rows, items = [], [], []

    def flush():
        if rows:
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            cells = [r for r in cells if not all(set(c) <= set("-: ") for c in r)]
            out.append("<table>" + "".join(
                "<tr>" + "".join(f"<{'th' if i == 0 else 'td'}>{inline(c)}</{'th' if i == 0 else 'td'}>" for c in r) + "</tr>"
                for i, r in enumerate(cells)) + "</table>")
            rows.clear()
        if items:
            out.append('<ul class="what">' + "".join(f"<li>{inline(x)}</li>" for x in items) + "</ul>")
            items.clear()

    for line in md.splitlines():
        if line.startswith("|"):
            rows.append(line)
            continue
        if line.startswith("- "):
            items.append(line[2:])
            continue
        flush()
        if line.startswith("# "):
            out.append(f"<h1>&gt; {inline(line[2:])}</h1>")
        elif line.startswith("## "):
            anchor = re.sub(r"[^a-z0-9]+", "-", line[3:].lower()).strip("-")
            out.append(f'<h2 id="{anchor}">{inline(line[3:])}</h2>')
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush()
    return "\n".join(out)


SITE = "https://ccmai.org/osyr/"


def social_meta(page, text):
    """Canonical + Open Graph + Twitter block for a page, taken from its <title> and its
    description (social networks do not show SVG: the image is og.png, 1200x630).
    Inserted before <link rel="icon">; if the template already has og:description, it is kept."""
    title = re.search(r"<title>(.*?)</title>", text).group(1)
    desc = re.search(r'<meta name="description" content="(.*?)">', text).group(1)
    url = SITE + re.sub(r"(^|/)index\.html$", r"\1", page)
    tags = [f'<link rel="canonical" href="{url}">',
            '<meta property="og:type" content="website">',
            '<meta property="og:site_name" content="CCMAI">',
            f'<meta property="og:title" content="{title.removesuffix(" · CCMAI")}">']
    if 'property="og:description"' not in text:
        tags.append(f'<meta property="og:description" content="{desc}">')
    tags += [f'<meta property="og:url" content="{url}">',
             f'<meta property="og:image" content="{SITE}og.png">',
             '<meta property="og:image:width" content="1200">',
             '<meta property="og:image:height" content="630">',
             '<meta property="og:image:alt" content="OSYR: an operating system with its own local AI">',
             '<meta name="twitter:card" content="summary_large_image">']
    icon = text.index('<link rel="icon"')
    return text[:icon] + "\n".join(tags) + "\n" + text[icon:]


def human(n):
    return f"{n / 1e9:.1f} GB"


def main():
    rel, out = sys.argv[1], sys.argv[2]
    img_info, ova_info = info(f"{rel}/osyr.img.xz.info"), info(f"{rel}/osyr.ova.info")
    version = subprocess.run(["git", "-C", ROOT, "describe", "--tags", "--abbrev=0", "--match", "v*",
                              img_info.get("COMMIT", "HEAD")],
                             capture_output=True, text=True).stdout.strip() or "dev"
    commit = img_info.get("COMMIT", "unknown")[:7]
    values = {
        "@VERSION@": version,
        "@COMMIT@": commit,
        "@DATE@": img_info.get("DATE", "")[:10],
    }
    for key, name in (("IMG", "osyr.img.xz"), ("OVA", "osyr.ova")):
        path = f"{rel}/{name}"
        size = os.path.getsize(path) if os.path.exists(path) else 0
        sha = open(f"{path}.sha256").read().split()[0] if os.path.exists(f"{path}.sha256") else "not available yet"
        values[f"@{key}_SIZE@"] = human(size) if size else "not available yet"
        values[f"@{key}_SHA@"] = sha
    if ova_info.get("COMMIT", "")[:7] != commit:
        values["@OVA_SIZE@"] += " · previous version, the new one is on its way"
    values["@HELP_BODY@"] = md_html(open(os.path.join(ROOT, "board/osyr/rootfs-overlay/usr/share/osyr/help.md")).read())
    # size on the USB stick: that of the uncompressed image (xz keeps it in its index)
    p = subprocess.run(["xz", "--robot", "--list", f"{rel}/osyr.img.xz"], capture_output=True, text=True)
    m = re.search(r"^totals\t\d+\t\d+\t\d+\t(\d+)", p.stdout, re.M)
    values["@IMG_FULL@"] = human(int(m.group(1))) if m else "?"

    for page in PAGES:
        text = open(os.path.join(ROOT, "web", page)).read()
        for k, v in values.items():
            text = text.replace(k, v)
        left = re.findall(r"@[A-Z_]+@", text)
        if left:
            sys.exit(f"{page}: unfilled {left}")
        text = social_meta(page, text)
        dst = os.path.join(out, page)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w") as f:
            f.write(text)
    shutil.copy(os.path.join(ROOT, "web", "og.png"), os.path.join(out, "og.png"))
    print(f"web {version} (image {commit}) in {out}")


if __name__ == "__main__":
    main()
