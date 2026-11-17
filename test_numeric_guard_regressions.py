"""Targeted offline regression tests for unit and date claims in Hermes.

Place next to newsroom.py and test_pipeline.py. No API requests or publication.
"""
import unittest
import newsroom as n
from test_pipeline import fixture


class NumericGuardRegressions(unittest.TestCase):
    def check_case(self, source_claim: str, draft_claim: str):
        docs, dossier, draft = fixture()
        supporting_quote = (
            "W raporcie opisano następującą informację. "
            + source_claim + " Dane przekazano redakcji do publikacji."
        )
        docs[0]["text"] += " " + supporting_quote
        dossier["facts"][0]["evidence"][0]["quote"] = supporting_quote
        facts = n.check_dossier(dossier, docs)
        draft["sections"][0]["paragraphs"][0]["text"] += " " + draft_claim
        return n.check_draft(draft, facts, docs)

    def test_same_date_without_r_passes(self):
        self.check_case(
            "Spotkanie odbyło się 16 listopada 2026.",
            "Spotkanie odbyło się 16 listopada 2026."
        )

    def test_same_date_with_r_passes(self):
        self.check_case(
            "Spotkanie odbyło się 16 listopada 2026 r.",
            "Spotkanie odbyło się 16 listopada 2026 r."
        )

    def test_percent_cannot_replace_percentage_points(self):
        with self.assertRaises(n.UnsupportedNumber):
            self.check_case(
                "Wskaźnik wzrósł o 10 p.p.",
                "Wskaźnik wzrósł o 10 procent."
            )

    def test_full_word_amount_cannot_change_currency(self):
        with self.assertRaises(n.UnsupportedNumber):
            self.check_case(
                "Budżet wyniósł 50 mln zł.",
                "Budżet wyniósł 50 milionów USD."
            )

    def test_tourists_not_confused_with_tonnes(self):
        self.check_case(
            "Na miejsce przyjechało 10 turystów.",
            "Na miejsce przyjechało 10 turystów."
        )

    def test_abbreviated_currency_change_rejected(self):
        with self.assertRaises(n.UnsupportedNumber):
            self.check_case(
                "Budżet wyniósł 50 mln zł.",
                "Budżet wyniósł 50 mln USD."
            )

    def test_identical_percent_passes(self):
        self.check_case(
            "Wskaźnik wzrósł o 10%.",
            "Wskaźnik wzrósł o 10%."
        )

    def test_identical_amount_passes(self):
        self.check_case(
            "Budżet wyniósł 1,5 mln zł.",
            "Budżet wyniósł 1,5 mln zł."
        )


class ExactEvidenceQuoteRegressions(unittest.TestCase):
    def test_exact_citation_accepted(self):
        source = "W raporcie podano, że wynik wyniósł 50 mln zł, a nie 50 mld zł. Komunikat zakończono."
        quote = "W raporcie podano, że wynik wyniósł 50 mln zł, a nie 50 mld zł."
        self.assertEqual(n.evidence_quote(quote, source, "test"), quote)

    def test_similar_but_falsified_citation_rejected(self):
        source = "W raporcie podano, że wynik wyniósł 50 mln zł, a nie 50 mld zł. Komunikat zakończono."
        falsified = "W raporcie podano, że wynik wyniósł 50 mln USD, a nie 50 mld zł."
        with self.assertRaises(n.CorrectableDossier):
            n.evidence_quote(falsified, source, "test")


if __name__ == "__main__":
    unittest.main()
