#!/usr/bin/env python3
"""
Blog build script for richardoyelowo.github.io

Reads .md files from blog_markdown/, generates:
  - Individual HTML pages in blog/{slug}.html
  - blog/posts.json (metadata for homepage JS)
  - blog/index.html (standalone blog listing page)

Features:
  - Pinned posts (max 3) sorted first
  - SHA256 hash-based update-if-changed (skips rewrites)
  - Orphaned HTML cleanup when MD files are deleted
  - External CSS references (no inline styles)

Usage:
    python scripts/build_blog.py

Requirements:
    pip install markdown
"""

import os
import sys
import json
import hashlib
from pathlib import Path

try:
    import markdown
except ImportError:
    print("Error: 'markdown' package required.")
    print("Install with:  pip install markdown")
    sys.exit(1)

# Paths
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BLOG_MD_DIR = REPO_ROOT / "blog_markdown"
BLOG_DIR = REPO_ROOT / "blog"
TEMPLATE_FILE = BLOG_DIR / "template.html"
POSTS_JSON = BLOG_DIR / "posts.json"
INDEX_FILE = BLOG_DIR / "index.html"
HASHES_FILE = BLOG_DIR / ".build_hashes.json"

# Listing page template — uses external CSS
LISTING_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Blog &bull; Richard Oyelowo</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>&#x2699;</text></svg>">
  <link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&amp;family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,600;0,9..144,700;1,9..144,300&amp;display=swap" rel="stylesheet">
  <link rel="stylesheet" href="../css/base.css">
  <link rel="stylesheet" href="../css/listing.css">
</head>
<body>

<nav class="sub-nav">
  <a href="../" class="nav-logo">Richard Oyelowo</a>
  <a href="../#blog" class="nav-back">&larr; Portfolio</a>
</nav>

<main>
  <div class="blog-header">
    <div class="container">
      <p class="section-label">Blog</p>
      <h1>All Posts</h1>
      <p>Notes on backend engineering, system design, and what I learn building production software.</p>
    </div>
  </div>
  <div class="container" style="margin-top:32px;">
    <div class="posts-grid">
{{POST_CARDS}}
    </div>
  </div>
</main>

<footer role="contentinfo">
  <div class="footer-inner">
    <span class="footer-text">&copy; 2026 Richard Oyelowo</span>
    <span class="footer-text">Built by Richard for the love of development</span>
  </div>
</footer>

