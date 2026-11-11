# Sygnał Dnia — aktualizacja v2

To paczka aktualizacyjna dla istniejącego projektu k0fa/sygnal-dnia. Nie jest samodzielną instalacją od zera.

## Wgranie krok po kroku
1. Rozpakuj ZIP na komputerze.
2. Otwórz https://github.com/k0fa/sygnal-dnia i wybierz Add file → Upload files.
3. Przeciągnij wszystkie 7 plików z rozpakowanej paczki bezpośrednio do repozytorium. Nie przeciągaj zewnętrznego folderu. Pliki generator.py, scraper.py, build.py i test_pipeline.py mają zastąpić poprzednie; newsroom.py jest nowy.
4. Kliknij Commit changes. Nie usuwaj folderów .github, data, content ani site. Ta paczka nie nadpisuje historii artykułów.
5. Settings → Secrets and variables → Actions → Variables. Utwórz lub edytuj Repository variable o nazwie GEMINI_MODEL i wartości gemini-3.5-flash-lite. Jeżeli Google nie udostępnia tego modelu Twojemu projektowi, wybierz dostępny model generowania tekstu obsługujący JSON z bezpłatnego poziomu. Nazwa musi być dokładnym identyfikatorem API.
6. W zakładce Secrets pozostaw istniejący GEMINI_API_KEY. Klucz nie może znaleźć się w kodzie ani publicznym repozytorium.
7. Poczekaj na zakończenie uruchomienia wywołanego zmianą kodu. Potem Actions → workflow publikacji → Run workflow → main → Run workflow. Dotychczasowy workflow na push tylko buduje stronę; generowanie jest uruchamiane ręcznie lub harmonogramem.
8. Otwórz podsumowanie uruchomienia i krok Generate articles. Po publikacji odwiedź https://k0fa.github.io/sygnal-dnia/ i odśwież stronę.

Dotychczasowe requirements.txt i .github/workflows/publish.yml pozostają bez zmian. Workflow powinien uruchamiać kolejno test_pipeline.py, scraper.py, generator.py i build.py, zapisywać content oraz data/history.json i wdrażać site na Pages.

## Co robi nowa wersja
- Pobiera aktualne nagłówki i opisy RSS z istniejących pięciu kanałów.
- Gemini wybiera wspólny temat opisany przez co najmniej dwie grupy wydawców.
- Skrypt pobiera dostępne teksty ze wskazanych stron, przestrzegając robots.txt. Nie omija płatnego dostępu.
- Może dołączyć dokumenty urzędowe znalezione w rzeczywistych odnośnikach w tych tekstach.
- Gemini przygotowuje zestaw faktów z fragmentami źródeł. Skrypt sprawdza obecność tych fragmentów.
- Powstaje artykuł po polsku: 600–1000 słów tekstu głównego, nagłówek, podsumowanie, śródtytuły i odnośniki do źródeł.
- Oddzielne wywołanie modelu ocenia zgodność artykułu z materiałami. Tematy oznaczone przez model jako wysokiego ryzyka wymagają źródła urzędowego i dodatkowej oceny.
- Zapis następuje dopiero po przejściu kontroli. Historia deduplikacji obejmuje źródła użytego tematu.

To nie jest samodzielne przeszukiwanie całego internetu przez Google Search. RSS dostarcza kandydatów; Gemini wybiera temat, opracowuje fakty, pisze i sprawdza artykuł. Nie są włączane płatne narzędzia wyszukiwania.

## Limity i koszty
Maksymalnie jeden artykuł na uruchomienie; ustawienie MAX_ARTICLES ze starego workflow nie zwiększa tego limitu. Typowo cztery, maksymalnie pięć wywołań Gemini na uruchomienie. Przy ośmiu cyklach na dobę to maksymalnie 40 wywołań, plus uruchomienia ręczne. Limity konta mogą być niższe.

Brak wystarczających materiałów oznacza brak artykułu w danym cyklu, a nie sztuczne wydłużanie tekstu. Wymagane są co najmniej dwie grupy wydawców i 1600 słów materiału źródłowego.

Kod nie przełącza się automatycznie na płatne API. Aby zachować budżet 0 zł, używaj projektu Google bez włączonego płatnego rozliczania i modelu dostępnego w jego bezpłatnym poziomie. Sam kod nie może sprawdzić ani zagwarantować stanu rozliczeń Twojego konta. Dostępność modeli i limity mogą się zmieniać.

## Jak odczytywać wynik
- OPUBLIKOWANO: zapisano artykuł; dalsze kroki publikują stronę.
- WSTRZYMANO: za mało danych lub kontrola jakości odrzuciła materiał. Zadanie może być zielone, ale nie ma nowego artykułu.
- BRAK NOWYCH TEMATÓW lub podobny komunikat: deduplikacja wykluczyła powtórki.
- BŁĄD API: zadanie jest czerwone. HTTP 404 zwykle oznacza nieprawidłowy lub niedostępny model/endpoint; sprawdź GEMINI_MODEL. HTTP 429 oznacza limit; skrypt nie ponawia połączeń bez końca.
- BŁĄD DANYCH: odpowiedź nie odpowiada oczekiwanemu formatowi; sprawdź log kroku.

Dostęp stron może być blokowany przez robots.txt, zapory lub zmianę układu HTML. Nie wszystkie tematy przejdą selekcję. Lista dozwolonych domen jest w newsroom.py.

## Granice kontroli
Automatyczna ocena przez model nie jest niezależnym fact-checkingiem człowieka. Dwie domeny nie gwarantują dwóch niezależnych informacji. Skrypt grupuje wydawców i odrzuca silnie podobne teksty, a model ocenia niezależność, lecz może się pomylić. Sprawdzanie liczb i dosłownych fragmentów również nie wykrywa wszystkich błędów. Przypisy pokazują materiały wykorzystane do opracowania tekstu, a nie gwarancję prawdziwości każdej tezy.

## Testy
Dotychczasowy workflow uruchamia python -m unittest -v test_pipeline.py. Nowe testy obejmują również odrzucenie błędów API, brak publikacji po negatywnej ocenie, odzyskanie historii oraz budowę przypisów. Szczegóły lokalnego sprawdzenia: VALIDATION.md.
