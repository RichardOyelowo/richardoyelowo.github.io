# Richard Oyelowo — Portfolio Site

GitHub Pages portfolio. Static HTML/CSS/JS, no framework, no build step for the core site. Blog posts are the only thing that need a build step.

## File Structure

```
/
├── index.html                  # Homepage (source)
├── css/
│   ├── base.css                # Shared: design tokens, reset, nav, footer, animations
│   ├── homepage.css            # Homepage-specific styles
│   ├── post.css                # Individual blog post styles
│   └── listing.css             # Blog listing page styles
├── blog/
│   ├── template.html           # Post page template (source, DO NOT edit generated files)
│   ├── index.html              # Listing page (GENERATED — do not edit manually)
│   ├── posts.json              # Metadata for homepage blog cards (GENERATED)
│   ├── .build_hashes.json      # Hash tracking for incremental builds (GENERATED)
│   └── *.html                  # Individual post pages (GENERATED)
├── blog_markdown/
│   └── *.md                    # Blog post sources (edit these)
└── scripts/
    └── build_blog.py           # Build script
```

**Source files** (you edit these): `index.html`, `css/*.css`, `blog/template.html`, `blog_markdown/*.md`, `scripts/build_blog.py`

**Generated files** (the build script creates these): everything else in `blog/` except `template.html`

## How the Blog Pipeline Works

1. You write markdown in `blog_markdown/` with YAML frontmatter
2. You run the build script
3. The script generates HTML files in `blog/`, updates `posts.json`, and rebuilds `blog/index.html`
4. The homepage fetches `blog/posts.json` via JS to render blog cards dynamically

No build step needed for the homepage itself. Only blog changes require running the script.

## Adding a New Blog Post

1. Create a new `.md` file in `blog_markdown/`:

```markdown
---
title: Your Post Title Here
date: 2026-07
summary: A one or two sentence description that appears on the blog cards.
pinned: true
---

Your markdown content goes here.

## Subheadings work

Regular paragraphs. Code blocks, lists, tables, blockquotes — all supported.

`inline code` works too.
```

2. Run the build script:

```bash
python scripts/build_blog.py
```

3. Commit and push. Done.

## Frontmatter Fields

| Field     | Required | Description                                      |
|-----------|----------|--------------------------------------------------|
| `title`   | Yes      | Post title                                       |
| `date`    | Yes      | Display date (e.g. `2026-07`, `2026-07-22`)       |
| `summary` | Yes      | Short description for blog listing cards         |
| `pinned`  | No       | `true` to pin to top of listing (max 3 pinned)   |

## Pinned Posts

Add `pinned: true` to frontmatter. Maximum 3 posts can be pinned. Pinned posts appear first on both the listing page and the homepage blog section, with a "Pinned" badge. If more than 3 posts have `pinned: true`, only the first 3 are pinned.

## Deleting a Blog Post

Delete the `.md` file from `blog_markdown/` and run the build script. The script automatically removes the orphaned HTML file from `blog/`.

## Build Script Details

- **Hash-based skipping**: If a `.md` file hasn't changed since last build, its HTML is not regenerated. Saves time and avoids unnecessary file changes in git.
- **Orphan cleanup**: If you delete a `.md` file, the corresponding `.html` in `blog/` is automatically removed.
- **Dependency**: Requires the `markdown` Python package. Install with `pip install markdown`

## Making Style Changes

All styling lives in `css/`:

- `base.css` — Design tokens (colors, fonts, spacing), reset, nav bar, footer, animations, responsive breakpoints. Edit `--page-pad` here to change site-wide horizontal padding.
- `homepage.css` — Hero, about, OSS card, projects grid, blog grid, stack grid, contact grid, and all homepage responsive rules.
- `post.css` — Article layout and markdown content styling (headings, code blocks, tables, blockquotes).
- `listing.css` — Blog listing page grid and card styles.

The CSS uses custom properties defined in `base.css`. Change a color or spacing once and it propagates everywhere.

## Deploying

This is a GitHub Pages site. Push to the `main` branch (or whichever branch is configured for Pages) and GitHub serves it automatically. The generated blog files need to be committed alongside the source files since GitHub Pages serves static files directly.

## Quick Reference

# Add a new post
```bash
vim blog_markdown/my-new-post.md
python scripts/build_blog.py
git add .
git commit -m "Add my-new-post"
git push
```


# Update an existing post
```bash
vim blog_markdown/existing-post.md
python scripts/build_blog.py
git add .
git commit -m "Update existing-post"
git push
```

# Delete a post
```bash
rm blog_markdown/old-post.md
python scripts/build_blog.py
git add .
git commit -m "Remove old-post"
git push
```


# Tweak styles
```bash
vim css/homepage.css
git add .
git commit -m "Style update"
git push
```
