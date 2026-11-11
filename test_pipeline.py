"""Offline regression tests. Integration tests require requirements.txt."""
import copy
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError

import newsroom as n

DEPS = all(importlib.util.find_spec(m) for m in ("yaml", "requests", "feedparser", "bs4", "markdown", "bleach"))


def fixture():
    docs = [
        dict(id="S1", url="https://wiadomosci.onet.pl/test", group="onet.pl", official=False,
             title="Pierwsze źródło", source="Onet", published="2026-11-10T10:00:00+00:00",
             text="Dokument źródłowy opisuje przebieg prac oraz potwierdzone wyniki spotkania."),
        dict(id="S2", url="https://wiadomosci.wp.pl/test", group="wp.pl", official=False,
             title="Drugie źródło", source="WP", published="2026-11-10T10:00:00+00:00",
             text="Druga relacja przedstawia szczegóły wydarzenia i ograniczenia dostępnych danych.")]
    facts = [dict(id=f"F{i+1}", claim=f"Fakt testowy numer {i+1}",
                  evidence=[dict(source=docs[i % 2]["id"], quote=docs[i % 2]["text"])])
             for i in range(12)]
    dossier = dict(ready=True, independent=True, high_risk=False, official_relevant=False, facts=facts)
    sections = []
    for j in range(3):
        paragraphs = []
        for k in range(3):
            # Synthetic text tests structure and limits, never published.
            prefix = ["Pierwszy", "Drugi", "Trzeci"][j] + " " + ["początek", "środek", "koniec"][k]
            paragraphs.append(dict(text=prefix + " " + " ".join(["analiza"]*70),
                                   facts=["F1", "F2"]))
        sections.append(dict(heading="Opis wydarzenia", facts=["F1"], paragraphs=paragraphs))
    draft = dict(publish=True, title="Testowy artykuł opisujący wydarzenie",
                 title_facts=["F1"], summary="Podsumowanie testowego wydarzenia z odwołaniem do obu dostępnych relacji.",
                 summary_facts=["F1", "F2"], sections=sections)
    return docs, dossier, draft


