"""Build portable static HTML; relative links support GitHub project Pages."""
import hashlib
import html
import shutil
from datetime import datetime

import bleach
import markdown

from scraper import CONTENT, ROOT, canonical, read_article

OUT = ROOT / "site"
NAME = "Sygnał Dnia"


def esc(value):
    return html.escape(str(value), quote=True)


def category_id(category):
    return "cat-" + hashlib.sha256(category.encode()).hexdigest()[:12]


def page(title, description, body, home="./index.html"):
    return f'''<!doctype html>
<html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{esc(description)}">
<title>{esc(title)} — {NAME}</title>
<script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
<style>body{{font:18px/1.7 system-ui,sans-serif;margin:0;background:#f8fafc;color:#0f172a}}
main,header,footer{{max-width:1100px;margin:auto;padding:24px}}
a{{color:inherit}}.article p{{margin:1em 0}}.article h2{{font-size:1.5em;font-weight:700}}
.article ul{{list-style:disc;padding-left:1.5em}}</style></head>
<body><header class="border-b border-slate-200 flex items-center justify-between gap-4">
<a class="font-black text-3xl tracking-tight no-underline" href="{home}">{NAME}<span class="text-red-600">.</span></a>
<span class="text-base text-slate-600">Polska · Świat · Technologia</span></header>
<main>{body}</main><footer class="border-t text-base text-slate-600">
Materiały opracowuje AI na podstawie opisów RSS. Nie są niezależną weryfikacją informacji.
Źródło i data oryginalnej publikacji znajdują się przy każdym artykule.</footer></body></html>'''


def main():
    # OUT is fixed under this project's directory; content is never deleted.
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "articles").mkdir(parents=True)
    items = []
    for path in CONTENT.glob("*.md"):
        meta, body = read_article(path)
        meta["slug"] = path.stem
        datetime.fromisoformat(meta["date"])
        items.append((meta, body))
    items.sort(key=lambda x: datetime.fromisoformat(x[0]["date"]), reverse=True)
    categories = sorted({m["category"] for m, _ in items})
    nav = " ".join(f'<a class="rounded-full border px-4 py-2" href="#{category_id(c)}">{esc(c)}</a>' for c in categories)
    index = f'<h1 class="text-4xl font-black my-6">Wiadomości w skrócie</h1><nav class="flex flex-wrap gap-3 mb-10" aria-label="Kategorie">{nav}</nav>'
    if not items:
        index += '<p>Pierwsze wiadomości pojawią się po udanej generacji.</p>'
    for category in categories:
        index += f'<section id="{category_id(category)}"><h2 class="text-2xl font-bold mb-5">{esc(category)}</h2><div class="grid md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">'
        for meta, _ in items:
            if meta["category"] != category:
                continue
            index += f'''<article class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
<time class="text-base text-slate-500" datetime="{esc(meta['date'])}">{esc(meta['date'][:10])}</time>
<h3 class="text-xl font-bold my-3"><a href="articles/{esc(meta['slug'])}.html">{esc(meta['title'])}</a></h3>
<p class="text-slate-600">{esc(meta['summary'])}</p></article>'''
        index += '</div></section>'
    (OUT / "index.html").write_text(page("Aktualności", "Przegląd wiadomości z podaniem źródeł.", index), encoding="utf-8")
    for meta, body in items:
        safe_body = bleach.clean(markdown.markdown(body), tags=["p", "h2", "h3", "strong", "em", "ul", "ol", "li", "blockquote", "br"], attributes={}, strip=True)
        source = meta["source"]
        url = canonical(source["url"])
        article = f'''<article class="max-w-3xl mx-auto">
<a href="../index.html" class="text-red-700">← Wszystkie wiadomości</a>
<p class="text-red-700 font-bold mt-8">{esc(meta['category'])}</p>
<h1 class="text-3xl md:text-5xl font-black my-5 leading-tight">{esc(meta['title'])}</h1>
<p class="text-base text-slate-500">{esc(meta['author'])} · <time datetime="{esc(meta['date'])}">{esc(meta['date'])}</time></p>
<p class="text-xl font-medium my-8">{esc(meta['summary'])}</p>
<div class="article">{safe_body}</div>
<aside class="mt-10 p-6 rounded-xl bg-slate-100"><strong>Źródło informacji:</strong>
<a class="underline" href="{esc(url)}" rel="noopener noreferrer">{esc(source['source'])} — {esc(source['title'])}</a>
<p class="text-base">Data publikacji źródła: {esc(source['published'])}</p></aside></article>'''
        (OUT / "articles" / (meta["slug"] + ".html")).write_text(page(meta["title"], meta["summary"], article, "../index.html"), encoding="utf-8")
    (OUT / ".nojekyll").touch()
    print(f"Built {len(items)} articles in {OUT}")


if __name__ == "__main__":
    main()
