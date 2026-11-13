"""Version 2: select, research, dossier, draft, verify, publish."""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone

import yaml
from scraper import CONTENT, DATA, duplicate, history, save_json
from newsroom import (APIError, Rejected, Reader, Gemini, SELECT, EXTRACT, WRITE, VERIFY,
                      collect, check_dossier, check_draft, render_markdown, source_group,
                      prepare_article, review_result, diagnostic_text, article_policy)


def report(status, message, **extra):
    save_json(DATA / "run_report.json", dict(status=status, message=message,
               timestamp=datetime.now(timezone.utc).isoformat(), **extra))
    logging.info("%s: %s", status, message)
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write("\n## Sygnał Dnia — wynik generacji\n\nStatus: **" + status + "**\n\n")
            stream.write("~~~~text\n" + message.replace("~", "").replace("<", "&lt;").replace(">", "&gt;")[:10000] + "\n~~~~\n")


def select_topics(ai, queue, records):
    lookup = {item["id"]: item for item in queue}
    response = ai.ask(SELECT, dict(
        candidates=[{k: item[k] for k in ("id", "title", "summary", "source", "published")}
                    for item in queue],
        recent=[r["title"] for r in records[-80:]]), tokens=4096)
    topics = response.get("topics")
    if not isinstance(topics, list):
        raise Rejected("Niepoprawny wynik wyboru tematów.")
    selected = []
    for topic in topics[:3]:
        if not isinstance(topic, dict):
            continue
        ids = topic.get("ids")
        if (not isinstance(ids, list) or not 2 <= len(ids) <= 5
                or any(not isinstance(i, str) or i not in lookup for i in ids)
                or len(set(ids)) != len(ids)):
            continue
        items = [lookup[i] for i in ids]
        if len({source_group(i["url"]) for i in items}) < 2:
            continue
        if any(duplicate(i, records) for i in items):
            continue
        selected.append(items)
    return selected


def check_review(review):
    review_result(review)


def run():
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        raise APIError("Brak sekretu GEMINI_API_KEY.")
    queue = json.loads((DATA / "queue.json").read_text(encoding="utf-8"))
    records = history()
    queue = [item for item in queue if not duplicate(item, records)]
    if not queue:
        raise Rejected("Brak nowych tematów RSS.")
    ai = Gemini(key)
    logging.info("Wersja 2.5: maksymalnie jeden artykuł i pięć żądań API w cyklu.")
    topics = select_topics(ai, queue, records)
    if not topics:
        raise Rejected("Brak materiałów o jednym wydarzeniu od dwóch wydawców.")
    reader = Reader()
    diagnostics = []
    chosen, docs, dossier, facts, draft, length, used_sources = prepare_article(
        ai, reader, topics, diagnostics)
    policy = article_policy(facts, docs)
    CONTENT.mkdir(parents=True, exist_ok=True)
    source_records = [{k: item.get(k, "") for k in ("url", "title", "source", "published")}
                      for item in chosen]
    sources = [{k: doc.get(k, "") for k in ("id", "url", "title", "source", "published", "retrieved")}
               for doc in docs if doc["id"] in used_sources]
    meta = dict(
        title=draft["title"], summary=draft["summary"],
        date=datetime.now(timezone.utc).isoformat(), category=chosen[0]["category"],
        author="Redakcja AI", source=source_records[0], sources=sources,
        dedup_sources=source_records, word_count=length,
        editorial_check="Automatyczne porównanie ze źródłami; nie jest kontrolą człowieka.",
        model=ai.model, version=2, article_format=policy["mode"],
        fact_count=policy["fact_count"], source_word_count=policy["source_words"],
        source_mode=dossier.get("source_mode", "independent"), origin=dossier.get("origin"), risk_reason=dossier.get("risk_reason"),
        risk_categories=dossier.get("risk_categories"), primary_evidence=dossier.get("primary_evidence", []))
    document = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + "---\n\n"
    document += render_markdown(draft, facts, dossier)
    slug = hashlib.sha256(source_records[0]["url"].encode()).hexdigest()[:24]
    path = CONTENT / (slug + ".md")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(path)
    for source in source_records:
        if not any(r["url"] == source["url"] for r in records):
            records.append(source)
    save_json(DATA / "history.json", records)
    report("OPUBLIKOWANO", f'Utworzono tekst ({policy["mode"]}, {policy["fact_count"]} faktów): {length} słów, {len(sources)} źródeł, {ai.calls} żądania API.',
           article=path.name, words=length, sources=len(sources), calls=ai.calls, attempts=diagnostics)


def main():
    try:
        run()
    except APIError as exc:
        report("BŁĄD API", str(exc))
        raise SystemExit(1) from None
    except Rejected as exc:
        report("WSTRZYMANO", str(exc))
        print("::warning::Brak nowego artykułu: sprawdź podsumowanie Generate articles.")
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        report("BŁĄD DANYCH", "Niepoprawny format danych; publikacja wstrzymana: " + type(exc).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
