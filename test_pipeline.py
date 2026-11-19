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
             for i in range(16)]
    dossier = dict(ready=True, independent=True, high_risk=False, official_relevant=False, facts=facts,
                   risk_reason="Opis neutralnego wydarzenia w materiale testowym.", risk_categories=[])
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
    for i, doc in enumerate(docs):
        doc["text"] += " " + " ".join(("źródło" if i == 0 else "relacja") + str(j) for j in range(650))
    return docs, dossier, draft



def primary_fixture(docs, dossier):
    quote = "Redakcja Alfa rozmawiała bezpośrednio ze świadkiem i opublikowała własny wywiad."
    docs[0]["text"] += " " + quote
    dossier.update(high_risk=True, risk_reason="Relacja dotyczy działań wojennych.",
                   risk_categories=["active_conflict"], risk_fact_ids=["F1"],
                   primary_evidence=[dict(
                       source="S1", kind="original_interview", is_original=True,
                       author="Redakcja Alfa", authorship_quote=quote,
                       why_primary="Tekst opisuje własny wywiad redakcji ze świadkiem wydarzenia.",
                       supports_facts=["F1"])])
    return dossier


def approved_primary_review():
    return dict(approved=True, issues=[], primary_checks=[
        dict(source="S1", is_original=True, supports_risk_facts=True,
             reason="Oryginalny wywiad i wskazane fragmenty wspierają przypisane twierdzenia.")])


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
        dossier.update(high_risk=True, official_relevant=True, risk_categories=["active_conflict"],
                       risk_reason="Opis działań wojennych.", risk_fact_ids=["F1"])
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
        with self.assertRaisesRegex(n.UnsupportedNumber, "99999"):
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
    def client(self, responses, limit=n.DEFAULT_CALL_LIMIT):
        class Fake:
            calls = 1  # Selection already consumed one request.
            call_limit = limit
            model = "test"
            def ask(self, *args, **kwargs):
                if self.calls >= self.call_limit:
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
        dossier.update(high_risk=True, official_relevant=True, risk_categories=["active_conflict"],
                       risk_reason="Opis działań wojennych.", risk_fact_ids=["F1"])
        primary_fixture(docs, dossier)
        ai = self.client([dict(ready=False, reason="Brak faktów"), dossier])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "wysokiego ryzyka"):
                n.prepare_article(ai, None, [[dict(title="A")], [dict(title="B")]], [])
        self.assertEqual(ai.calls, 3)

    def test_high_risk_receives_both_reviews(self):
        docs, dossier, draft = fixture()
        docs[0]["official"] = True
        dossier.update(high_risk=True, official_relevant=True, risk_categories=["active_conflict"],
                       risk_reason="Opis działań wojennych.", risk_fact_ids=["F1"])
        primary_fixture(docs, dossier)
        ai = self.client([dossier, draft, dict(approved=True, issues=[]),
                          approved_primary_review()])
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

    def test_rejected_rewrite_leaves_budget_for_both_reviews_on_backup_topic(self):
        docs, dossier, draft = fixture()
        bad_draft = copy.deepcopy(draft)
        bad_draft["sections"][0]["paragraphs"][0]["text"] += " Podano także 99999 zdarzeń."
        high_docs, high_dossier = copy.deepcopy((docs, dossier))
        high_docs[0]["official"] = True
        high_dossier.update(high_risk=True, official_relevant=True, risk_categories=["public_safety"],
                            risk_reason="Opis zagrożenia bezpieczeństwa publicznego.", risk_fact_ids=["F1"])
        primary_fixture(high_docs, high_dossier)
        ai = self.client([
            dict(ready=False, reason="Niewystarczające dowody"),
            dossier, bad_draft, copy.deepcopy(bad_draft),
            high_dossier, draft, dict(approved=True, issues=[]), approved_primary_review(),
        ], n.FALLBACK_CALL_LIMIT)
        attempts = []
        topics = [[dict(title=title)] for title in ("A", "B", "C")]
        with patch.object(n, "collect", side_effect=[docs, docs, high_docs]):
            result = n.prepare_article(ai, None, topics, attempts)
        self.assertEqual(result[0][0]["title"], "C")
        self.assertEqual([entry["status"] for entry in attempts], ["POMINIĘTO", "POMINIĘTO", "ZATWIERDZONO"])
        self.assertIn("99999", attempts[1]["reason"])
        self.assertEqual(ai.calls, n.FALLBACK_CALL_LIMIT)
        self.assertEqual(ai.call_limit, n.FALLBACK_CALL_LIMIT)

    def test_unusable_model_response_tries_next_topic(self):
        docs, dossier, draft = fixture()
        ai = self.client([n.CandidateResponseError("Niepoprawny JSON"),
                          dossier, draft, dict(approved=True, issues=[])],
                         n.FALLBACK_CALL_LIMIT)
        attempts = []
        with patch.object(n, "collect", return_value=docs):
            result = n.prepare_article(ai, None, [[dict(title="A")], [dict(title="B")]], attempts)
        self.assertEqual(result[0][0]["title"], "B")
        self.assertIn("Niepoprawny JSON", attempts[0]["reason"])

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



class ExtractionTests(unittest.TestCase):
    def test_div_body_without_paragraphs(self):
        text, method, _ = n.extract_article(
            '<nav><p>' + 'MENU ' * 400 + '</p></nav>'
            '<div itemprop="articleBody"><div>' + 'Fakty wydarzenia. ' * 160 +
            '</div><aside>REKLAMA</aside></div>', "https://onet.pl/a", "Tytuł")
        self.assertEqual(len(n.words(text)), 320)
        self.assertNotIn("MENU", text)
        self.assertNotIn("REKLAMA", text)
        self.assertEqual(method, "HTML articleBody")

    def test_matching_free_jsonld(self):
        body = "Pełna publiczna treść artykułu. " * 100
        data = {"@graph": [{"@type": "NewsArticle", "url": "https://onet.pl/a",
                "isAccessibleForFree": True, "articleBody": body}]}
        text, method, _ = n.extract_article(
            '<script type="application/ld+json">' + json.dumps(data) + '</script>',
            "https://onet.pl/a", "")
        self.assertEqual(len(n.words(text)), 400)
        self.assertIn("JSON-LD", method)

    def test_paid_and_unrelated_jsonld_not_used(self):
        for free, url in [(False, "https://onet.pl/a"), (True, "https://onet.pl/b")]:
            data = {"@type": "NewsArticle", "url": url,
                    "isAccessibleForFree": free, "articleBody": "UKRYTE " * 400}
            text, _, _ = n.extract_article(
                '<p>' + 'Dostępna zajawka. ' * 10 + '</p>'
                '<script type="application/ld+json">' + json.dumps(data) + '</script>',
                "https://onet.pl/a", "")
            self.assertNotIn("UKRYTE", text)

    def test_short_source_retains_count(self):
        reader = n.Reader()
        with patch.object(reader, "page", return_value=(
                "https://onet.pl/a", '<div itemprop="articleBody">' + 'słowo ' * 119 + '</div>')):
            with self.assertRaises(n.SourceRejected) as error:
                reader.document(dict(url="https://onet.pl/a"))
        self.assertEqual(error.exception.count, 119)

    def test_collection_failure_preserves_all_source_diagnostics(self):
        reader = MagicMock()
        reader.document.side_effect = [
            n.SourceRejected("Za krótki", 120, "HTML articleBody"),
            n.Rejected("Źródło zwróciło HTTP 403.")]
        audit = []
        with self.assertRaises(n.Rejected):
            n.collect(reader, [dict(url="https://onet.pl/a"), dict(url="https://wp.pl/b")], audit)
        self.assertEqual(audit[0]["words"], 120)
        self.assertEqual(audit[1]["words"], None)
        self.assertIn("403", audit[1]["reason"])

    def test_less_than_300_reports_real_total(self):
        def document(item):
            prefix = "alpha" if "onet" in item["url"] else "beta"
            return dict(url=item["url"], group=n.source_group(item["url"]),
                        text=" ".join(prefix + str(i) for i in range(140)),
                        official_links=[])
        reader = MagicMock()
        reader.document.side_effect = document
        audit = []
        with self.assertRaisesRegex(n.Rejected, "280 słów"):
            n.collect(reader, [dict(url="https://onet.pl/a"), dict(url="https://wp.pl/b")], audit)
        self.assertEqual([a["words"] for a in audit], [140, 140])
        self.assertTrue(all(a["status"] == "PRZYJĘTO" for a in audit))

    def test_original_16000_character_limit_restored(self):
        body = " ".join("wyraz" + str(i) for i in range(1400))
        reader = n.Reader()
        with patch.object(reader, "page", return_value=(
                "https://onet.pl/a", '<div itemprop="articleBody">' + body + '</div>')):
            doc = reader.document(dict(url="https://onet.pl/a"))
        self.assertGreater(len(doc["text"]), 8000)
        self.assertLessEqual(len(doc["text"]), 16000)