</body>
</html>"""


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_hashes():
    if HASHES_FILE.exists():
        return json.loads(HASHES_FILE.read_text(encoding="utf-8"))
    return {}


def save_hashes(hashes):
    HASHES_FILE.write_text(json.dumps(hashes, indent=2), encoding="utf-8")


def parse_frontmatter(content):
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    meta = {}
    for line in parts[1].strip().split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                meta[key] = value
    return meta, parts[2].strip()


def convert_md_to_html(body_md):
    return markdown.markdown(
        body_md,
        extensions=["fenced_code", "tables", "codehilite"],
        extension_configs={
            "codehilite": {"css_class": "highlight", "guess_lang": False}
        },
    )


def generate_post_html(template, meta, body_html, slug):
    html = template
    html = html.replace("{{TITLE}}", meta.get("title", "Untitled"))
    html = html.replace("{{DATE}}", meta.get("date", ""))
    html = html.replace("{{SUMMARY}}", meta.get("summary", ""))
    html = html.replace("{{CONTENT}}", body_html)
    html = html.replace("{{SLUG}}", slug)
    return html


def generate_listing_card(post):
    pinned_html = ""
    if post.get("pinned"):
        pinned_html = '<span class="pinned-badge">Pinned</span>'

    return (
        '      <a href="' + post["slug"] + '.html" class="post-card"'
        + ' aria-label="Read: ' + post["title"] + '">'
        + '\n        <span class="post-date">' + post["date"] + pinned_html + '</span>'
        + '\n        <h3 class="post-title">' + post["title"] + '</h3>'
        + '\n        <p class="post-excerpt">' + post["summary"] + '</p>'
        + '\n        <span class="post-read">Read &rarr;</span>'
        + '\n      </a>\n'
    )


def main():
    print("Building blog...")
    print(f"  Repo root:  {REPO_ROOT}")
    print(f"  MD source:  {BLOG_MD_DIR}")
    print(f"  Output dir: {BLOG_DIR}")

    # Verify template
    if not TEMPLATE_FILE.exists():
        print(f"  Error: template not found at {TEMPLATE_FILE}")
        sys.exit(1)

    # Ensure blog dir exists
    BLOG_DIR.mkdir(parents=True, exist_ok=True)

    # Find MD files
    md_files = sorted(BLOG_MD_DIR.glob("*.md"))
    if not md_files:
        print("  No .md files found in blog_markdown/ \u2014 nothing to build.")
        return

    # Read template
    template = TEMPLATE_FILE.read_text(encoding="utf-8")

    # Load previous hashes
    hashes = load_hashes()
    new_hashes = {}
    generated_slugs = set()

    # Process each post
    posts_meta = []
    for md_file in md_files:
        slug = md_file.stem
        content = md_file.read_text(encoding="utf-8")
        content_hash = sha256(content)
        new_hashes[slug] = content_hash

        meta, body = parse_frontmatter(content)
        if not meta.get("title"):
            print(f"  Skipping {slug}.md \u2014 no title in frontmatter")
            continue

        # Skip rewrite if content unchanged
        output_file = BLOG_DIR / f"{slug}.html"
        if output_file.exists() and hashes.get(slug) == content_hash:
            print(f"  Unchanged: {slug}.html (skipped)")
            posts_meta.append({
                "slug": slug,
                "title": meta.get("title", "Untitled"),
                "date": meta.get("date", ""),
                "summary": meta.get("summary", ""),
                "pinned": meta.get("pinned", "").lower() in ("true", "yes", "1"),
                "url": f"./{slug}.html",
            })
            generated_slugs.add(slug)
            continue

        # Generate
        body_html = convert_md_to_html(body)
        post_html = generate_post_html(template, meta, body_html, slug)
        output_file.write_text(post_html, encoding="utf-8")
        print(f"  Generated: {slug}.html")

        posts_meta.append({
            "slug": slug,
            "title": meta.get("title", "Untitled"),
            "date": meta.get("date", ""),
            "summary": meta.get("summary", ""),
            "pinned": meta.get("pinned", "").lower() in ("true", "yes", "1"),
            "url": f"./{slug}.html",
        })
        generated_slugs.add(slug)

    # Sort: pinned first (max 3), then by date descending
    pinned_posts = [p for p in posts_meta if p.get("pinned")][:3]
    unpinned_posts = [p for p in posts_meta if not p.get("pinned")]
    unpinned_posts.sort(key=lambda p: p.get("date", ""), reverse=True)
    sorted_posts = pinned_posts + unpinned_posts

    # Write posts.json
    json_content = json.dumps(sorted_posts, indent=2, ensure_ascii=False)
    POSTS_JSON.write_text(json_content, encoding="utf-8")
    print(f"  Generated: posts.json ({len(sorted_posts)} posts)")

    # Generate listing page
    cards_html = ""
    for post in sorted_posts:
        cards_html += generate_listing_card(post)
    listing_html = LISTING_TEMPLATE.replace("{{POST_CARDS}}", cards_html)
    INDEX_FILE.write_text(listing_html, encoding="utf-8")
    print(f"  Generated: index.html (listing page)")

    # Clean up orphaned HTML files
    for html_file in sorted(BLOG_DIR.glob("*.html")):
        if html_file.name in ("index.html", "template.html"):
            continue
        slug = html_file.stem
        if slug not in generated_slugs:
            html_file.unlink()
            print(f"  Removed orphan: {html_file.name}")

    # Save hashes
    save_hashes(new_hashes)

    print(f"\nDone! {len(sorted_posts)} post(s) built.")
    print(f"Workflow:")
    print(f"  1. Add a new .md file to blog_markdown/")
    print(f"  2. Run: python scripts/build_blog.py")
    print(f"  3. Commit and push")


if __name__ == "__main__":
    main()