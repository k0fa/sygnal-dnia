# Zakres weryfikacji

Sprawdzono składnię plików Python przez `python -m compileall -q portal`.
Kontrola przeszła pomyślnie w środowisku przygotowania pakietu.

Dołączono testy offline w test_pipeline.py. Workflow uruchamia je przed pobraniem
RSS i wdrożeniem. Nie udało się uruchomić ich lokalnie: środowisko nie zawierało
zależności, a polityka sieciowa blokowała pobieranie pakietów z PyPI.
Nie wykonano prawdziwego wywołania Gemini ani publikacji GitHub Pages:
nie podano klucza API ani repozytorium.

Adresy RSS są jawnie konfigurowalne. Dostępność wszystkich pięciu kanałów
z runnera GitHub wymaga potwierdzenia podczas pierwszego uruchomienia.
Kod raportuje liczbę działających kanałów i pomija kanały niedostępne.