class AdaptiveLengthTests(unittest.TestCase):
    def material(self, total, count):
        docs, dossier, draft = fixture()
        for index, doc in enumerate(docs):
            length = total // 2 + (total % 2 if index else 0)
            doc["text"] = " ".join(doc["text"].split()[:length])
        dossier["facts"] = dossier["facts"][:count]
        return docs, dossier, draft

    def article(self, draft, length):
        # Three unique paragraphs with an exact synthetic word count.
        for i, section in enumerate(draft["sections"]):
            size = length // 3 + (length % 3 if i == 0 else 0)
            prefix = ["Pierwszy", "Drugi", "Trzeci"][i]
            section["paragraphs"] = [
                dict(text=prefix + " " + " ".join(["opis"] * (size - 1)),
                     facts=["F1", "F2"])]
        return draft

    def test_708_source_words_allow_standard_article(self):
        docs, dossier, draft = self.material(708, 10)
        facts = n.check_dossier(dossier, docs)
        policy = n.article_policy(facts, docs)
        self.assertEqual((policy["min_words"], policy["max_words"]), (300, 550))
        self.assertEqual(policy["source_words"], 708)
        length, sources = n.check_draft(self.article(draft, 360), facts, docs)
        self.assertEqual(length, 360)
        self.assertEqual(sources, {"S1", "S2"})

    def test_six_facts_choose_brief_even_with_long_sources(self):
        docs, dossier, draft = self.material(1200, 6)
        facts = n.check_dossier(dossier, docs)
        self.assertEqual(n.article_policy(facts, docs)["max_words"], 300)
        self.assertEqual(n.check_draft(self.article(draft, 216), facts, docs)[0], 216)
        with self.assertRaisesRegex(n.Rejected, "słów"):
            n.check_draft(self.article(draft, 600), facts, docs)

    def test_rich_material_keeps_long_articles(self):
        docs, dossier, draft = self.material(1200, 16)
        facts = n.check_dossier(dossier, docs)
        self.assertEqual(n.article_policy(facts, docs)["min_words"], 600)
        self.assertEqual(n.check_draft(draft, facts, docs)[0], 648)

    def test_word_boundaries_for_each_mode(self):
        for total, count, low, high in [(400, 6, 160, 300),
                                        (708, 10, 300, 550),
                                        (1200, 16, 600, 1000)]:
            docs, dossier, draft = self.material(total, count)
            facts = n.check_dossier(dossier, docs)
            for length in [low, high]:
                with self.subTest(count=count, length=length):
                    self.assertEqual(n.check_draft(self.article(draft, length), facts, docs)[0], length)
            for length in [low-1, high+1]:
                with self.subTest(count=count, length=length):
                    with self.assertRaisesRegex(n.Rejected, "słów"):
                        n.check_draft(self.article(draft, length), facts, docs)

    def test_five_facts_rejected_with_actual_count(self):
        docs, dossier, _ = self.material(708, 5)
        with self.assertRaisesRegex(n.Rejected, "otrzymano 5"):
            n.check_dossier(dossier, docs)

    def test_duplicate_claim_cannot_inflate_fact_count(self):
        docs, dossier, _ = self.material(708, 10)
        dossier["facts"][1]["claim"] = dossier["facts"][0]["claim"]
        with self.assertRaisesRegex(n.Rejected, "Powtórzony fakt"):
            n.check_dossier(dossier, docs)

    def test_under_300_cited_words_rejected(self):
        docs, dossier, _ = self.material(299, 10)
        facts = n.check_dossier(dossier, docs)
        with self.assertRaisesRegex(n.Rejected, "299"):
            n.article_policy(facts, docs)

    def test_uncited_material_does_not_inflate_length(self):
        docs, dossier, _ = self.material(400, 16)
        facts = n.check_dossier(dossier, docs)
        extra = dict(docs[0], id="S3", text="materiał " * 2000)
        self.assertEqual(n.article_policy(facts, docs + [extra])["max_words"], 300)

    def test_213_word_source_is_read(self):
        reader = n.Reader()
        with patch.object(reader, "page", return_value=(
                "https://onet.pl/a", '<div itemprop="articleBody">' + "słowo " * 213 + "</div>")):
            doc = reader.document(dict(url="https://onet.pl/a"))
        self.assertEqual(len(n.words(doc["text"])), 213)

    def test_empty_html_attributes_regression(self):
        html = ('<div class><script type>ignored</script><span itemprop>lead</span></div>'
                '<div itemprop="articleBody" class><p>' + "Treść wiadomości. " * 100 + '</p></div>')
        text, _, _ = n.extract_article(html, "https://onet.pl/a", "")
        self.assertEqual(len(n.words(text)), 200)

    @unittest.skipUnless(DEPS, "Integration requires requirements.txt")
    def test_brief_is_saved_and_built(self):
        docs, dossier, draft = self.material(708, 6)
        draft = self.article(draft, 216)
        with patch(__name__ + ".fixture", return_value=(docs, dossier, draft)):
            IntegrationTests.test_publish_history_and_build(self)



class ProvenanceTests(unittest.TestCase):
    def attributed(self, kind="poll"):
        docs, dossier, draft = fixture()
        quote = "Badanie wykonała Pracownia Alfa na zlecenie Portalu Beta w ostatnim tygodniu."
        docs[0]["text"] += " " + quote
        dossier.update(source_mode="attributed", independent=False,
                       origin=dict(kind=kind, name=dict(value="Pracownia Alfa",
                           evidence=[dict(source="S1", quote=quote)])))
        if kind == "poll":
            dossier["origin"]["poll"] = dict(
                commissioner=dict(value="Portal", evidence=[dict(source="S1", quote=quote)]),
                fieldwork=None, sample=None, method=None)
        draft["title"] = "Sondaż: opis wyników badania opinii" if kind == "poll" else "Według Pracownia Alfa opisano wydarzenie"
        draft["summary"] = "Według Pracownia Alfa " + draft["summary"]
        draft["sections"][0]["paragraphs"][0]["text"] = "Według Pracownia Alfa " + draft["sections"][0]["paragraphs"][0]["text"]
        return docs, dossier, draft

    def test_same_poll_can_pass_with_attribution(self):
        docs, dossier, draft = self.attributed()
        facts = n.check_dossier(dossier, docs)
        n.check_draft(draft, facts, docs, dossier)
        text = n.render_markdown(draft, facts, dossier)
        self.assertIn("Pochodzenie informacji", text)
        self.assertIn("Pracownia Alfa", text)
        self.assertIn("próba: nie podano", text)
        self.assertIn("[S1](#source-S1)", text)
        self.assertIn("nie wynik przyszłych wyborów", text)

    def test_origin_name_needs_real_evidence(self):
        docs, dossier, _ = self.attributed()
        dossier["origin"]["name"]["value"] = "Wymyślona Agencja"
        with self.assertRaisesRegex(n.Rejected, "Niepotwierdzone"):
            n.check_dossier(dossier, docs)

    def test_false_independence_not_automatically_approved(self):
        docs, dossier, _ = fixture()
        dossier["independent"] = False
        with self.assertRaises(n.Rejected):
            n.check_dossier(dossier, docs)

    def test_attributed_without_origin_is_rejected(self):
        docs, dossier, _ = fixture()
        dossier.update(source_mode="attributed", independent=False)
        with self.assertRaises(n.Rejected):
            n.check_dossier(dossier, docs)

    def test_missing_attribution_rejected(self):
        docs, dossier, draft = self.attributed()
        facts = n.check_dossier(dossier, docs)
        draft["summary"] = "Podsumowanie nie wskazuje autora badania opisanego w dostępnych materiałach."
        with self.assertRaisesRegex(n.Rejected, "Brak nazwy"):
            n.check_draft(draft, facts, docs, dossier)

    def test_poll_must_be_named_in_title(self):
        docs, dossier, draft = self.attributed()
        draft["title"] = "Opis aktualnych wyników badania opinii"
        with self.assertRaisesRegex(n.Rejected, "Tytuł"):
            n.check_draft(draft, n.check_dossier(dossier, docs), docs, dossier)

    def test_fabricated_poll_method_rejected(self):
        docs, dossier, _ = self.attributed()
        dossier["origin"]["poll"]["method"] = dict(
            value="CATI", evidence=dossier["origin"]["name"]["evidence"])
        with self.assertRaises(n.Rejected):
            n.check_dossier(dossier, docs)

    def test_high_risk_still_requires_primary_source(self):
        docs, dossier, _ = self.attributed("dispatch")
        dossier.update(high_risk=True, risk_categories=["active_conflict"],
                       risk_reason="Opis działań wojennych.", risk_fact_ids=["F1"])
        with self.assertRaisesRegex(n.Rejected, "wysokiego ryzyka"):
            n.check_dossier(dossier, docs)

    def test_one_retained_dispatch_source_can_be_used(self):
        docs, dossier, draft = self.attributed("dispatch")
        # Retained source is long enough after copied versions have been removed.
        docs[0]["text"] += " materiał" * 700
        for fact in dossier["facts"]:
            fact["evidence"] = [dict(source="S1", quote=docs[0]["text"][:80])]
        facts = n.check_dossier(dossier, docs[:1])
        _, sources = n.check_draft(draft, facts, docs[:1], dossier)
        self.assertEqual(sources, {"S1"})

    def test_note_is_in_final_review(self):
        docs, dossier, draft = self.attributed()
        class Fake:
            calls = 1
            call_limit = n.DEFAULT_CALL_LIMIT
            def ask(self, prompt, data, **kwargs):
                self.calls += 1
                if self.calls == 2:
                    return dossier
                if self.calls == 3:
                    return draft
                if "provenance_note" not in data:
                    raise AssertionError("Missing provenance in review")
                return dict(approved=True, issues=[])
        with patch.object(n, "collect", return_value=docs):
            n.prepare_article(Fake(), None, [[dict(title="Temat")]], [])


    @unittest.skipUnless(DEPS, "Integration requires requirements.txt")
    def test_provenance_survives_publication_and_html(self):
        import build as b
        docs, dossier, draft = self.attributed()
        original_build = b.main
        def build_and_check():
            original_build()
            article = next((b.OUT / "articles").glob("*.html")).read_text(encoding="utf-8")
            self.assertIn("Pochodzenie informacji", article)
            self.assertIn("Pracownia Alfa", article)
            self.assertIn("próba: nie podano", article)
        with patch(__name__ + ".fixture", return_value=(docs, dossier, draft)), \
             patch.object(b, "main", side_effect=build_and_check):
            IntegrationTests.test_publish_history_and_build(self)



