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

## Länderspiele (scripts/international.py)
Reiter „Länderspiele“ im Fußball-Bereich. Elo aus allen Länderspielen seit 1872 (martj42/international_results),
kommende Spiele über The Odds API (Spielplan-Abfrage kostet keine Abrufe).

## Tennis, alle Turnierstufen
Spielplan zusätzlich aus Sofascore und Tennis Explorer (inkl. Challenger und ITF). Datenlage je Match:
●●● Aufschlag + Elo, ●●○ Elo, ●○○ Schätzung über Weltrangliste, ○○○ kaum Daten (Buchmacher-Tendenz oder 50/50).
Beide Seiten können Abrufe von GitHub-Servern blockieren; dann läuft das Board mit den übrigen Quellen weiter.

## Alle Spiele weltweit (scripts/allgames.py)
Reiter „Alle Spiele“: kompletter Spielplan aus Sofascore (alle Länder, Ligen, Pokale, Länderspiele) plus ESPN für Länderspiele.
Datenlage: ●●● tiefes Liga-Modell, ●●○ Länder-Elo oder ClubElo, ●○○ eigene Elo aus täglich gesammelten Ergebnissen, ○○○ keine Daten.
Die gesammelten Ergebnisse liegen in data/sf_results.json (2 Jahre rollierend) und machen die eigene Elo mit jedem Tag besser.

## Spieldetails für alle Spiele
Für Spiele der nächsten 36 Stunden (max. 120 Abrufe je Lauf): Form und Tabellenplatz, Serien/Tendenzen,
gemeldete Ausfälle (verletzt, gesperrt, fraglich) und ab 90 Minuten vor Anpfiff die bestätigte Aufstellung (Sofascore).
Top-Ligen zusätzlich: Ecken für/gegen, Fouls begangen/erlitten, Karten, Häufigkeiten und Prognose (Spielblatt → „Vor dem Spiel“).

## Basketball, Eishockey, Handball (scripts/multisport.py)
Schalter oben auf der Seite. Alle Ligen weltweit aus Sofascore, Ergebnisse werden gesammelt und einmalig 90 Tage
rückwirkend nachgeladen (10 Tage je Lauf). Modell: Elo mit Punkt-/Tordifferenz und Heimvorteil, Heim- und Auswärtsschnitte.
Basketball/Handball: Siegchance, Handicap, Gesamtpunkte. Eishockey: Sieg inkl. Verlängerung, 1/X/2 nach 60 Minuten, Tore.
Serien, Ausfälle und Aufstellungen für Top-Ligen in den 36 Stunden vor Beginn.

## Wettschein und Bet Builder
„+“ neben jedem Tipp legt ihn in den Schein (unten rechts). Tipps aus demselben Spiel werden zum Bet Builder.
Fußball und Eishockey: gemeinsame Chance exakt aus der Ergebnisverteilung (z. B. 1X + Unter 3,5). Sonst angenähert und markiert.
Buchmacher-Quote je Spiel eintragbar, Einsatz, möglicher Gewinn, Kopieren. Gespeichert im Browser.

## Was die Welt setzt (scripts/world.py)
Prognosemarkt-Preise und Handelsvolumen von Polymarket (öffentliche Gamma-API, ohne Key), stündlich.
Im Spielblatt neben dem Modell: einig oder uneinig, wie viel Geld gehandelt wurde.

## Offizielle US-Daten (scripts/usapis.py)
NBA: ESPN Verletztenbericht und Tabelle. NHL: offizielle NHL-Tabelle (api-web.nhle.com) und ESPN Verletztenbericht.
Bei jedem Lauf aktualisiert, im Spielblatt von NBA- und NHL-Spielen unter „Offizielle Daten“.

## Quellen-Übersicht
Knopf „Quellen“ oben rechts: welche Quelle läuft, welche ruht (eigener Takt), welche ausgefallen ist (data/status.json).

## Externer Auslöser (alle 15 Minuten, für den Check vor Spielbeginn)
cron-job.org ruft per POST https://api.github.com/repos/<user>/<repo>/actions/workflows/update-data.yml/dispatches
mit Header "Authorization: Bearer <Token>" und Body {"ref":"main"} auf. Token: Fine-grained, nur dieses Repo, Actions: Read and write.
