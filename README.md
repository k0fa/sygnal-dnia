# Sygnał Dnia

Polski serwis informacyjny. Pobiera wiadomości z kanałów RSS, opracowuje wybrany temat na podstawie dostępnych źródeł i publikuje statyczną stronę na [GitHub Pages](https://k0fa.github.io/sygnal-dnia/).

## Generowanie i publikacja

Workflow **Generate and publish news** na gałęzi `main` jest zaplanowany co trzy godziny. Można go też uruchomić ręcznie w GitHub Actions. Zmiana kodu na `main` uruchamia testy i wdraża stronę z istniejącymi artykułami. Generowanie nowego materiału odbywa się przy uruchomieniu ręcznym lub zaplanowanym.

GitHub Actions używa sekretu `GEMINI_API_KEY` i opcjonalnej zmiennej repozytorium `GEMINI_MODEL`. Cykl kończy się po zaakceptowaniu jednego artykułu. Jeśli redakcja odrzuci pierwszy temat, generator próbuje opracować kolejny temat z listy w tym samym cyklu. Każdy temat przechodzi pełną kontrolę.

W GitHub Actions domyślny limit to 9 żądań Gemini na cykl, w tym najwyżej 4 na pojedynczy temat. Zmienna repozytorium `GEMINI_MAX_CALLS=5` przywraca niższy limit. Lokalnie domyślnie obowiązuje 5 żądań. Limity konta Google mogą być niższe. Workflow **Gemini - jednorazowy test bez publikacji** sprawdza ten sam proces na aktualnych źródłach bez zapisu artykułu w repozytorium i bez wdrożenia.

## Zasady redakcyjne

Gemini grupuje nagłówki według wydarzeń. Skrypt pobiera materiały z dopuszczonych domen z uwzględnieniem robots.txt i zachowuje tylko źródła z dostępną treścią. Dossier wiąże każdy fakt z fragmentem źródła. Relacje oparte na jednym badaniu, komunikacie lub depeszy wymagają udokumentowanego pochodzenia i jawnej atrybucji. Tematy wysokiego ryzyka wymagają dowodów pierwotnych i dodatkowej kontroli.

Długość zależy od liczby udokumentowanych faktów i ilości dostępnego materiału. Przed zapisem skrypt sprawdza odwołania, liczby i podobieństwo do źródeł, a odrębne wywołanie Gemini ocenia zgodność tekstu z materiałem. Wynik **WSTRZYMANO** oznacza, że cykl nie wygenerował materiału spełniającego wymagania. Uzasadnienie znajduje się w podsumowaniu kroku **Generate articles**. Automatyczna kontrola ma ograniczenia i nie zastępuje weryfikacji redakcyjnej przez człowieka.

## Testy lokalne

```sh
python -m pip install -r requirements.txt
python -m unittest discover -v
python build.py
```

Testy jednostkowe i budowa HTML działają bez klucza Gemini. Gotowa strona znajduje się w `site/index.html`, a archiwum artykułów w `content/`.