class PrimaryRiskTests(unittest.TestCase):
    def test_original_nonofficial_document_can_qualify(self):
        docs, dossier, _ = fixture()
        primary_fixture(docs, dossier)
        self.assertFalse(docs[0]["official"])
        self.assertTrue(n.check_dossier(dossier, docs))

    def test_government_domain_alone_is_not_enough(self):
        docs, dossier, _ = fixture()
        docs[0]["official"] = True
        dossier.update(high_risk=True, risk_categories=["active_conflict"],
                       risk_reason="Brak potwierdzenia ataku.", risk_fact_ids=["F1"])
        with self.assertRaisesRegex(n.Rejected, "Nie wskazano"):
            n.check_dossier(dossier, docs)

    def test_risk_reason_is_required(self):
        docs, dossier, _ = fixture()
        dossier.pop("risk_reason")
        with self.assertRaisesRegex(n.Rejected, "uzasadnienia"):
            n.check_dossier(dossier, docs)

    def test_low_flag_cannot_hide_high_risk_category(self):
        docs, dossier, _ = fixture()
        dossier["risk_categories"] = ["casualties"]
        with self.assertRaisesRegex(n.Rejected, "Sprzeczna"):
            n.check_dossier(dossier, docs)

    def test_missing_author_quote_is_rejected(self):
        docs, dossier, _ = fixture()
        primary_fixture(docs, dossier)
        dossier["primary_evidence"][0]["authorship_quote"] = "Redakcja Alfa opisała nieistniejący w źródle materiał."
        with self.assertRaisesRegex(n.Rejected, "autorstwo"):
            n.check_dossier(dossier, docs)

    def test_secondary_document_cannot_be_marked_primary(self):
        docs, dossier, _ = fixture()
        primary_fixture(docs, dossier)
        dossier["primary_evidence"][0]["is_original"] = False
        with self.assertRaises(n.Rejected):
            n.check_dossier(dossier, docs)

    def test_primary_has_to_support_each_risk_fact(self):
        docs, dossier, _ = fixture()
        primary_fixture(docs, dossier)
        dossier["risk_fact_ids"].append("F2")
        with self.assertRaisesRegex(n.Rejected, "F2"):
            n.check_dossier(dossier, docs)

    def test_primary_cannot_claim_fact_from_other_source(self):
        docs, dossier, _ = fixture()
        primary_fixture(docs, dossier)
        dossier["risk_fact_ids"] = ["F2"]
        dossier["primary_evidence"][0]["supports_facts"] = ["F2"]
        with self.assertRaisesRegex(n.Rejected, "nie ma dowodu"):
            n.check_dossier(dossier, docs)

    def test_second_review_must_confirm_document_role(self):
        docs, dossier, _ = fixture()
        primary_fixture(docs, dossier)
        with self.assertRaises(n.Rejected):
            n.check_primary_review(dict(approved=True, issues=[]), dossier)
        review = approved_primary_review()
        review["primary_checks"][0]["is_original"] = False
        with self.assertRaises(n.Rejected):
            n.check_primary_review(review, dossier)
        n.check_primary_review(approved_primary_review(), dossier)

    def test_research_links_are_real_and_allowed(self):
        html = ('<nav><a href="https://www.gov.pl/web/nav/komunikat">Źródło</a></nav>'
                '<article><a href="https://www.cbos.pl/raport-wyborczy">Raport wyborczy CBOS</a>'
                '<a href="https://wiadomosci.onet.pl/wywiad-test">Oryginalny wywiad</a>'
                '<a href="https://unknown.invalid/report">Raport</a>'
                '<a href="http://127.0.0.1/report">Źródło</a></article>')
        links = n.research_links(html, "https://onet.pl/test", "Sondaż wyborczy CBOS")
        self.assertEqual(len(links), 1)
        self.assertIn("https://www.cbos.pl/raport-wyborczy", links)
        self.assertNotIn("https://wiadomosci.onet.pl/wywiad-test", links)

    def test_risk_explanation_survives_rejection_summary(self):
        docs, dossier, _ = fixture()
        dossier.update(high_risk=True, risk_categories=["casualties"],
                       risk_reason="Brak niezależnego potwierdzenia liczby ofiar.",
                       risk_fact_ids=["F1"])
        ai = RetryTests().client([dossier])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "liczby ofiar"):
                n.prepare_article(ai, None, [[dict(title="Temat")]], [])



class SourceRelevanceRegressionTests(unittest.TestCase):
    def test_supplemental_news_stories_are_excluded(self):
        html = '<article><a href="https://biznes.interia.pl/raport-microsoft">Raport Microsoft</a><a href="https://wiadomosci.wp.pl/komunikat-lotto">Komunikat Lotto</a></article>'
        self.assertEqual(n.research_links(html, "https://onet.pl/test", "Awaria Microsoft"), [])

    def test_recommended_primary_links_are_excluded(self):
        html = '<article><aside class="recommended"><a href="https://www.cbos.pl/raport-wyborczy">Raport wyborczy</a></aside></article>'
        self.assertEqual(n.research_links(html, "https://onet.pl/test", "Sondaż wyborczy"), [])

    def test_unrelated_primary_report_is_excluded(self):
        html = '<article><a href="https://stat.gov.pl/raport-inflacja">Raport inflacja</a></article>'
        self.assertEqual(n.research_links(html, "https://onet.pl/test", "Awaria Microsoft"), [])

    def test_missing_topic_does_not_follow_links(self):
        self.assertEqual(n.research_links('<article><a href="https://www.cbos.pl/raport-wyborczy">Raport wyborczy</a></article>', "https://onet.pl/test"), [])

    def test_unique_short_exact_quote_gets_source_context(self):
        text = "To jest pełna relacja o wydarzeniu. Unikatowy dowód znajduje się w środku tego dokumentu."
        quote = n.evidence_quote("Unikatowy dowód", text, "Fakt F1, źródło S1")
        self.assertIn(quote, text)
        self.assertGreaterEqual(len(quote), 30)

    def test_ambiguous_short_quote_is_rejected(self):
        with self.assertRaisesRegex(n.Rejected, "wielokrotnie"):
            n.evidence_quote("ten sam dowód", "ten sam dowód oraz ten sam dowód", "F1/S1")

    def test_false_quote_has_fact_and_source_diagnostic(self):
        with self.assertRaisesRegex(n.Rejected, "F1/S2.*nie istnieje"):
            n.evidence_quote("Zmyślony fragment dowodowy", "Rzeczywista treść źródła jest inna.", "F1/S2")

    def test_long_exact_quote_is_retained(self):
        quote = "fakty " * 100
        self.assertEqual(n.evidence_quote(quote, quote, "F1/S1"), quote.strip())

    def test_quote_whitespace_is_normalized(self):
        text = "Dokładny fragment źródła zawiera liczne fakty."
        self.assertEqual(n.evidence_quote(text.replace(" ", "  "), text, "F1/S1"), text)

    def test_non_text_and_too_large_evidence_rejected(self):
        for quote in (None, 123, "x" * 2001):
            with self.subTest(quote_type=type(quote).__name__):
                with self.assertRaises(n.Rejected):
                    n.evidence_quote(quote, "x" * 3000, "F1/S1")



