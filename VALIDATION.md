# Sprawdzenie aktualizacji v2

- Składnia pięciu plików Python sprawdzona kompilacją.
- Lokalnie Python 3.15: 17 testów, 14 zaliczonych i 3 pominięte.
- Trzy testy integracyjne wymagają requests, feedparser, beautifulsoup4, PyYAML, Markdown i bleach z istniejącego requirements.txt. Nie były dostępne w lokalnym środowisku; testy nie zostały przedstawione jako zaliczone.
- GitHub Actions instaluje zależności i uruchamia ten sam test_pipeline.py; dopiero jego wynik potwierdzi integrację w środowisku użytkownika.
- Testy jednostkowe obejmują m.in. cytaty źródłowe, wymagane źródło urzędowe dla tematów wysokiego ryzyka, przypisy, długość tekstu, nowe liczby, dosłowne kopiowanie, HTML, dozwolone adresy i prywatne IP, robots.txt, HTTP 404 i limit wywołań.
- Testy integracyjne używają atrapy API, nie płatnych zapytań.
- Nie wykonano rzeczywistego wywołania Gemini z kluczem użytkownika ani wdrożenia do jego repozytorium.
- Paczka aktualizuje wyłącznie kod i dokumentację. Nie zawiera kluczy, artykułów ani historii użytkownika.
