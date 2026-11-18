"""One-shot Gemini end-to-end smoke test. No article, history or site publishing.

The RSS scraper may write
queue.json and history.json inside the ephemeral Actions checkout; no changes
are committed or pushed.
"""

import json
import logging
import os
import sys
from pathlib import Path

# Ensure project modules are importable when launched as tools/gemini_dry_run.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from generator import select_topics
from newsroom import APIError, Gemini, Reader, Rejected, prepare_article
from scraper import DATA, duplicate, fetch, history


def main():
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("STOP: GEMINI_API_KEY nie jest dostepny dla tej pracy GitHub Actions.")
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    # All scraper writes happen only in this ephemeral runner checkout.
    fetch()
    queue = json.loads((DATA / "queue.json").read_text(encoding="utf-8"))
    records = history()
    queue = [item for item in queue if not duplicate(item, records)]
    print(f"DRY RUN: kandydaci RSS: {len(queue)}")
    if not queue:
        print("WSTRZYMANO: brak nowych wiadomosci. Nic nie opublikowano.")
        return 2

    ai = Gemini(key)
    try:
        topics = select_topics(ai, queue, records, [])
        if not topics:
            print("WSTRZYMANO: Gemini nie wybralo poprawnej grupy tematow.")
            return 2
        diagnostics = []
        chosen, docs, dossier, facts, draft, length, used_sources = prepare_article(
            ai, Reader(), topics, diagnostics
        )
        print(
            "ZATWIERDZONO BEZ PUBLIKACJI: "
            f"fakty={len(facts)}, slowa={length}, "
            f"zrodla={len(used_sources)}, tryb={dossier.get('source_mode', 'brak')}"
        )
        return 0
    except (Rejected, APIError) as exc:
        # Expected security rejection is a useful finding, but should be visible.
        print(f"WSTRZYMANO: {type(exc).__name__}: {exc}")
        print("Nie uruchomiono generator.run, build.py ani wdrozenia.")
        return 2
    finally:
        print(f"Zuzyte wywolania Gemini: {ai.calls}/5")


if __name__ == "__main__":
    sys.exit(main())
