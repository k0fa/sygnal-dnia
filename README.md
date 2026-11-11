# Sygnał Dnia — autonomiczny portal RSS + AI

Kompletny projekt Python 3.12 + Gemini API + GitHub Actions + GitHub Pages.
Nie wymaga stale włączonego komputera. Wymaga jednorazowego założenia kont,
utworzenia klucza API i konfiguracji repozytorium. Nie zawiera kluczy ani kont.

## Pliki

- `requirements.txt`: zależności.
- `scraper.py`: RSS, ranking, normalizacja URL, deduplikacja.
- `generator.py`: API Gemini, kontrola odpowiedzi, Markdown + YAML.
- `build.py`: HTML, kategorie, linki do źródeł, Tailwind przez CDN.
- `.github/workflows/publish.yml`: harmonogram, commit/push i wdrożenie Pages.
- `data/history.json`: trwała historia udanych publikacji, początkowo `[]`.
- `.gitignore`: pomija sekrety, kolejkę roboczą i pliki tymczasowe.
- `test_pipeline.py`: testy offline z atrapą API.
- `content/`: tworzony automatycznie, zawiera artykuły Markdown.
- `site/`: tworzony automatycznie, zawiera gotowy HTML.
- `data/queue.json`: robocza kolejka, nie jest commitowana.

## Wdrożenie krok po kroku

1. Utwórz PUBLICZNE repozytorium GitHub z gałęzią `main`.
2. Skopiuj zawartość tego katalogu do głównego katalogu repozytorium, łącznie
   z ukrytym katalogiem `.github`. Plik `scraper.py` ma być w katalogu głównym,
   a workflow dokładnie w `.github/workflows/publish.yml`.
3. W Google AI Studio utwórz klucz dla projektu korzystającego z bezpłatnego
   poziomu API: https://aistudio.google.com/apikey . Nie włączaj płatnego
   rozliczania, jeżeli budżet ma wynosić bezwzględnie 0 zł.
4. GitHub → Settings → Secrets and variables → Actions → New repository secret:
   nazwa `GEMINI_API_KEY`, wartość: Twój klucz. Nie wklejaj go do kodu.
5. Opcjonalna Repository variable: `GEMINI_MODEL`. Domyślnie używany jest
   `gemini-2.5-flash-lite`; jeśli model zniknie lub nie jest dostępny dla konta,
   wybierz dostępny model z darmowym poziomem w AI Studio.
6. Settings → Actions → General → Workflow permissions → Read and write
   permissions. Reguły ochrony `main` muszą zezwalać temu workflow na zapis.
7. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
   Jeśli środowisko `github-pages` wymaga ręcznych zatwierdzeń, usuń ten wymóg,
   aby umożliwić autonomiczne wdrażanie na `main`.
8. Actions → Generate and publish news → Run workflow → main.
9. Po powodzeniu adres strony będzie widoczny w kroku Deploy i Settings → Pages.
   Zwykle: `https://NAZWA_UZYTKOWNIKA.github.io/NAZWA_REPOZYTORIUM/`.

Nie potrzeba PAT ani zewnętrznego serwera. Workflow używa automatycznego
GITHUB_TOKEN. Sam commit wykonany tym tokenem nie jest podstawą wdrożenia:
workflow przesyła artefakt i jawnie uruchamia `actions/deploy-pages`.

## Harmonogram i budżet

Cron `17 */3 * * *` oznacza 00:17, 03:17, 06:17 itd. UTC. W Polsce zimą
są to 01:17, 04:17 itd., latem 02:17, 05:17 itd. GitHub może opóźniać lub
pominąć uruchomienia. Nie jest to gwarantowany zegar czasu rzeczywistego.