@unittest.skipUnless(DEPS, "Integration requires requirements.txt")
class TopicSelectionRegressionTests(unittest.TestCase):
    def candidates(self):
        return [dict(id="hash1", title="Konkretny temat numer jeden",
                     summary="Dłuższy opis wydarzenia dla pierwszego wydawcy.",
                     source="Onet", published="2026-11-14", url="https://wiadomosci.onet.pl/test"),
                dict(id="hash2", title="Inny nagłówek o tym samym wydarzeniu",
                     summary="Dłuższy opis wydarzenia dla drugiego wydawcy.",
                     source="WP", published="2026-11-14", url="https://wiadomosci.wp.pl/test")]

    def select(self, response, queue=None, records=None):
        import generator as g
        ai = MagicMock()
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, queue or self.candidates(), records or [], audit)
        return result, audit, ai

    def test_short_ids_resolve_to_original_records(self):
        result, audit, ai = self.select({"topics": [{"ids": ["N1", "N2"]}]})
        self.assertEqual([i["id"] for i in result[0]], ["hash1", "hash2"])
        sent = ai.ask.call_args.args[1]["candidates"]
        self.assertEqual([i["id"] for i in sent], ["N1", "N2"])
        self.assertNotEqual(sent[0]["publisher_group"], sent[1]["publisher_group"])

    def test_original_ids_still_accepted(self):
        result, _, _ = self.select({"topics": [{"ids": ["hash1", "hash2"]}]})
        self.assertEqual(len(result), 1)

    def test_empty_result_explains_model_reason(self):
        result, audit, _ = self.select({"topics": [], "reason": "Tematy dotyczą innych wydarzeń."})
        self.assertEqual(result, [])
        self.assertIn("Tematy dotyczą innych wydarzeń.", " ".join(audit))

    def test_unknown_ids_are_diagnosed(self):
        result, audit, _ = self.select({"topics": [{"ids": ["N1", "N999"]}]})
        self.assertEqual(result, [])
        self.assertIn("nieznane identyfikatory", " ".join(audit))

    def test_repeated_id_is_not_two_sources(self):
        result, audit, _ = self.select({"topics": [{"ids": ["N1", "N1"]}]})
        self.assertEqual(result, [])
        self.assertIn("powtórzone identyfikatory", " ".join(audit))

    def test_same_publisher_candidate_reaches_later_provenance_check(self):
        queue = self.candidates()
        queue[0]["url"] = "https://wydarzenia.interia.pl/test"
        queue[1]["url"] = "https://biznes.interia.pl/other"
        result, audit, _ = self.select({"topics": [{"ids": ["N1", "N2"]}]}, queue=queue)
        self.assertEqual(len(result), 1)
        self.assertIn("attributed", " ".join(audit))

    def test_single_candidate_is_not_rejected_before_research(self):
        result, _, _ = self.select({"topics": [{"ids": ["N1"]}]})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0]["id"], "hash1")

    def test_valid_group_after_invalid_groups_is_considered(self):
        result, _, _ = self.select({"topics": [None, {}, {"ids": []}, {"ids": ["N1", "N2"]}]})
        self.assertEqual(len(result), 1)

    def test_duplicate_group_is_not_retried(self):
        result, _, _ = self.select({"topics": [{"ids": ["N1", "N2"]}, {"ids": ["N2", "N1"]}]})
        self.assertEqual(len(result), 1)

    def test_published_topic_is_rejected(self):
        result, audit, _ = self.select({"topics": [{"ids": ["N1", "N2"]}]}, records=self.candidates()[:1])
        self.assertEqual(result, [])
        self.assertIn("już opublikowany", " ".join(audit))



class OriginEvidenceRegressionTests(unittest.TestCase):
    def origin(self, quote, value="Microsoft", text=None):
        docs = [dict(id="S1", text=text or "Microsoft wydał komunikat o przywróceniu usług po awarii.")]
        dossier = dict(source_mode="attributed", independent=False,
                       origin=dict(kind="statement", name=dict(value=value,
                           evidence=[dict(source="S1", quote=quote)])))
        return dossier, docs

    def attributed(self, kind="poll"):
        """Helper from ProvenanceTests."""
        docs, dossier, draft = fixture()
        quote = "Badanie wykonała Pracownia Alfa na zlecenie Portalu Beta w ostatnim tygodniu."
        docs[0]["text"] += " " + quote
        dossier.update(source_mode="attributed", independent=False,
                       origin=dict(kind=kind, name=dict(value="Pracownia Alfa",
                           evidence=[dict(source="S1", quote=quote)])))
        if kind == "poll":
            dossier["origin"]["poll"] = dict(
                commissioner=dict(value="Portal", evidence=[dict(source="S1", quote=quote)]),
                fieldwork=None, sample=None, method=None)
        return docs, dossier

    def test_short_exact_statement_evidence_has_context(self):
        dossier, docs = self.origin("Microsoft wydał komunikat")
        origin = n.check_origin(dossier, docs)
        quote = origin["name"]["evidence"][0]["quote"]
        self.assertGreaterEqual(len(quote), 30)
        self.assertIn(quote, docs[0]["text"])

    def test_long_exact_origin_evidence_is_accepted(self):
        quote = "Microsoft wydał komunikat. " + "Opis potwierdzonej informacji. " * 25
        dossier, docs = self.origin(quote, text=quote)
        n.check_origin(dossier, docs)

    def test_unfounded_full_company_name_rejected(self):
        dossier, docs = self.origin("Microsoft wydał komunikat o przywróceniu usług po awarii.",
                                   value="Microsoft Corporation")
        with self.assertRaisesRegex(n.Rejected, "S1.*nie występuje"):
            n.check_origin(dossier, docs)

    def test_paraphrased_origin_quote_rejected(self):
        dossier, docs = self.origin("Microsoft poinformował, że wszystkie usługi już działają.")
        with self.assertRaisesRegex(n.Rejected, "S1.*nie istnieje"):
            n.check_origin(dossier, docs)

    def test_ambiguous_short_name_is_not_enough(self):
        dossier, docs = self.origin("Microsoft", text="Microsoft to firma. Microsoft wydał komunikat.")
        with self.assertRaisesRegex(n.Rejected, "wielokrotnie"):
            n.check_origin(dossier, docs)

    def test_missing_mode_is_diagnosed_not_inferred(self):
        with self.assertRaisesRegex(n.Rejected, "brak source_mode"):
            n.check_origin(dict(independent=False), [])

    def test_missing_quote_in_origin_name_evidence_correctable(self):
        """Brak quote w origin.name.evidence -> CorrectableDossier (można skorygować)."""
        docs, dossier = self.attributed("dispatch")
        dossier["origin"]["name"]["evidence"][0].pop("quote", None)
        with self.assertRaisesRegex(n.CorrectableDossier, "Brak dowodu pochodzenia.*quote jest wymagany"):
            n.check_origin(dossier, docs)

    def test_null_quote_in_origin_name_evidence_correctable(self):
        """quote=null w origin.name.evidence -> CorrectableDossier."""
        docs, dossier = self.attributed("dispatch")
        dossier["origin"]["name"]["evidence"][0]["quote"] = None
        with self.assertRaisesRegex(n.CorrectableDossier, "Brak dowodu pochodzenia.*quote jest wymagany"):
            n.check_origin(dossier, docs)

    def test_missing_quote_in_origin_poll_evidence_correctable(self):
        """Brak quote w origin.poll.*.evidence -> CorrectableDossier."""
        docs, dossier = self.attributed("poll")
        dossier["origin"]["poll"]["commissioner"]["evidence"][0].pop("quote", None)
        with self.assertRaisesRegex(n.CorrectableDossier, "Brak dowodu pochodzenia.*quote jest wymagany"):
            n.check_origin(dossier, docs)


