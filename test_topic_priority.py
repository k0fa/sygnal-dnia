"""Offline regressions for deterministic topic scheduling before any Gemini research."""
import importlib.util
import unittest
from unittest.mock import MagicMock

from topic_priority import rank_topics, topic_priority_key


def group(url, title, summary=""):
    return [dict(url=url, title=title, summary=summary)]


def publisher(url):
    from urllib.parse import urlsplit
    host = (urlsplit(url).hostname or "").casefold()
    for name in ("onet.pl", "wp.pl", "interia.pl", "nbp.pl", "gov.pl"):
        if host == name or host.endswith("." + name):
            return name
    return host


class PriorityHeuristicTests(unittest.TestCase):
    def test_low_risk_first_even_if_critical_has_two_publishers(self):
        accident = group("https://onet.pl/a", "Zderzenie pociągów, wielu rannych")
        accident += group("https://wp.pl/a", "Katastrofa kolejowa")
        data = group("https://interia.pl/b", "Dane NBP o inflacji bazowej")
        self.assertEqual(rank_topics([accident, data], publisher), [data, accident])

    def test_two_publishers_preferred_for_equal_risk(self):
        single = group("https://interia.pl/1", "Program rozbudowy magazynów ciepła")
        multi = group("https://onet.pl/2", "Program rozbudowy magazynów ciepła")
        multi += group("https://wp.pl/2", "Program rozbudowy magazynów ciepła")
        self.assertEqual(rank_topics([single, multi], publisher), [multi, single])

    def test_same_publisher_not_counted_twice(self):
        first = group("https://biznes.interia.pl/a", "Program magazynowania ciepła")
        first += group("https://geekweek.interia.pl/b", "Program magazynowania ciepła")
        second = group("https://onet.pl/a", "Program magazynowania ciepła")
        second += group("https://wp.pl/a", "Program magazynowania ciepła")
        # Real source_group collapses subdomains under the same publisher.
        self.assertEqual(rank_topics([first, second], publisher)[0], second)

    def test_official_url_is_hint_and_does_not_remove_other_groups(self):
        other = group("https://interia.pl/a", "Raport o energii")
        official = group("https://nbp.pl/komunikat", "Dane o inflacji")
        result = rank_topics([other, official], publisher)
        self.assertEqual(result, [official, other])
        self.assertEqual(len(result), 2)

    def test_all_high_risk_candidates_remain_when_only_option(self):
        a = group("https://onet.pl/a", "Wypadek kolejowy")
        b = group("https://interia.pl/b", "Zarzuty karne")
        ranked = rank_topics([a, b], publisher)
        self.assertEqual(ranked, [a, b])
        self.assertEqual(len(ranked), 2)

    def test_stable_order_on_ties_and_no_mutation(self):
        original = [group("https://onet.pl/a", "Robot do badań kosmosu"),
                    group("https://onet.pl/b", "Technologia magazynowania ciepła")]
        ranked = rank_topics(original, publisher)
        self.assertEqual(ranked, original)
        self.assertIsNot(ranked, original)

    def test_document_hint_is_weak_tiebreaker(self):
        neutral = group("https://interia.pl/a", "Nowa technologia")
        documentary = group("https://interia.pl/b", "NBP publikuje dane o inflacji")
        self.assertEqual(rank_topics([neutral, documentary], publisher),
                         [documentary, neutral])

    def test_bad_host_like_official_domain_is_not_official(self):
        evil = group("https://nbp.pl.example.org/x", "Technologia")
        official = group("https://nbp.pl/x", "Technologia")
        self.assertLess(topic_priority_key(official, publisher),
                        topic_priority_key(evil, publisher))


@unittest.skipUnless(importlib.util.find_spec("yaml"), "requires PyYAML")
class SelectTopicsIntegrationTests(unittest.TestCase):
    def test_reordered_validated_topics_and_one_api_request(self):
        import generator
        urls = ["https://onet.pl/1", "https://wp.pl/2", "https://interia.pl/3",
                "https://onet.pl/4", "https://wp.pl/5"]
        titles = ["Zderzenie pociągów", "Katastrofa kolejowa",
                  "Dane NBP o inflacji", "Nowa technologia", "Nowa technologia"]
        queue = [dict(id=f"hash{i}", title=title, summary="Krótki opis.",
                      source="RSS", published="2026-11-17T10:00:00+00:00",
                      category="Wiadomości", url=url)
                 for i, (title, url) in enumerate(zip(titles, urls), 1)]
        ai = MagicMock()
        ai.ask.return_value = {"topics": [
            {"label": "Wypadek", "ids": ["N1", "N2"]},
            {"label": "Dane NBP", "ids": ["N3"]},
            {"label": "Technologia", "ids": ["N4", "N5"]},
        ]}
        audit = []
        result = generator.select_topics(ai, queue, [], audit)
        self.assertEqual(len(result), 3)
        self.assertEqual([item[0]["id"] for item in result], ["hash4", "hash3", "hash1"])
        self.assertEqual(ai.ask.call_count, 1)
        self.assertTrue(any("heurystyce" in line for line in audit))


if __name__ == "__main__":
    unittest.main()
