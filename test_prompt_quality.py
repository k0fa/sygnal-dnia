"""Regresja: instrukcje modelu i ostre bramki redakcyjne Hermesa."""
import unittest
import newsroom as n


class PromptQualityTests(unittest.TestCase):
    def test_initial_write_warns_about_12_word_sequences(self):
        self.assertIn("12 kolejnych słów", n.WRITE)
        self.assertIn("publish=false", n.WRITE)

    def test_extract_requires_same_primary_source_as_fact(self):
        self.assertIn("supports_facts", n.EXTRACT)
        self.assertIn("TEN SAM source", n.EXTRACT)
        self.assertIn("ready=false", n.EXTRACT)

    def test_copied_passage_guard_is_still_enforced(self):
        words = ["slowo" + str(i) for i in range(12)]
        phrase = " ".join(words)
        self.assertIsNotNone(n.copied_passage(
            [phrase], [{"id": "S1", "text": "Kontekst " + phrase}]))


if __name__ == "__main__":
    unittest.main()
