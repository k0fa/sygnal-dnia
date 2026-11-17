"""Offline prompt checks. No Gemini calls or publication.

Verify the base instruction and the one-rewrite path require a complete
fact/evidence check after paraphrasing and do not relax the validator.
"""
import unittest
from unittest.mock import patch

import newsroom as n
from test_pipeline import fixture


class CaptureAI:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.prompts = []
        self.datas = []
        self.calls = 0

    def ask(self, instruction, data, tokens=8192):
        self.calls += 1
        self.prompts.append(instruction)
        self.datas.append(data)
        return next(self.responses)


class WritePromptSafeguardsTests(unittest.TestCase):
    def test_write_requires_date_evidence_for_each_fragment(self):
        prompt = n.WRITE
        for phrase in (
            "KONTROLA DAT I DOWODÓW",
            "title_facts", "summary_facts", "paragraph.facts",
            "Nie przenoś daty publikacji", "publish=false",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, prompt)

    def test_write_requires_whole_article_reaudit(self):
        prompt = n.WRITE
        for phrase in (
            "OSTATECZNA KONTROLA CAŁEGO TEKSTU",
            "Wszystkie", "jednostki", "waluty", "punkty procentowe",
            "identyfikatory faktów", "12 kolejnych słów",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.lower(), prompt.lower())

    def test_initial_draft_receives_date_and_copy_safeguards(self):
        docs, dossier, draft = fixture()
        ai = CaptureAI([draft])
        with patch.object(n, "check_draft", return_value=(200, {"S1", "S2"})):
            result, length, used = n.checked_draft(ai, dossier, {}, docs, {}, {})
        self.assertIs(result, draft)
        self.assertEqual(ai.calls, 1)
        self.assertIn("KONTROLA DAT I DOWODÓW", ai.prompts[0])
        self.assertIn("KONTROLA ORYGINALNOŚCI", ai.prompts[0])

    def test_rewrite_rechecks_every_claim_even_after_copy_error(self):
        docs, dossier, draft = fixture()
        ai = CaptureAI([draft, draft])
        with patch.object(n, "check_draft", side_effect=[
            n.CopiedPassage("Powielony fragment"), (200, {"S1", "S2"})
        ]) as validator:
            n.checked_draft(ai, dossier, {}, docs, {}, {})
        self.assertEqual(ai.calls, 2)
        self.assertEqual(validator.call_count, 2)
        prompt = ai.prompts[1]
        for phrase in (
            "PONOWNĄ KONTROLĘ CAŁEGO", "title, summary",
            "fact_id", "evidence", "12 kolejnych słów",
            "publish=false",
        ):
            self.assertIn(phrase, prompt)
        self.assertIn("Powielony fragment", ai.datas[1]["validation_error"])
        self.assertEqual(ai.datas[1]["documents"], docs)

    def test_budget_still_prevents_unreviewed_rewrite(self):
        docs, dossier, draft = fixture()
        ai = CaptureAI([draft])
        ai.calls = 3
        with patch.object(n, "check_draft", side_effect=n.CopiedPassage("Powielony fragment")):
            with self.assertRaises(n.CopiedPassage):
                n.checked_draft(ai, dossier, {}, docs, {}, {})
        self.assertEqual(ai.calls, 4)
        self.assertEqual(len(ai.prompts), 1)


if __name__ == "__main__":
    unittest.main()
