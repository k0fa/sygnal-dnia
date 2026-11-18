"""Regresja: instrukcje modelu i ostre bramki redakcyjne Hermesa."""
import importlib.util
import unittest
import newsroom as n
from unittest.mock import MagicMock

# Generator requires pyyaml; skip if not available
GENERATOR_AVAILABLE = importlib.util.find_spec("yaml") is not None
if GENERATOR_AVAILABLE:
    import generator as g


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


@unittest.skipUnless(GENERATOR_AVAILABLE, "generator requires pyyaml")
class SelectTopicsTests(unittest.TestCase):
    """Testy wyboru tematów: limit 6 grup, walidacja, deduplikacja."""

    def make_candidates(self, count=10):
        """Generuje kandydatów z różnymi publisher_group."""
        sources = ["onet.pl", "wp.pl", "interia.pl", "biznes.interia.pl", "geekweek.interia.pl"]
        candidates = []
        for i in range(count):
            src = sources[i % len(sources)]
            candidates.append({
                "id": f"hash{i}",
                "title": f"Temat numer {i}",
                "summary": f"Opis wydarzenia {i} od {src}.",
                "source": src.replace(".pl", "").capitalize(),
                "published": "2026-11-16T10:00:00+00:00",
                "url": f"https://{src}/test{i}",
                "category": "Wiadomości"
            })
        return candidates

    def test_select_instruction_allows_six_topics(self):
        """Instrukcja SELECT pozwala na 6 tematów."""
        self.assertIn("maksymalnie 6", n.SELECT)
        self.assertNotIn("maksymalnie 3", n.SELECT)

    def test_select_instruction_prefers_two_publisher_groups(self):
        """Instrukcja SELECT preferuje co najmniej dwa publisher_group."""
        self.assertIn("co najmniej dwa publisher_group", n.SELECT)

    def test_select_topics_accepts_up_to_six_valid_groups(self):
        """select_topics() akceptuje do 6 poprawnych grup."""
        candidates = self.make_candidates(12)
        ai = MagicMock()
        # Zwróć 6 grup z różnymi publisher_group
        response = {
            "topics": [
                {"label": f"Wydarzenie {i}", "ids": [f"N{i*2+1}", f"N{i*2+2}"]}
                for i in range(6)
            ]
        }
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, candidates, [], audit)
        self.assertEqual(len(result), 6)
        self.assertEqual(ai.ask.call_count, 1)

    def test_select_topics_rejects_seventh_group(self):
        """Siódma grupa jest odrzucana (limit 6)."""
        candidates = self.make_candidates(14)
        ai = MagicMock()
        # Zwróć 7 grup
        response = {
            "topics": [
                {"label": f"Wydarzenie {i}", "ids": [f"N{i*2+1}", f"N{i*2+2}"]}
                for i in range(7)
            ]
        }
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, candidates, [], audit)
        self.assertEqual(len(result), 6)
        # Sprawdź log o pominięciu 7. grupy
        audit_text = " ".join(audit)
        self.assertEqual(len(result), 6)

    def test_select_topics_validates_ids(self):
        """Nieznane identyfikatory są odrzucane."""
        candidates = self.make_candidates(4)
        ai = MagicMock()
        response = {
            "topics": [
                {"label": "Wydarzenie", "ids": ["N1", "N999"]}  # N999 nie istnieje
            ]
        }
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, candidates, [], audit)
        self.assertEqual(len(result), 0)
        audit_text = " ".join(audit)
        self.assertIn("nieznane identyfikatory", audit_text)

    def test_select_topics_rejects_duplicate_ids(self):
        """Powtórzone identyfikatory w grupie są odrzucane."""
        candidates = self.make_candidates(4)
        ai = MagicMock()
        response = {
            "topics": [
                {"label": "Wydarzenie", "ids": ["N1", "N1"]}
            ]
        }
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, candidates, [], audit)
        self.assertEqual(len(result), 0)
        audit_text = " ".join(audit)
        self.assertIn("powtórzone identyfikatory", audit_text)

    def test_select_topics_allows_single_publisher_group(self):
        """Grupa z jednym publisher_group jest odrzucana lub oznaczana."""
        # Tylko jeden publisher (wszystkie z onet.pl)
        candidates = [{
            "id": f"hash{i}",
            "title": f"Temat {i}",
            "summary": "Opis " * 20,
            "source": "Onet",
            "published": "2026-11-16T10:00:00+00:00",
            "url": f"https://wiadomosci.onet.pl/test{i}",
            "category": "Wiadomości"
        } for i in range(4)]
        ai = MagicMock()
        response = {
            "topics": [
                {"label": "Wydarzenie", "ids": ["N1", "N2", "N3", "N4"]}
            ]
        }
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, candidates, [], audit)
        # Grupa jest odrzucana, bo ma <2 publisher_group
        self.assertEqual(len(result), 1)
        audit_text = " ".join(audit)
        self.assertIn("jedna grupa wydawnicza", audit_text)

    def test_select_topics_deduplicates_same_signature(self):
        """Grupy z tymi samymi ID (inną kolejnością) są deduplikowane."""
        candidates = self.make_candidates(4)
        ai = MagicMock()
        response = {
            "topics": [
                {"label": "Wydarzenie 1", "ids": ["N1", "N2"]},
                {"label": "Wydarzenie 2", "ids": ["N2", "N1"]},  # ta sama sygnatura
            ]
        }
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, candidates, [], audit)
        self.assertEqual(len(result), 1)
        audit_text = " ".join(audit)
        self.assertIn("pominięta — powtórzona grupa", audit_text)

    def test_select_topics_rejects_already_published(self):
        """Już opublikowane tematy są odrzucane."""
        candidates = self.make_candidates(4)
        # Symuluj historię z jednym z URL-i
        records = [candidates[0]]
        ai = MagicMock()
        response = {
            "topics": [
                {"label": "Wydarzenie", "ids": ["N1", "N2"]}
            ]
        }
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, candidates, records, audit)
        self.assertEqual(len(result), 0)
        audit_text = " ".join(audit)
        self.assertIn("już opublikowany", audit_text)

    def test_select_topics_empty_response(self):
        """Pusta lista topics zwraca pusty wynik z uzasadnieniem."""
        candidates = self.make_candidates(4)
        ai = MagicMock()
        response = {"topics": [], "reason": "Brak pasujących grup"}
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, candidates, [], audit)
        self.assertEqual(len(result), 0)
        audit_text = " ".join(audit)
        self.assertIn("Brak pasujących grup", audit_text)

    def test_select_topics_invalid_format_rejected(self):
        """Niepoprawny format grupy jest odrzucany."""
        candidates = self.make_candidates(4)
        ai = MagicMock()
        response = {
            "topics": [
                None,  # niepoprawny
                {"label": "OK", "ids": ["N1", "N2"]}
            ]
        }
        ai.ask.return_value = response
        audit = []
        result = g.select_topics(ai, candidates, [], audit)
        self.assertEqual(len(result), 1)
        audit_text = " ".join(audit)
        self.assertIn("odrzucona — niepoprawny format grupy", audit_text)


if __name__ == "__main__":
    unittest.main()