class EditorialTests(unittest.TestCase):
    def test_full_structural_acceptance_and_citations(self):
        docs, dossier, draft = fixture()
        facts = n.check_dossier(dossier, docs)
        length, sources = n.check_draft(draft, facts, docs)
        self.assertEqual(length, 648)
        self.assertEqual(sources, {"S1", "S2"})
        text = n.render_markdown(draft, facts)
        self.assertIn("[S2](#source-S2)", text)

    def test_fabricated_evidence_is_rejected(self):
        docs, dossier, _ = fixture()
        dossier["facts"][0]["evidence"][0]["quote"] = "Ten nieistniejący fragment model wymyślił bez podstaw."
        with self.assertRaisesRegex(n.Rejected, "nie istnieje"):
            n.check_dossier(dossier, docs)

    def test_high_risk_requires_primary_evidence(self):
        docs, dossier, _ = fixture()
        dossier.update(high_risk=True, official_relevant=True)
        with self.assertRaises(n.Rejected):
            n.check_dossier(dossier, docs)

    def test_same_publisher_is_not_two_sources(self):
        docs, dossier, _ = fixture()
        docs[1]["group"] = docs[0]["group"]
        with self.assertRaises(n.Rejected):
            n.check_dossier(dossier, docs)
        self.assertEqual(n.source_group("https://geekweek.interia.pl/a"),
                         n.source_group("https://biznes.interia.pl/b"))

    def test_unknown_fact_reference_is_rejected(self):
        docs, dossier, draft = fixture()
        draft["sections"][0]["paragraphs"][0]["facts"] = ["F999"]
        with self.assertRaises(n.Rejected):
            n.check_draft(draft, n.check_dossier(dossier, docs), docs)

    def test_invented_number_is_rejected(self):
        docs, dossier, draft = fixture()
        draft["title"] += " 99999"
        with self.assertRaisesRegex(n.Rejected, "Liczba"):
            n.check_draft(draft, n.check_dossier(dossier, docs), docs)

    def test_short_article_is_rejected(self):
        docs, dossier, draft = fixture()
        for section in draft["sections"]:
            section["paragraphs"] = section["paragraphs"][:1]
        with self.assertRaisesRegex(n.Rejected, "słów"):
            n.check_draft(draft, n.check_dossier(dossier, docs), docs)

    def test_source_copy_is_rejected(self):
        docs, dossier, draft = fixture()
        quote = "Jeden bardzo długi fragment pochodzący ze źródła nie powinien zostać skopiowany bezpośrednio do publikacji."
        docs[0]["text"] += " " + quote
        draft["sections"][0]["paragraphs"][0]["text"] += " " + quote
        with self.assertRaisesRegex(n.Rejected, "sekwencję"):
            n.check_draft(draft, n.check_dossier(dossier, docs), docs)

    def test_untrusted_html_is_rejected(self):
        docs, dossier, draft = fixture()
        draft["title"] += "<script>"
        with self.assertRaises(n.Rejected):
            n.check_draft(draft, n.check_dossier(dossier, docs), docs)

    def test_article_extractor_excludes_navigation(self):
        parser = n.ArticleParser()
        paragraph = "Treść prawdziwego artykułu zawiera szczegóły opisywanego wydarzenia. " * 40
        parser.feed("<nav><p>" + "Nawigacja " * 70 + "</p></nav><article><p>" +
                    paragraph + "</p><script>złośliwy kod</script></article>")
        self.assertNotIn("Nawigacja", parser.text())
        self.assertNotIn("złośliwy", parser.text())
        self.assertIn("prawdziwego", parser.text())

    def test_urls_and_private_addresses(self):
        for url in ("http://onet.pl/a", "https://onet.pl.evil.com/a", "https://localhost/a",
                    "https://user:pass@onet.pl/a", "https://onet.pl:8080/a"):
            with self.assertRaises(n.Rejected):
                n.checked_url(url, dns=False)
        with patch.object(n.socket, "getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))]):
            with self.assertRaises(n.Rejected):
                n.checked_url("https://onet.pl/a")

    def test_api_errors_do_not_expose_key(self):
        client = n.Gemini("SECRET_TEST_KEY")
        client.opener.open = MagicMock(side_effect=HTTPError("test", 404, "missing", {}, io.BytesIO()))
        with self.assertRaises(n.APIError) as context:
            client.ask("test", {})
        self.assertIn("GEMINI_MODEL", str(context.exception))
        self.assertNotIn("SECRET_TEST_KEY", str(context.exception))

    def test_no_api_call_after_limit(self):
        client = n.Gemini("unused")
        client.calls = 5
        client.opener.open = MagicMock()
        with self.assertRaises(n.APIError):
            client.ask("test", {})
        client.opener.open.assert_not_called()

    def test_robots_disallow(self):
        reader = n.Reader()
        reader.raw = MagicMock(return_value=("User-agent: *\nDisallow: /\n", "text/plain"))
        self.assertFalse(reader.allowed("https://onet.pl/a"))


@unittest.skipUnless(DEPS, "Integration requires requirements.txt")
class IntegrationTests(unittest.TestCase):
    def test_publish_history_and_build(self):
        import generator as g
        import scraper as s
        import build as b
        docs, dossier, draft = fixture()
        items = [dict(id=f"item{i}", title=d["title"], summary=d["text"], url=d["url"],
                      source=d["source"], published=d["published"], category="Wiadomości")
                 for i, d in enumerate(docs)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, content = root / "data", root / "content"
            s.save_json(data / "queue.json", items)
            with patch.object(g, "DATA", data), patch.object(g, "CONTENT", content), \
                 patch.object(s, "DATA", data), patch.object(s, "CONTENT", content), \
                 patch.object(b, "CONTENT", content), patch.object(b, "OUT", root / "site"), \
                 patch.dict(os.environ, {"GEMINI_API_KEY": "mock", "GITHUB_STEP_SUMMARY": ""}), \
                 patch.object(n, "collect", return_value=docs), \
                 patch.object(n.Gemini, "ask", side_effect=[
                     {"topics": [{"ids": ["item0", "item1"]}]}, dossier, draft,
                     {"approved": True, "issues": []}]) as api:
                g.main()
                self.assertEqual(len(list(content.glob("*.md"))), 1)
                self.assertEqual(api.call_count, 4)
                (data / "history.json").unlink()
                recovered = s.history()
                self.assertEqual(len(recovered), 2)
                g.main()
                self.assertEqual(api.call_count, 4)
                b.main()
                article = next((root / "site/articles").glob("*.html")).read_text(encoding="utf-8")
                self.assertIn('href="#source-S2"', article)
                self.assertIn('id="source-S2"', article)
                self.assertIn(docs[1]["url"], article)

    def test_api_failure_marks_run_failed(self):
        import generator as g
        with patch.object(g, "run", side_effect=n.APIError("HTTP 429")), patch.object(g, "report") as report:
            with self.assertRaises(SystemExit) as exit:
                g.main()
            self.assertEqual(exit.exception.code, 1)
            self.assertEqual(report.call_args.args[0], "BŁĄD API")

    def test_review_rejection_does_not_publish(self):
        import generator as g
        with self.assertRaises(n.Rejected):
            g.check_review({"approved": True, "issues": ["Brak dowodu"]})
        with self.assertRaises(n.Rejected):
            g.check_review({"approved": False, "issues": []})



class RetryTests(unittest.TestCase):
    def client(self, responses):
        class Fake:
            calls = 1  # Selection already consumed one request.
            model = "test"
            def ask(self, *args, **kwargs):
                if self.calls >= 5:
                    raise AssertionError("Exceeded budget")
                self.calls += 1
                result = responses.pop(0)
                if isinstance(result, Exception):
                    raise result
                return result
        return Fake()

    def test_rejected_dossier_tries_next_topic(self):
        docs, dossier, draft = fixture()
        ai = self.client([
            dict(ready=False, independent=False, reason="Obie relacje powielają depeszę."),
            dossier, draft, dict(approved=True, issues=[])])
        attempts = []
        with patch.object(n, "collect", return_value=docs):
            result = n.prepare_article(ai, None, [[dict(title="Pierwszy")],
                                                   [dict(title="Drugi")]], attempts)
        self.assertEqual(result[0][0]["title"], "Drugi")
        self.assertEqual(ai.calls, 5)
        self.assertIn("powielają depeszę", attempts[0]["reason"])
        self.assertEqual(attempts[1]["status"], "ZATWIERDZONO")
        self.assertEqual(attempts[0]["sources"][0]["url"], docs[0]["url"])

    def test_high_risk_cannot_skip_extra_review_after_retry(self):
        docs, dossier, draft = fixture()
        docs[0]["official"] = True
        dossier.update(high_risk=True, official_relevant=True)
        ai = self.client([dict(ready=False, reason="Brak faktów"), dossier])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "wysokiego ryzyka"):
                n.prepare_article(ai, None, [[dict(title="A")], [dict(title="B")]], [])
        self.assertEqual(ai.calls, 3)

    def test_high_risk_receives_both_reviews(self):
        docs, dossier, draft = fixture()
        docs[0]["official"] = True
        dossier.update(high_risk=True, official_relevant=True)
        ai = self.client([dossier, draft, dict(approved=True, issues=[]),
                          dict(approved=True, issues=[])])
        with patch.object(n, "collect", return_value=docs):
            n.prepare_article(ai, None, [[dict(title="A")]], [])
        self.assertEqual(ai.calls, 5)

    def test_review_rejection_never_returns_article(self):
        docs, dossier, draft = fixture()
        ai = self.client([dossier, draft, dict(approved=False, issues=["Niepoparta teza"])])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "Niepoparta teza"):
                n.prepare_article(ai, None, [[dict(title="A")], [dict(title="B")]], [])
        self.assertEqual(ai.calls, 4)

    def test_api_failure_is_not_editorial_retry(self):
        docs, _, _ = fixture()
        ai = self.client([n.APIError("HTTP 429")])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaises(n.APIError):
                n.prepare_article(ai, None, [[dict(title="A")], [dict(title="B")]], [])
        self.assertEqual(ai.calls, 2)

    def test_collection_failure_can_try_next_without_api_cost(self):
        docs, dossier, draft = fixture()
        ai = self.client([dossier, draft, dict(approved=True, issues=[])])
        with patch.object(n, "collect", side_effect=[n.Rejected("Brak treści"), docs]):
            result = n.prepare_article(ai, None, [[dict(title="A")], [dict(title="B")]], [])
        self.assertEqual(result[0][0]["title"], "B")
        self.assertEqual(ai.calls, 4)

    def test_diagnostic_redacts_key(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "secret-test"}):
            self.assertEqual(n.diagnostic_text("Powód secret-test"), "Powód [UKRYTO]")


if __name__ == "__main__":
    unittest.main()