class DossierCorrectionTests(unittest.TestCase):
    def run_article(self, responses, docs):
        ai = RetryTests().client(responses)
        entries = []
        with patch.object(n, "collect", return_value=docs):
            result = n.prepare_article(ai, None, [[dict(title="Test")]], entries)
        return ai, entries, result

    def test_missing_risk_reason_corrected_with_full_review(self):
        docs, good, draft = fixture()
        bad = copy.deepcopy(good)
        bad["risk_reason"] = ""
        ai, entries, _ = self.run_article([bad, good, draft, dict(approved=True, issues=[])], docs)
        self.assertEqual(ai.calls, 5)
        self.assertIn("Brak uzasadnienia", entries[0]["correction"])

    def test_wrong_quote_can_be_replaced_only_by_real_evidence(self):
        docs, good, draft = fixture()
        bad = copy.deepcopy(good)
        bad["facts"][0]["evidence"][0]["quote"] = "Ten wymyślony cytat nie istnieje w żadnym dokumencie."
        ai, entries, _ = self.run_article([bad, good, draft, dict(approved=True, issues=[])], docs)
        self.assertEqual(ai.calls, 5)
        self.assertIn("nie istnieje", entries[0]["correction"])

    def test_second_invalid_dossier_is_rejected_without_loop(self):
        docs, bad, _ = fixture()
        bad["risk_reason"] = ""
        ai = RetryTests().client([copy.deepcopy(bad), copy.deepcopy(bad)])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "Brak uzasadnienia"):
                n.prepare_article(ai, None, [[dict(title="Test")]], [])
        self.assertEqual(ai.calls, 3)

    def test_correction_never_skips_final_review(self):
        docs, good, draft = fixture()
        bad = copy.deepcopy(good)
        bad["risk_reason"] = ""
        with self.assertRaisesRegex(n.Rejected, "Niepoparta teza"):
            self.run_article([bad, good, draft, dict(approved=False, issues=["Niepoparta teza"])], docs)

    def test_high_risk_correction_not_attempted_without_review_budget(self):
        docs, bad, _ = fixture()
        bad.update(high_risk=True, risk_categories=["active_conflict"], risk_reason="")
        ai = RetryTests().client([bad])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "Brak uzasadnienia"):
                n.prepare_article(ai, None, [[dict(title="Test")]], [])
        self.assertEqual(ai.calls, 2)

    def test_correction_escalating_risk_cannot_bypass_extra_review(self):
        docs, good, draft = fixture()
        bad = copy.deepcopy(good)
        bad["risk_reason"] = ""
        good.update(high_risk=True, risk_categories=["active_conflict"],
                    risk_reason="Opis działań wojennych.", risk_fact_ids=["F1"])
        primary_fixture(docs, good)
        ai = RetryTests().client([bad, good])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "wysokiego ryzyka"):
                n.prepare_article(ai, None, [[dict(title="Test")]], [])
        self.assertEqual(ai.calls, 3)

    def test_repair_request_contains_original_documents_and_validation_error(self):
        docs, good, _ = fixture()
        bad = copy.deepcopy(good)
        bad["risk_reason"] = ""
        ai = RetryTests().client([bad, good])
        original = ai.ask
        ai.ask = MagicMock(side_effect=original)
        n.validated_dossier(ai, docs, {})
        data = ai.ask.call_args.args[1]
        self.assertEqual([{k: v for k, v in d.items() if k != "passages"} for d in data["documents"]], docs)
        self.assertIn("Brak uzasadnienia", data["validation_error"])

    def test_missing_quote_origin_corrected_with_full_review(self):
        """Brak quote w origin -> korekta dossier + pełny review (5 wywołań)."""
        docs, good, draft = fixture()
        bad = copy.deepcopy(good)
        bad["source_mode"] = "attributed"
        bad["independent"] = False
        bad["origin"] = dict(kind="statement", name=dict(
            value="Pracownia Alfa", evidence=[dict(source="S1")]))  # brak quote
        # Poprawiony dossier z quote
        good_origin = copy.deepcopy(good)
        good_origin["source_mode"] = "attributed"
        good_origin["independent"] = False
        good_origin["origin"] = dict(kind="statement", name=dict(
            value="Pracownia Alfa", evidence=[dict(source="S1", quote="Pracownia Alfa potwierdza.")]))
        # docs[0] musi zawierać quote
        docs[0]["text"] += " Pracownia Alfa potwierdza."
        # Draft musi mieć atrybucję w summary i pierwszym akapicie
        draft = copy.deepcopy(draft)
        draft["title"] = "Pracownia Alfa: " + draft["title"]
        draft["summary"] = "Według Pracownia Alfa " + draft["summary"]
        draft["sections"][0]["paragraphs"][0]["text"] = "Według Pracownia Alfa " + draft["sections"][0]["paragraphs"][0]["text"]
        ai, entries, _ = self.run_article([bad, good_origin, draft, dict(approved=True, issues=[])], docs)
        self.assertEqual(ai.calls, 5)
        self.assertIn("Brak dowodu pochodzenia", entries[0]["correction"])
        self.assertEqual(entries[0]["status"], "ZATWIERDZONO")

    def test_missing_quote_origin_correction_blocked_by_budget(self):
        """Brak quote w origin high-risk -> brak budżetu na korektę + extra review -> Rejected."""
        docs, bad, _ = fixture()
        bad.update(high_risk=True, risk_categories=["active_conflict"], risk_reason="Test.")
        bad["source_mode"] = "attributed"
        bad["independent"] = False
        bad["origin"] = dict(kind="statement", name=dict(
            value="Pracownia Alfa", evidence=[dict(source="S1")]))  # brak quote
        ai = RetryTests().client([bad])  # tylko 1 odpowiedź (błędna)
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "Brak dowodu pochodzenia"):
                n.prepare_article(ai, None, [[dict(title="Test")]], [])
        self.assertEqual(ai.calls, 2)  # SELECT + EXTRACT (błędny), bez korekty

    def test_missing_real_evidence_never_publishes(self):
        """Fałszywy quote w origin -> korekta nie pomaga, brak prawdziwego dowodu -> Rejected."""
        docs, good, draft = fixture()
        bad = copy.deepcopy(good)
        bad["source_mode"] = "attributed"
        bad["independent"] = False
        bad["origin"] = dict(kind="statement", name=dict(
            value="Pracownia Alfa", evidence=[dict(source="S1", quote="Wymyślony cytat.")]))
        # Draft z atrybucją (choć fałszywa)
        draft = copy.deepcopy(draft)
        draft["summary"] = "Według Pracownia Alfa " + draft["summary"]
        draft["sections"][0]["paragraphs"][0]["text"] = "Według Pracownia Alfa " + draft["sections"][0]["paragraphs"][0]["text"]
        # Korekta też z fałszywym quote
        bad2 = copy.deepcopy(bad)
        bad2["origin"]["name"]["evidence"][0]["quote"] = "Inny wymyślony cytat."
        ai = RetryTests().client([bad, bad2])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "nie istnieje"):
                n.prepare_article(ai, None, [[dict(title="Test")]], [])
        self.assertEqual(ai.calls, 3)  # SELECT + EXTRACT + korekta

    def test_valid_origin_evidence_passes(self):
        """Poprawny quote w origin przechodzi walidację."""
        docs, good, draft = fixture()
        good["source_mode"] = "attributed"
        good["independent"] = False
        quote = "Pracownia Alfa wydała komunikat o wyniku badań."
        docs[0]["text"] += " " + quote
        good["origin"] = dict(kind="statement", name=dict(
            value="Pracownia Alfa", evidence=[dict(source="S1", quote=quote)]))
        # Draft musi mieć atrybucję
        draft = copy.deepcopy(draft)
        draft["title"] = "Pracownia Alfa: " + draft["title"]
        draft["summary"] = "Według Pracownia Alfa " + draft["summary"]
        draft["sections"][0]["paragraphs"][0]["text"] = "Według Pracownia Alfa " + draft["sections"][0]["paragraphs"][0]["text"]
        ai = RetryTests().client([good, draft, dict(approved=True, issues=[])])
        with patch.object(n, "collect", return_value=docs):
            result = n.prepare_article(ai, None, [[dict(title="Test")]], [])
        self.assertEqual(ai.calls, 4)
        self.assertEqual(result[0][0]["title"], "Test")


