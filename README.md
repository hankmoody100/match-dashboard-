# Tendenz-Board Fußball

Dashboard für Tendenzen (Über/Unter, Beide treffen, Ø Tore, Ecken, Karten) und den Abgleich mit Quoten.
Datenquelle: football-data.co.uk, 12 Ligen, aktuelle Saison plus zwei Vorsaisonen.

## Einrichten (einmalig)
1. Neues Repository auf GitHub anlegen, Sichtbarkeit "Public".
2. Alle Dateien aus diesem Ordner hochladen (inkl. `.github/workflows/update-data.yml`).
3. Settings → Actions → General → Workflow permissions: "Read and write permissions" aktivieren.
4. Actions → "Daten aktualisieren" → "Run workflow". Nach 1–2 Minuten liegt `data/football.json` im Repo.
5. Settings → Pages → Source: "Deploy from a branch", Branch `main`, Ordner `/ (root)`.
   Nach kurzer Zeit ist das Dashboard unter `https://<benutzername>.github.io/<repo>/` erreichbar.

Danach aktualisiert sich alles zweimal täglich automatisch.

## Ligen ändern
In `scripts/fetch_football.py` die Liste `LEAGUES` anpassen (Kürzel wie auf football-data.co.uk).
