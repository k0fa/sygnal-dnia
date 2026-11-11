import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build
import generator
import scraper


class PipelineTests(unittest.TestCase):
    def test_pipeline_recovery_and_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, content = root / "data", root / "content"
            data.mkdir()
            content.mkdir()
            item = dict(id="abc123", url="https://example.org/news", title="Testowy komunikat o rozwoju kolei",
                        summary="Opis materiału wejściowego zawiera informacje o rozbudowie linii kolejowej.",
                        source="Test RSS", published="2026-11-10T10:00:00+00:00", category="Wiadomości")
            result = dict(publish=True, title='Nowa informacja: kolej <img src=x onerror=alert(1)>',
                          summary="Podsumowanie testowego materiału opracowanego na podstawie danych.",
                          body="Treść testowa do sprawdzenia działania całego procesu publikacji. " * 4 + '<script>alert(1)</script>')
            scraper.save_json(data / "queue.json", [item])
            with patch.object(scraper, "DATA", data), patch.object(scraper, "CONTENT", content), \
                 patch.object(generator, "DATA", data), patch.object(generator, "CONTENT", content), \
                 patch.object(build, "CONTENT", content), patch.object(build, "OUT", root / "site"), \
                 patch.dict(os.environ, {"GEMINI_API_KEY": "mock", "MAX_ARTICLES": "2"}), \
                 patch.object(generator, "generate", return_value=result) as api:
                generator.main()
                self.assertEqual(len(list(content.glob("*.md"))), 1)
                # Simulate loss of history after Markdown was written.
                (data / "history.json").unlink()
                generator.main()
                self.assertEqual(api.call_count, 1)
                build.main()
                rendered = (root / "site/articles/abc123.html").read_text(encoding="utf-8")
                self.assertNotIn("<img", rendered)
                self.assertNotIn("<script>alert", rendered)
                self.assertIn('../index.html', rendered)
                self.assertIn('https://example.org/news', rendered)

    def test_dedup_and_copy_detection(self):
        url = scraper.canonical("https://example.org/a?id=3&utm_source=rss#x")
        self.assertEqual(url, "https://example.org/a?id=3")
        self.assertTrue(scraper.duplicate(dict(url=url, title="Rząd ogłosił ważne zmiany"),
                                         [dict(url="https://other.org/a", title="Rząd ogłosił ważne zmiany!")]))
        result = dict(publish=True, title="Wiadomość o zmianach gospodarczych",
                      summary="Bardzo długie podsumowanie wydarzenia gospodarczego.",
                      body="To jest dokładnie ten sam długi fragment tekstu źródłowego. " * 5)
        with self.assertRaises(ValueError):
            generator.validate(result, dict(title="Nagłówek", summary=result["body"]))

    def test_quota_does_not_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            content = Path(tmp) / "content"
            scraper.save_json(data / "queue.json", [dict(id="x", title="Test", url="https://example.org")])
            with patch.object(generator, "DATA", data), patch.object(generator, "CONTENT", content), \
                 patch.object(generator, "history", return_value=[]), \
                 patch.dict(os.environ, {"GEMINI_API_KEY": "mock"}), \
                 patch.object(generator, "generate", side_effect=generator.StopAPI("HTTP 429")):
                generator.main()
                self.assertEqual(list(content.glob("*.md")), [])
                self.assertFalse((data / "history.json").exists())


if __name__ == "__main__":
    unittest.main()