class JsonResponseTests(unittest.TestCase):
    def test_plain_and_fenced_json(self):
        for text in ('{"ready":true}', '\ufeff {"ready":true}', chr(96)*3 + 'json\n{"ready":true}\n' + chr(96)*3):
            self.assertEqual(n.parse_model_json(text), {"ready": True})

    def test_malformed_json_is_not_guessed(self):
        for text in ('{"ready": true,}', '{"ready":', 'Opis: {"ready":true}', '{"a":1} {"b":2}'):
            with self.subTest(text=text):
                with self.assertRaises(n.APIError):
                    n.parse_model_json(text)

    def test_ambiguous_or_nonstandard_json_is_rejected(self):
        for text in ('{"ready":true,"ready":false}', '{"value":NaN}', '[]', 'null'):
            with self.subTest(text=text):
                with self.assertRaises(n.APIError):
                    n.parse_model_json(text)

    def response(self, text, finish="STOP"):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]
        }).encode()
        return response

    def client(self, texts):
        client = n.Gemini("TEST_SECRET")
        client.opener.open = MagicMock(side_effect=[self.response(t) for t in texts])
        return client

    def test_invalid_json_retried_once(self):
        client = self.client(['{"ready":', '{"ready":true}'])
        with patch.object(n.time, "sleep"):
            self.assertEqual(client.ask("prompt", {"documents": []}), {"ready":True})
        self.assertEqual(client.calls, 2)

    def test_second_invalid_response_stops(self):
        client = self.client(['bad', 'still bad'])
        with patch.object(n.time, "sleep"):
            with self.assertRaisesRegex(n.APIError, "wiersz"):
                client.ask("prompt", {})
        self.assertEqual(client.calls, 2)

    def test_fifth_call_cannot_retry(self):
        client = self.client(['bad'])
        client.calls = 4
        with patch.object(n.time, "sleep"):
            with self.assertRaises(n.APIError):
                client.ask("prompt", {})
        self.assertEqual(client.calls, 5)
        self.assertEqual(client.opener.open.call_count, 1)

    def test_error_does_not_expose_response_text_or_secret(self):
        with self.assertRaises(n.APIError) as result:
            n.parse_model_json("TEST_SECRET bad response")
        self.assertNotIn("TEST_SECRET", str(result.exception))

    def test_truncated_api_output_not_accepted(self):
        client = n.Gemini("unused")
        client.opener.open = MagicMock(return_value=self.response('{"ready":true}', "MAX_TOKENS"))
        with self.assertRaises(n.APIError):
            client.ask("prompt", {})
        self.assertEqual(client.calls, 1)

    def test_fifth_call_blocks_further_requests(self):
        """Po 5 wywołaniach API dalsze żądania są blokowane."""
        client = n.Gemini("test")
        client.calls = 5
        client.opener.open = MagicMock()
        with self.assertRaises(n.APIError):
            client.ask("prompt", {})
        client.opener.open.assert_not_called()

    def test_fourth_call_allows_one_more(self):
        """4. wywołanie pozwala na 5., 5. blokuje kolejne."""
        client = n.Gemini("test")
        client.calls = 4
        client.opener.open = MagicMock(return_value=self.response('{"ready":true}', "STOP"))
        with patch.object(n.time, "sleep"):
            result = client.ask("prompt", {})
        self.assertEqual(client.calls, 5)
        self.assertEqual(result, {"ready": True})
        # 6. wywołanie blokowane
        with self.assertRaises(n.APIError):
            client.ask("prompt", {})


class RewriteTests(unittest.TestCase):
    def test_rewrite_retains_final_review_and_budget(self):
        docs, dossier, draft = fixture()
        ai = RetryTests().client([dossier, draft, copy.deepcopy(draft), dict(approved=True, issues=[])])
        real_check = n.check_draft
        calls = []
        def check(*args):
            calls.append(1)
            if len(calls) == 1:
                raise n.CopiedPassage("Wykryto dosłowną sekwencję 12 słów.")
            return real_check(*args)
        entries = []
        with patch.object(n, "collect", return_value=docs), patch.object(n, "check_draft", side_effect=check):
            n.prepare_article(ai, None, [[dict(title="Euclid")]], entries)
        self.assertEqual(ai.calls, 5)
        self.assertIn("rewrite", entries[0])
        self.assertEqual(entries[0]["status"], "ZATWIERDZONO")

    def test_second_copied_draft_is_rejected(self):
        docs, dossier, draft = fixture()
        ai = RetryTests().client([draft, draft])
        ai.calls = 2
        with patch.object(n, "check_draft", side_effect=n.CopiedPassage("Powielony tekst")):
            with self.assertRaises(n.CopiedPassage):
                n.checked_draft(ai, dossier, {}, docs, {}, {})
        self.assertEqual(ai.calls, 4)

    def test_no_rewrite_without_budget_for_review(self):
        docs, dossier, draft = fixture()
        ai = RetryTests().client([draft])
        ai.calls = 3
        with patch.object(n, "check_draft", side_effect=n.CopiedPassage("Powielony tekst")):
            with self.assertRaises(n.CopiedPassage):
                n.checked_draft(ai, dossier, {}, docs, {}, {})
        self.assertEqual(ai.calls, 4)

    def test_other_rejection_does_not_trigger_rewrite(self):
        docs, dossier, draft = fixture()
        ai = RetryTests().client([draft])
        with patch.object(n, "check_draft", side_effect=n.Rejected("Niepotwierdzona liczba")):
            with self.assertRaisesRegex(n.Rejected, "liczba"):
                n.checked_draft(ai, dossier, {}, docs, {}, {})
        self.assertEqual(ai.calls, 2)

    def test_high_risk_reserves_extra_review(self):
        docs, dossier, draft = fixture()
        dossier["high_risk"] = True
        ai = RetryTests().client([draft])
        ai.calls = 2
        with patch.object(n, "check_draft", side_effect=n.CopiedPassage("Powielony tekst")):
            with self.assertRaises(n.CopiedPassage):
                n.checked_draft(ai, dossier, {}, docs, {}, {})
        self.assertEqual(ai.calls, 3)



