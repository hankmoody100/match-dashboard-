# Tendenz-Board Fußball

Simulation, Value-Vergleich, Quotenverlauf, ClubElo-Gegencheck, Schlagzeilen je Team,
Wetter, Modell-Check (Backtest) und ein Tipp-Protokoll mit Monatslimit.

## Wie es läuft
- GitHub Actions ruft alle 20 Minuten Daten ab (`scripts/fetch_football.py`).
- Jede Quelle hat ihren eigenen Takt: Quoten jeder Lauf, Ergebnisse und News alle 3 Std., ClubElo alle 2 Std.
- Ergebnis landet auf dem Zweig `gh-pages` (wird jedes Mal ersetzt, das Repo wächst nicht).
- GitHub Pages zeigt den Zweig `gh-pages`. Die Seite lädt sich alle 10 Minuten selbst neu.

## Namen zuordnen
Taucht ein Team bei ClubElo, News oder Wetter nicht auf, in `scripts/aliases.json` ergänzen:
`"Name wie im Board": {"search": "Name für News", "elo": "Name bei clubelo.com", "city": "Stadt"}`.
Offene ClubElo-Zuordnungen stehen in `data/elo.json` unter `unmatched` (auf dem Zweig gh-pages).

## Modell (scripts/model.py)
Rating-Modell mit Angriffs- und Abwehrwert je Team, gegnerbereinigt und zeitgewichtet.
Grundlage ist eine Mischung aus Toren und xG (Understat für die großen fünf Ligen,
sonst aus Schüssen geschätzt, siehe scripts/understat.py), dazu die Dixon-Coles-Remis-Korrektur.
Halbwertszeit, xG-Anteil und Korrektur werden einmal am Tag automatisch neu bestimmt.

## Quellen
football-data.co.uk, understat.com, clubelo.com, Google News (RSS), Open-Meteo.

## Tennis (scripts/tennis.py)
Umschalter ganz oben auf der Seite. Modell: Belag-Elo + Aufschlag-Return-Modell, Simulation Spiel für Spiel.
Quellen: Sackmann-Archiv (Aufschlagstatistik, Stand Mai 2026), TML-Database, tennis-data.co.uk (Ergebnisse, Quoten).
Kommende Matches mit Quoten: kostenloser Key von the-odds-api.com als GitHub-Secret `ODDS_API_KEY`
(Settings → Secrets and variables → Actions). Mit Key werden auch neue Ergebnisse gesammelt, damit die Elo-Werte aktuell bleiben.
