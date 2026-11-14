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


def select_topics(ai, queue, records, diagnostics=None):
    audit = diagnostics if diagnostics is not None else []
    audit.clear()
    lookup = {item["id"]: item for item in queue}
    # Short labels reduce identifier copying errors without changing stored IDs.
    aliases = {f"N{i}": item["id"] for i, item in enumerate(queue, 1)}
    candidates = []
    for alias, original in aliases.items():
        item = lookup[original]
        candidate = {k: item[k] for k in ("title", "summary", "source", "published")}
        candidate.update(id=alias, publisher_group=source_group(item["url"]))
        candidates.append(candidate)
    groups = sorted({c["publisher_group"] for c in candidates})
    audit.append(f"Wejście: {len(candidates)} wiadomości; grupy wydawców: {', '.join(groups)}.")
    response = ai.ask(SELECT, dict(candidates=candidates,
                      recent=[r["title"] for r in records[-80:]]), tokens=4096)
    topics = response.get("topics")
    if not isinstance(topics, list):
        raise Rejected("Wybór tematów: odpowiedź nie zawiera listy topics.")
    audit.append(f"Gemini zaproponowało grup: {len(topics)}.")
    if not topics:
        reason = response.get("reason")
        audit.append("Uzasadnienie Gemini: " + diagnostic_text(reason if isinstance(reason, str)
                     and reason.strip() else "Model nie podał uzasadnienia.", 1200))
    selected, seen_groups = [], set()
    # Validate all returned groups, then retain up to three valid groups.
    for index, topic in enumerate(topics[:20], 1):
        prefix = f"Grupa {index}: "
        if not isinstance(topic, dict):
            audit.append(prefix + "odrzucona — niepoprawny format grupy.")
            continue
        label = diagnostic_text(topic.get("label", ""), 180)
        prefix += label + ": " if label else ""
        ids = topic.get("ids")
        if not isinstance(ids, list) or not 2 <= len(ids) <= 5:
            audit.append(prefix + "odrzucona — wymagane 2–5 identyfikatorów.")
            continue
        resolved = [aliases.get(i.strip(), i.strip()) if isinstance(i, str) else None for i in ids]
        if any(i not in lookup for i in resolved):
            audit.append(prefix + "odrzucona — nieznane identyfikatory: " +
                         diagnostic_text(repr(ids), 300))
            continue
        if len(set(resolved)) != len(resolved):
            audit.append(prefix + "odrzucona — powtórzone identyfikatory.")
            continue
        items = [lookup[i] for i in resolved]
        publishers = {source_group(i["url"]) for i in items}
        if len(publishers) < 2:
            audit.append(prefix + "odrzucona — jedna grupa wydawnicza: " + ", ".join(publishers))
            continue
        if any(duplicate(i, records) for i in items):
            audit.append(prefix + "odrzucona — temat już opublikowany.")
            continue
        signature = tuple(sorted(resolved))
        if signature in seen_groups:
            audit.append(prefix + "pominięta — powtórzona grupa.")
            continue
        seen_groups.add(signature)
        selected.append(items)
        audit.append(prefix + "przyjęta; " + ", ".join(publishers))
        if len(selected) == 3:
            break
    for entry in audit:
        logging.info("Wybór tematów: %s", entry)
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
    logging.info("Wersja 2.10: maksymalnie jeden artykuł i pięć żądań API w cyklu.")
    selection_audit = []
    topics = select_topics(ai, queue, records, selection_audit)
    if not topics:
        raise Rejected("Nie wybrano tematu do opracowania.\n" + "\n".join(selection_audit))
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