class NumberCorrectionTests(unittest.TestCase):
    def test_numeric_correction_has_error_and_sources(self):
        docs, dossier, draft = fixture()
        ai = RetryTests().client([draft, copy.deepcopy(draft)])
        original = ai.ask
        ai.ask = MagicMock(side_effect=original)
        real_check = n.check_draft
        count = []
        def check(*args):
            count.append(1)
            if len(count) == 1:
                raise n.UnsupportedNumber("Liczby bez zgodnego zapisu w źródłach: 999999.")
            return real_check(*args)
        facts = n.check_dossier(dossier, docs)
        with patch.object(n, "check_draft", side_effect=check):
            n.checked_draft(ai, dossier, facts, docs, n.article_policy(facts, docs), {})
        data = ai.ask.call_args.args[1]
        self.assertIn("999999", data["validation_error"])
        self.assertEqual(data["documents"], docs)

    def test_persistent_unsupported_number_is_rejected(self):
        docs, dossier, draft = fixture()
        ai = RetryTests().client([draft, draft])
        with patch.object(n, "check_draft", side_effect=n.UnsupportedNumber("999999")):
            with self.assertRaises(n.UnsupportedNumber):
                n.checked_draft(ai, dossier, {}, docs, {}, {})
        self.assertEqual(ai.calls, 3)

    def test_decimal_separator_mismatch_rejected(self):
        """1,5 w tekście vs 1.5 w źródle -> UnsupportedNumber (różny zapis)."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Wartość wynosi 1,5 mln."
        draft["sections"][0]["paragraphs"][0]["text"] += " Wartość wynosi 1.5 mln."
        facts = n.check_dossier(dossier, docs)
        with self.assertRaisesRegex(n.UnsupportedNumber, "1.5"):
            n.check_draft(draft, facts, docs)

    def test_thousand_separator_space_not_matched(self):
        """1 000 w tekście vs 1000 w źródle -> UnsupportedNumber (spacja nie w regex)."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Populacja to 1 000 osób."
        draft["sections"][0]["paragraphs"][0]["text"] += " Populacja to 1000 osób."
        facts = n.check_dossier(dossier, docs)
        # "1 000" daje "1" i "000"; "1000" to "1000" -> "000" i "1000" niezgodne
        with self.assertRaisesRegex(n.UnsupportedNumber, "000"):
            n.check_draft(draft, facts, docs)

    def test_leading_zeros_not_normalized(self):
        """001.5 w tekście vs 1.5 w źródle -> UnsupportedNumber (leading zeros nie usuwane)."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Wynik 1.5."
        draft["sections"][0]["paragraphs"][0]["text"] += " Wynik 001.5."
        facts = n.check_dossier(dossier, docs)
        with self.assertRaisesRegex(n.UnsupportedNumber, "001.5"):
            n.check_draft(draft, facts, docs)

    def test_truly_unconfirmed_number_rejected(self):
        """Liczba nieistniejąca w źródle -> UnsupportedNumber."""
        docs, dossier, draft = fixture()
        draft["sections"][0]["paragraphs"][0]["text"] += " Nowa wartość 99999."
        facts = n.check_dossier(dossier, docs)
        with self.assertRaisesRegex(n.UnsupportedNumber, "99999"):
            n.check_draft(draft, facts, docs)

    def test_different_value_rejected(self):
        """1.5 vs 15 to różne wartości -> UnsupportedNumber."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Wartość 1.5."
        draft["sections"][0]["paragraphs"][0]["text"] += " Wartość 15000."
        facts = n.check_dossier(dossier, docs)
        with self.assertRaisesRegex(n.UnsupportedNumber, "15000"):
            n.check_draft(draft, facts, docs)

    def test_missing_decimal_part_rejected(self):
        """1,5 vs 1 to różne wartości -> UnsupportedNumber."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Wartość 1,5."
        draft["sections"][0]["paragraphs"][0]["text"] += " Wartość 10000."
        facts = n.check_dossier(dossier, docs)
        with self.assertRaisesRegex(n.UnsupportedNumber, "10000"):
            n.check_draft(draft, facts, docs)

    # --- Unit-aware validation tests ---

    def _update_quotes(self, docs, facts):
        """Aktualizuje cytaty w evidence, aby odpowiadały obecnemu tekstu dokumentów.
        Obsługuje oba formaty: listę faktów pod kluczem 'facts' lub słownik {fact_id: fact}.
        """
        fact_list = facts.get("facts", [])
        if not fact_list and isinstance(facts, dict):
            # Słownik {fact_id: fact} zwracany przez check_dossier
            fact_list = list(facts.values())
        for fact in fact_list:
            for ev in fact.get("evidence", []):
                src_id = ev.get("source")
                for doc in docs:
                    if doc["id"] == src_id:
                        ev["quote"] = doc["text"]
                        break

    def test_magnitude_mismatch_mln_vs_tys_rejected(self):
        """1,5 mln vs 1,5 tys. -> UnsupportedNumber."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Budżet to 1,5 mln zł."
        draft["sections"][0]["paragraphs"][0]["text"] += " Budżet to 1,5 tys. zł."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        with self.assertRaisesRegex(n.UnsupportedNumber, "1,5.*mln|1,5.*tys"):
            n.check_draft(draft, facts, docs)

    def test_percent_vs_percentage_points_rejected(self):
        """10% vs 10 p.p. -> UnsupportedNumber."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Wzrost o 10%."
        draft["sections"][0]["paragraphs"][0]["text"] += " Wzrost o 10 p.p."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        with self.assertRaisesRegex(n.UnsupportedNumber, "10.*%|10.*p.p"):
            n.check_draft(draft, facts, docs)

    def test_currency_mismatch_mln_vs_mld_rejected(self):
        """50 mln zł vs 50 mld zł -> UnsupportedNumber."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Kwota 50 mln zł."
        draft["sections"][0]["paragraphs"][0]["text"] += " Kwota 50 mld zł."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        with self.assertRaisesRegex(n.UnsupportedNumber, "50.*mln|50.*mld"):
            n.check_draft(draft, facts, docs)

    def test_year_mismatch_rejected(self):
        """2025 vs 2026 -> UnsupportedNumber."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Rok 2025."
        draft["sections"][0]["paragraphs"][0]["text"] += " Rok 2026."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        with self.assertRaisesRegex(n.UnsupportedNumber, "2025|2026"):
            n.check_draft(draft, facts, docs)

    def test_correct_unit_passes(self):
        """Poprawna liczba z jednostką przechodzi."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Budżet to 1,5 mln zł."
        draft["sections"][0]["paragraphs"][0]["text"] += " Budżet to 1,5 mln zł."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        length, sources = n.check_draft(draft, facts, docs)
        self.assertGreater(length, 0)

    def test_year_in_text_passes(self):
        """Rok w tekście zgodny z źródłem przechodzi."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " W 2026 roku."
        docs[1]["text"] += " W 2026 roku."
        draft["sections"][0]["paragraphs"][0]["text"] += " W 2026 roku."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        length, sources = n.check_draft(draft, facts, docs)
        self.assertGreater(length, 0)

    def test_number_without_unit_still_validated(self):
        """Liczba bez jednostki wciąż musi istnieć w źródle (stara reguła)."""
        docs, dossier, draft = fixture()
        draft["sections"][0]["paragraphs"][0]["text"] += " Nowa wartość 99999."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        with self.assertRaisesRegex(n.UnsupportedNumber, "99999"):
            n.check_draft(draft, facts, docs)

    def test_currency_mismatch_mln_zl_vs_mln_usd_rejected(self):
        """50 mln zł vs 50 mln USD -> REJECT (waluta inna)."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Kwota 50 mln zł."
        draft["sections"][0]["paragraphs"][0]["text"] += " Kwota 50 mln USD."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        with self.assertRaisesRegex(n.UnsupportedNumber, "50.*mln.*usd|50.*mln.*zł"):
            n.check_draft(draft, facts, docs)

    def test_correct_percent_passes(self):
        """Poprawne 10% -> PASS."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Wzrost o 10%."
        draft["sections"][0]["paragraphs"][0]["text"] += " Wzrost o 10%."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        length, sources = n.check_draft(draft, facts, docs)
        self.assertGreater(length, 0)

    def test_correct_percentage_points_passes(self):
        """Poprawne 10 p.p. -> PASS."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Wzrost o 10 p.p."
        draft["sections"][0]["paragraphs"][0]["text"] += " Wzrost o 10 p.p."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        length, sources = n.check_draft(draft, facts, docs)
        self.assertGreater(length, 0)

    def test_correct_mln_passes(self):
        """Poprawne 1,5 mln -> PASS."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Wartość 1,5 mln."
        draft["sections"][0]["paragraphs"][0]["text"] += " Wartość 1,5 mln."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        length, sources = n.check_draft(draft, facts, docs)
        self.assertGreater(length, 0)

    def test_number_from_different_source_paragraph_rejected(self):
        """Liczba z innego akapitu źródła -> REJECT (nie można przesuwać liczb między cytatami)."""
        docs, dossier, draft = fixture()
        # doc[0] ma "10% w akapicie 1", doc[1] ma "20% w akapicie 2"
        docs[0]["text"] += " Pierwszy wskaźnik 10%."
        docs[1]["text"] += " Drugi wskaźnik 20%."
        # Artykuł próbuje użyć 20% z dowodu F1 (który pochodzi z doc[0])
        draft["sections"][0]["paragraphs"][0]["text"] += " Wskaźnik wynosi 20%."
        draft["sections"][0]["paragraphs"][0]["facts"] = ["F1"]
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        with self.assertRaisesRegex(n.UnsupportedNumber, "20"):
            n.check_draft(draft, facts, docs)

    def test_number_from_different_fact_id_rejected(self):
        """Liczba z dowodu innego fact_id -> REJECT."""
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Wskaźnik A: 10%."
        docs[1]["text"] += " Wskaźnik B: 20%."
        # Artykuł w akapicie z faktem F1 próbuje użyć 20% (który jest w dowodzie F2)
        draft["sections"][0]["paragraphs"][0]["text"] += " Wartość 20%."
        # Ogranicz akapit tylko do faktu F1
        draft["sections"][0]["paragraphs"][0]["facts"] = ["F1"]
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        # Paragraph ma facts=["F1"] ale próbuje użyć 20% z dowodu F2
        with self.assertRaisesRegex(n.UnsupportedNumber, "20"):
            n.check_draft(draft, facts, docs)

    def test_number_and_unit_in_separate_quote_fragments_rejected(self):
        """Liczba i jednostka w oddzielnych fragmentach cytatu -> REJECT."""
        docs, dossier, draft = fixture()
        # Dodajemy do istniejących tekstów (nie nadpisujemy, by zachować długość)
        docs[0]["text"] += " Wartość wynosi 10. Jednostka to procent. Wartość 10%."
        docs[1]["text"] += " Inny dokument o wskaźniku. Wartość 10%."
        # Tworzymy nowy dossier z poprawnymi quotes (pasującymi do nowego tekstu)
        q1 = "Wartość wynosi 10. Jednostka to procent."  # 40 znaków
        q2 = "Inny dokument o wskaźniku. Wartość 10%."  # 35 znaków
        facts_list = [dict(id=f"F{i+1}", claim=f"Fakt testowy numer {i+1}",
                           evidence=[dict(source=docs[i % 2]["id"],
                                          quote=q1 if i % 2 == 0 else q2)])
                      for i in range(16)]
        # check_dossier oczekuje facts jako LISTA
        dossier = dict(ready=True, independent=True, high_risk=False, official_relevant=False,
                       facts=facts_list, risk_reason="Opis neutralnego wydarzenia w materiale testowym.",
                       risk_categories=[])
        # Dossier z poprawnym quote (zawiera "10%")
        n.check_dossier(dossier, docs)
        self._update_quotes(docs, {"facts": facts_list})
        # Artykuł próbuje użyć "10%" jako wyrażenie
        # Ogranicz akapit TYLKO do faktu F1 (który ma problematyczny quote)
        draft["sections"][0]["paragraphs"][0]["facts"] = ["F1"]
        draft["sections"][0]["paragraphs"][0]["text"] += " Wartość 10%."
        # Nadpisujemy quote na taki, który NIE zawiera "10%" jako ciągłe wyrażenie
        for fact in facts_list:
            for ev in fact.get("evidence", []):
                if ev["source"] == "S1":
                    ev["quote"] = "Wartość wynosi 10. Jednostka to procent."
        with self.assertRaisesRegex(n.UnsupportedNumber, "10"):
            n.check_draft(draft, {f["id"]: f for f in facts_list}, docs)

    def test_same_amount_different_entity_limitation(self):
        """Ta sama kwota przypisana innej firmie -> wykryj ograniczenie walidacji kontekstu.
        Walidator sprawdza liczbę+jednostkę, nie przypisanie encji.
        """
        docs, dossier, draft = fixture()
        docs[0]["text"] += " Firma X zarobiła 100 mln zł."
        docs[1]["text"] += " Firma Y zarobiła 100 mln zł."
        draft["sections"][0]["paragraphs"][0]["text"] += " Firma X zarobiła 100 mln zł."
        facts = n.check_dossier(dossier, docs)
        self._update_quotes(docs, facts)
        # Walidator sprawdza czy "100 mln zł" jest w dowodach - JEST (w obu cytatach)
        # Nie wykrywa, że w artykule przypisano to Firmie X a w dowodach jest Firma Y
        # To jest znane ograniczenie - walidator nie robi pełnej analizy semantycznej
        length, sources = n.check_draft(draft, facts, docs)
        self.assertGreater(length, 0)
        # Test dokumentuje ograniczenie: walidator nie weryfikuje kontekstu przypisania


class LengthCorrectionTests(unittest.TestCase):
    def test_short_draft_is_rewritten_and_reviewed(self):
        docs, dossier, draft = fixture()
        short = copy.deepcopy(draft)
        for section in short["sections"]:
            section["paragraphs"] = section["paragraphs"][:1]
        ai = RetryTests().client([dossier, short, draft, dict(approved=True, issues=[])])
        entries = []
        with patch.object(n, "collect", return_value=docs):
            n.prepare_article(ai, None, [[dict(title="Test")]], entries)
        self.assertEqual(ai.calls, 5)
        self.assertIn("słów", entries[0]["rewrite"])
        self.assertEqual(entries[0]["status"], "ZATWIERDZONO")

    def test_repeated_length_error_stops_after_one_rewrite(self):
        docs, dossier, draft = fixture()
        for section in draft["sections"]:
            section["paragraphs"] = section["paragraphs"][:1]
        ai = RetryTests().client([dossier, draft, copy.deepcopy(draft)])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "słów"):
                n.prepare_article(ai, None, [[dict(title="Test")]], [])
        self.assertEqual(ai.calls, 4)



class PassageBoundaryTests(unittest.TestCase):
    def test_real_copy_reports_words_and_source(self):
        text = " ".join("slowo" + str(i) for i in range(12))
        result = n.copied_passage([text], [dict(id="S7", text=text)])
        self.assertIn("S7", result)
        self.assertIn(text, result)

    def test_no_match_across_written_passage_boundary(self):
        tokens = ["slowo" + str(i) for i in range(12)]
        self.assertIsNone(n.copied_passage([" ".join(tokens[:6]), " ".join(tokens[6:])],
                                         [dict(id="S1", text=" ".join(tokens))]))

    def test_no_match_across_source_boundary(self):
        tokens = ["slowo" + str(i) for i in range(12)]
        self.assertIsNone(n.copied_passage([" ".join(tokens)],
            [dict(id="S1", text=" ".join(tokens[:6])), dict(id="S2", text=" ".join(tokens[6:]))]))

    def test_case_and_punctuation_do_not_hide_copy(self):
        text = " ".join("slowo" + str(i) for i in range(12))
        self.assertIsNotNone(n.copied_passage([text.upper() + "."], [dict(id="S1", text=text)]))


if __name__ == "__main__":
    unittest.main()


import copy
import unittest
import newsroom as n

class PassageTests(unittest.TestCase):
    def setUp(self):
        self.docs = [{"id":"S1","text":"Pierwszy fakt.  Drugi fakt.\n" + " ".join("wyraz"+str(i) for i in range(110))}]
    def test_exact_and_complete(self):
        before = copy.deepcopy(self.docs)
        data = n.evidence_documents(self.docs)
        self.assertEqual(self.docs, before)
        for passage in data[0]["passages"].values():
            self.assertIn(passage, self.docs[0]["text"])
        self.assertTrue(list(data[0]["passages"].values())[-1].endswith("wyraz109"))
    def test_resolves(self):
        d = {"facts":[{"evidence":[{"source":"S1","passage_id":"P1"}]}]}
        n.resolve_fact_passages(d,self.docs)
        self.assertIn(d["facts"][0]["evidence"][0]["quote"], self.docs[0]["text"])

    def test_resolves_origin_name_and_poll_fields(self):
        text = "Pracownia Przykład opublikowała sondaż, w którym uczestniczyło 600 osób."
        name = {"value": "Pracownia Przykład", "evidence": [{"source": "S1", "passage_id": "P1"}]}
        sample = {"value": "600 osób", "evidence": [{"source": "S1", "passage_id": "P1"}]}
        dossier = {"source_mode": "attributed", "independent": False, "origin": {
            "kind": "poll", "name": name,
            "poll": {"sample": sample, "commissioner": None, "fieldwork": None, "method": None},
        }}
        docs = [{"id": "S1", "text": text}]
        n.resolve_fact_passages(dossier, docs)
        self.assertEqual(n.check_origin(dossier, docs), dossier["origin"])
        self.assertEqual(name["evidence"][0]["quote"], text)
        self.assertEqual(sample["evidence"][0]["quote"], text)
    def test_unknown_source_or_passage(self):
        for sid,pid in [("S2","P1"),("S1","P999"),("S1",[])]:
            with self.subTest(sid=sid,pid=pid), self.assertRaises(n.CorrectableDossier):
                n.resolve_fact_passages({"facts":[{"evidence":[{"source":sid,"passage_id":pid}]}]}, self.docs)
    def test_conflicting_quote_is_not_silently_repaired(self):
        with self.assertRaises(n.CorrectableDossier):
            n.resolve_fact_passages({"facts":[{"evidence":[{"source":"S1","passage_id":"P1","quote":"wymyślony cytat"}]}]}, self.docs)
    def test_legacy_quotes_preserved(self):
        d={"facts":[{"evidence":[{"source":"S1","quote":"stary format"}]}]}
        before=copy.deepcopy(d)
        n.resolve_fact_passages(d,self.docs)
        self.assertEqual(d,before)

    def test_valid_passage_id_without_quote_passes(self):
        """Poprawny passage_id bez quote w facts -> passage zostaje rozwiązany."""
        d = {"facts": [{"evidence": [{"source": "S1", "passage_id": "P1"}]}]}
        n.resolve_fact_passages(d, self.docs)
        self.assertIn("quote", d["facts"][0]["evidence"][0])
        self.assertIsInstance(d["facts"][0]["evidence"][0]["quote"], str)
        self.assertGreater(len(d["facts"][0]["evidence"][0]["quote"]), 0)

    def test_nonexistent_passage_id_rejected(self):
        """Nieistniejący passage_id -> CorrectableDossier."""
        d = {"facts": [{"evidence": [{"source": "S1", "passage_id": "P999"}]}]}
        with self.assertRaises(n.CorrectableDossier):
            n.resolve_fact_passages(d, self.docs)

    def test_unknown_source_in_passage_id_rejected(self):
        """Nieznane źródło w passage_id -> CorrectableDossier."""
        d = {"facts": [{"evidence": [{"source": "S999", "passage_id": "P1"}]}]}
        with self.assertRaises(n.CorrectableDossier):
            n.resolve_fact_passages(d, self.docs)
