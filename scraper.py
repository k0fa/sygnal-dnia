"""Fetch RSS only; history records successfully generated articles."""
import calendar
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
CONTENT = ROOT / "content"
FEEDS = [
    ("Onet", "Wiadomości", "https://wiadomosci.onet.pl/.feed"),
    ("WP", "Wiadomości", "https://wiadomosci.wp.pl/rss.xml"),
    ("Interia", "Wiadomości", "https://wydarzenia.interia.pl/feed"),
    ("Interia Biznes", "Biznes", "https://biznes.interia.pl/feed"),
    ("GeekWeek", "Technologia", "https://geekweek.interia.pl/feed"),
]
IMPORTANT = ("wybor", "rząd", "wojn", "inflac", "energ", "ustaw", "nato", "bezpieczeń", "technolog")
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def plain(value):
    return " ".join(BeautifulSoup(str(value), "html.parser").get_text(" ").split())


def canonical(url):
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("Invalid source URL")
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", urlencode(sorted(query)), ""))


def normalized(title):
    return " ".join(re.findall(r"\w+", title.casefold()))


def duplicate(item, records):
    title = normalized(item["title"])
    for old in records:
        if item["url"] == old["url"]:
            return True
        if SequenceMatcher(None, title, normalized(old["title"])).ratio() >= 0.86:
            return True
    return False


def read_article(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(f"Missing frontmatter: {path}")
    header, body = text[4:].split("\n---\n", 1)
    meta = yaml.safe_load(header)
    if not isinstance(meta, dict):
        raise ValueError(f"Invalid frontmatter: {path}")
    return meta, body


def history():
    path = DATA / "history.json"
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    # Recover a publication if a process stopped after writing Markdown.
    for article in CONTENT.glob("*.md"):
        meta, _ = read_article(article)
        for record in meta.get("dedup_sources", [meta["source"]]):
            if not any(r["url"] == record["url"] for r in records):
                records.append(record)
    return records


def fetch():
    old = history()
    now = datetime.now(timezone.utc)
    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  respect_retry_after_header=False)
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "SygnalDniaRSS/1.0"
    candidates = []
    healthy = 0
    for source, category, url in FEEDS:
        try:
            response = session.get(url, timeout=(10, 30))
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if not feed.entries:
                raise ValueError("Empty or invalid RSS")
            healthy += 1
            for entry in feed.entries[:80]:
                try:
                    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
                    if not stamp:
                        continue  # Do not invent publication dates.
                    published = datetime.fromtimestamp(calendar.timegm(stamp), timezone.utc)
                    if published < now - timedelta(hours=48) or published > now + timedelta(hours=1):
                        continue
                    title = plain(entry.get("title", ""))[:300]
                    summary = plain(entry.get("summary", ""))[:800]
                    if len(title) < 15 or len(summary) < 40:
                        continue
                    link = canonical(entry.get("link", ""))
                    item = dict(id=hashlib.sha256(link.encode()).hexdigest()[:24],
                                title=title, summary=summary, url=link, source=source,
                                category=category, published=published.isoformat())
                    age = max(0, (now - published).total_seconds() / 3600)
                    item["score"] = sum(3 for word in IMPORTANT if word in title.casefold()) + max(0, 48-age)/12
                    if not duplicate(item, old):
                        candidates.append(item)
                except (ValueError, TypeError, OverflowError):
                    logging.warning("Skipped malformed entry in %s", source)
        except (requests.RequestException, ValueError):
            logging.warning("Unavailable feed: %s", source)
    selected = []
    per_feed = {}
    for item in sorted(candidates, key=lambda x: (-x["score"], x["id"])):
        if not any(i["url"] == item["url"] for i in selected) and per_feed.get(item["source"], 0) < 15:
            selected.append(item)
            per_feed[item["source"]] = per_feed.get(item["source"], 0) + 1
    save_json(DATA / "queue.json", selected[:75])
    save_json(DATA / "history.json", old)
    logging.info("Healthy feeds: %s/%s; queued: %s", healthy, len(FEEDS), min(75, len(selected)))
    if not healthy:
        raise SystemExit("All RSS feeds failed; existing publications are preserved.")


if __name__ == "__main__":
    fetch()
