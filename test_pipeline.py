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
        for total, count, low, high in [(400, 6, 180, 300),
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
                '<article><a href="https://www.cbos.pl/raport-test">Raport źródłowy</a>'
                '<a href="https://wiadomosci.onet.pl/wywiad-test">Oryginalny wywiad</a>'
                '<a href="https://unknown.invalid/report">Raport</a>'
                '<a href="http://127.0.0.1/report">Źródło</a></article>')
        links = n.research_links(html, "https://onet.pl/test")
        self.assertEqual(len(links), 2)
        self.assertIn("https://www.cbos.pl/raport-test", links)
        self.assertIn("https://wiadomosci.onet.pl/wywiad-test", links)

    def test_risk_explanation_survives_rejection_summary(self):
        docs, dossier, _ = fixture()
        dossier.update(high_risk=True, risk_categories=["casualties"],
                       risk_reason="Brak niezależnego potwierdzenia liczby ofiar.",
                       risk_fact_ids=["F1"])
        ai = RetryTests().client([dossier])
        with patch.object(n, "collect", return_value=docs):
            with self.assertRaisesRegex(n.Rejected, "liczby ofiar"):
                n.prepare_article(ai, None, [[dict(title="Temat")]], [])


if __name__ == "__main__":
    unittest.main()