Domyślnie maksymalnie 2 tematy na cykl: nominalnie 16 generacji dziennie,
bez dodatkowych ręcznych uruchomień i ponowień błędów serwera. Możesz zmienić
MAX_ARTICLES w YAML (kod dopuszcza 1–5). Błąd 429 zatrzymuje generowanie na ten
cykl. Nie ma przejścia na płatnego dostawcę. Sam kod nie potrafi sprawdzić,
czy właściciel klucza włączył płatny plan — to ustawienie konta Google.

Standardowe runnery Actions są darmowe dla publicznych repozytoriów;
wykorzystaj darmowy adres github.io. Limity Pages, API i przechowywania
artefaktów nadal obowiązują. Artefakt Pages ma retencję jednego dnia.
Z upływem czasu archiwum i historia Git rosną; projekt nie obiecuje
bezterminowo nieograniczonej pojemności. GitHub może wyłączyć harmonogram
publicznego repozytorium po 60 dniach bez aktywności. Udane publikacje tworzą
commity, ale przy długotrwałej awarii należy sprawdzić i ponownie włączyć workflow.

## Uruchomienie lokalne

Python 3.12 lub nowszy. W terminalu w katalogu projektu:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:GEMINI_API_KEY = "TWOJ_KLUCZ"
.venv\Scripts\python scraper.py
.venv\Scripts\python generator.py
.venv\Scripts\python build.py
.venv\Scripts\python -m http.server 8000 --directory site
```

Otwórz http://localhost:8000 . Zatrzymanie: Ctrl+C.
Linux/macOS: zamiast `.venv\Scripts\python` użyj `.venv/bin/python`,
a klucz ustaw przez `export GEMINI_API_KEY='TWOJ_KLUCZ'`.
Skrypt nie wczytuje pliku `.env`; używa zmiennych środowiskowych.

Testy offline: `python -m unittest -v test_pipeline.py`.

## Zasady działania i ograniczenia

RSS jest jedynym materiałem źródłowym. Projekt nie pobiera pełnych stron,
nie omija paywalli i nie tworzy długich analiz z samych nagłówków. Zbyt krótkie
opisy i wpisy bez daty są pomijane. Źródła mogą zmienić URL, blokować roboty
lub przestać publikować RSS; edytuj wtedy listę FEEDS w scraper.py. Niedostępny
pojedynczy kanał nie przerywa pracy. Niedostępność wszystkich kończy cykl
błędem, zachowując już wdrożoną stronę.

Deduplikacja porównuje URL bez parametrów śledzących oraz podobieństwo
nagłówków (próg 0.86). To heurystyka: różne tytuły tego samego wydarzenia
mogą przejść, a aktualizacja o podobnym tytule może zostać pominięta.
Historia nie znika przy nowym uruchomieniu Actions, ponieważ jest commitowana.
Brak udanej generacji nie zapisuje tematu jako opublikowanego.

Prompt ogranicza treść do źródła, a filtr odrzuca wspólne ciągi ośmiu słów.
Nie jest to pełna kontrola plagiatu ani niezależny fact-checking. Model może
błędnie streścić opis lub zmienić liczbę mimo instrukcji. Kod nie gwarantuje
bezbłędności faktów i nie udaje wieloźródłowej weryfikacji.

Zgodnie z wymaganiem zastosowano Tailwind Play CDN. Dokumentacja Tailwind
rekomenduje go do developmentu; dla docelowej produkcji lepiej kompilować
statyczny CSS w buildzie. Podstawowe style awaryjne są osadzone w HTML.

Publikowanie i używanie cudzych feedów wymaga uwzględnienia warunków
udostępnienia danego źródła. Projekt nie zawiera licencji od wydawców.

## Dokumentacja usług

- https://ai.google.dev/gemini-api/docs/pricing
- https://ai.google.dev/api/generate-content
- https://docs.github.com/en/actions/concepts/billing-and-usage
- https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows
- https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages
- https://tailwindcss.com/docs/installation/play-cdn

Pierwsze wdrożenie i prawdziwa generacja wymagają Twojego klucza oraz repozytorium.
