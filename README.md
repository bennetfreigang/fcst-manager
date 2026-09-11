# fcst-manager

Automatisiert den Forecast-Prozess für Artikel aus ÖBB-Rahmenverträgen: pro
Artikelzeile Klassifizierung (High Runner / Mid Runner / Sleeper), Bestimmung der
Lieferlücke und Erzeugung eines 18-Monats-FCST — mit vollständiger Herleitung
jedes einzelnen Werts.

## Installation

```bash
uv sync
```

Streamlit steckt in der Dev-Gruppe und ist damit sofort dabei. Für eine Installation
ohne Dev-Abhängigkeiten: `uv sync --no-dev --extra app`. Ganz ohne Streamlit laufen
CLI und Engine weiterhin; die App-Tests überspringen sich dann selbst.

## Oberfläche (Streamlit)

```bash
uv run streamlit run src/fcst_manager/app.py
```

Excel hochladen (Aufbau wie die Beispieldateien) — die App klassifiziert jede Zeile,
rechnet den FCST und zeigt ihn in vier Reitern:

| Reiter | Inhalt |
|---|---|
| **Übersicht** | Kennzahlen, Verteilung auf die Äste, Tabelle aller Artikel, Vergleich mit einem vorhandenen manuellen FCST |
| **FCST-Matrix** | Artikel × Monat |
| **Artikel-Detail** | Verlaufsdiagramm (Historie / Order / FCST), Herleitung Schritt für Schritt, Annahmen und Warnungen |
| **Export** | Excel mit befüllten FCST-Spalten und Report-Blatt, dazu das Audit-Log |

### 18-Monats-Sicht

Die Umschaltung **„Letzte 18 Monate“ / „Gesamte Historie“** in der Seitenleiste betrifft
ausschließlich die *Anzeige*. **Gerechnet wird immer auf der gesamten Historie** — eine
Kürzung würde das High-Runner-Kriterium (mindestens 36 Monate Datenbasis) aushebeln und
jeden Artikel zum Mid Runner machen (abgesichert durch
`test_truncating_history_would_break_classification`).

Damit die Kurzsicht nicht in die Irre führt:

* die Übersicht zeigt **beide** Bestellzahlen nebeneinander — „letzte 18 Monate“ und „gesamt“
* im Artikel-Detail erscheint ein Hinweis, sobald Bestellungen außerhalb des Fensters
  liegen („Nur 4 von 6 Bestellungen liegen in den letzten 18 Monaten“) — also genau bei den
  Artikeln, für die sich die gesamte Historie lohnt

## Verwendung (CLI)

Ganze Datei rechnen und als neue Excel exportieren:

```bash
uv run fcst-manager run Artikel.xlsx -o Artikel_FCST.xlsx --log audit.txt
```

Herleitung einzelner Artikel im Klartext:

```bash
uv run fcst-manager explain Artikel.xlsx --item 1/136648
```

T-Schätzmethoden nebeneinander stellen (nur Artikel ohne AVG Demand):

```bash
uv run fcst-manager compare-t Artikel.xlsx
```

Regression gegen die Referenzdateien des Kunden:

```bash
uv run python -m fcst_manager.validate
```

### Wichtige Schalter

| Schalter | Bedeutung |
|---|---|
| `--stichtag JJJJ_MM` | „Aktueller Zeitpunkt“ explizit setzen |
| `--stichtag-heute` | laufenden Kalendermonat verwenden statt der ersten FCST-Spalte |
| `--horizon N` | FCST-Horizont (Default: Breite des FCST-Abschnitts, sonst 18) |
| `--t-method` | T-Schätzer ohne AVG Demand (siehe unten) |
| `--large-gap-months N` | Schwelle „große Lieferlücke“ (Default 12) |
| `--log DATEI` | Audit-Log mit der Herleitung jedes Artikels |

## Eingabeformat

Ein Arbeitsblatt, eine Zeile je Artikel:

* **Zeile 1** — Abschnittsname je Spalte: `Historie`, `Order`, `FCST` (leer bei Metaspalten)
* **Zeile 2** — Spaltenkopf: Metafeldname bzw. Monat `JJJJ_MM`
* **Zeile 3 ff** — je Zeile ein Artikel

Metaspalten: `ItemNumber`, `AVG Demand ÖBB [mon]`, `MOQ Vertrag`, `LT [mon]`.
Die Zuordnung ist tolerant (Groß-/Kleinschreibung, Zusätze wie `[mon]`).
`"."`, leer und `"-"` bedeuten „unbekannt“; unlesbare Werte werden als
Warnung gemeldet statt still verschluckt.

**Stichtag** = erste FCST-Spalte der Datei (so mit dem Kunden festgelegt).

## Entscheidungsbaum und Validierungsstand

| Ast | Verhalten | Stand |
|---|---|---|
| Sleeper | kein FCST | aus Diagramm |
| High Runner, kleine Lücke | Standard-FCST | **an 2 Referenzitems validiert (8/8 Monate)** |
| High Runner, große Lücke | `Q = AVG Demand × T`, aufgerundet auf MOQ-Vielfaches; erster Termin nicht vor Lückenende | **Annahme** — keine Referenzdaten |
| Mid Runner, kleine Lücke | Standard-FCST | **Annahme** — keine Referenzdaten |
| Mid Runner, große Lücke | FCST nur bei offenem Backlog **und** bekanntem AVG Demand | **Annahme** — keine Referenzdaten |

Jede Annahme steht als Klartextsatz in `Decision.assumptions`, im Audit-Log und
in der Spalte „Annahmen“ des Report-Blatts. Kein nicht validierter Ast erzeugt
stillschweigend Zahlen.

### Standard-FCST

1. **Q** = historisch häufigste Menge, MOQ-Vielfache bevorzugt; Gleichstand → größere Menge.
2. **T** = `floor(Q / AVG Demand)`, mindestens 1. Fehlt AVG Demand, greift der Fallback (unten).
3. **Anchor** = spätester Kandidat aus
   * letzte Order **(Beginn der Order, Split-Lieferungen zusammengefasst)** + T
   * letzter Verkauf + LT
   * Stichtag + LT
4. Weitere Termine: Anchor + n×T bis Horizont-Ende, jeweils Menge Q.
5. Monate mit bereits bekannter Order werden übersprungen, nicht dupliziert.

## T-Fallback ohne AVG Demand

Default `implied_demand`: `Demand = Summe Historie / Monate seit erster
Bestellung`, dann `T = floor(Q / Demand)` — dieselbe Formel wie im bekannten
Fall, nur mit geschätztem Demand.

Vergleich am Referenzitem `D228025-100` (Kunde rechnet mit T=4):

| `--t-method` | T | |
|---|---|---|
| `implied_demand` | **4** | ✅ Default |
| `implied_demand_order_span` | 3 | |
| `mean_gaps` | 3 | erste Iteration |
| `mean_gaps_filtered` | 6 | Abstände von 1 Monat verworfen |
| `median_cluster_gaps` | 8 | |

Median und Ausreißerfilter auf den rohen Bestellabständen machen es hier
**schlechter**: die kurzen Abstände sind keine Ausreißer, sondern Split-Lieferungen
derselben Order — der reine Median der Abstände ergibt T=1.

## Nachvollziehbarkeit

`forecast()` liefert ein `Decision`-Objekt mit Segment, Ast, Q, T, implizitem
Demand, Anchor, je Termin einer Begründung sowie Annahmen und Warnungen.
`Decision.explain()` rendert das als Klartext:

```
Item 1/136648   (Stichtag 2026_08)
  Klassifizierung : High Runner  -  8 Bestellungen (>= 3) bei 42 Monaten Datenbasis (>= 36); 0.57 Bestellungen/Quartal
  Lieferluecke    : 2027_05 (= letzte Bindung + LT), +9 Monate ab Stichtag -> klein
  Ast             : High Runner, kleine Luecke -> Standard-FCST
  Menge Q         : 150  (haeufigste Menge unter den MOQ-Vielfachen (5x in der Historie, MOQ=50))
  Intervall T     : 3 Monate  (floor(Q/AvgDemand) = floor(150/37.86))
  Erster Termin   : 2027_03  (letzte Order 2026_12 + T=3; spaeter als 2027_01)
  FCST:
    2027_03         150   Anchor (Menge aus Historie)
    2027_06         150   Anchor + 1xT = 2027_03 + 1x3 (Menge aus Historie)
```

Die Export-Excel enthält zusätzlich das Blatt **FCST-Report** mit einer Zeile je
Artikel und 29 Spalten (Segment, Ast, Q/T/Anchor samt Herleitung, Annahmen,
Warnungen, Vergleich mit einem vorhandenen manuellen FCST).

## Offene Punkte für die Abstimmung mit dem Kunden

1. **„3 Jahre Datenbasis“** ist als Breite des Historie-Fensters ausgelegt. Damit
   ist das Kriterium für alle Zeilen einer Datei identisch und unterscheidet die
   Artikel nicht. Gemeint ist vermutlich etwas anderes — eine Auslegung als
   „Artikel wird seit 3 Jahren verkauft“ würde aber das validierte Referenzitem
   `D228025-100` (erste Bestellung 2024_11) zum Mid Runner machen.
2. **„≥1 Bestellung/Quartal“** wird nur berichtet, nicht geprüft — beide
   Referenzitems liegen darunter (0,43 bzw. 0,57) und gelten dem Kunden dennoch
   als High Runner.
3. **Sleeper-Abgrenzung**: umgesetzt als ≤1 Bestellung **und** kein AVG Demand
   **und** letzte Bestellung älter als 12 Monate. „Kleine Bestellungen“ aus dem
   Diagramm ist nicht quantifizierbar und bleibt unberücksichtigt.
4. **„Geringer Demand“** beim Mid Runner ist nicht quantifiziert und daher kein
   eigenes Kriterium.
5. **Lieferlücke in der Vergangenheit** gilt nach der Regel „mehr als 12 Monate ab
   Stichtag“ als *kleine* Lücke, obwohl der Artikel faktisch ungedeckt ist. Die
   Engine warnt, ändert die Regel aber nicht.
6. **`floor()` bei T** ist instabil, wenn `Q/Demand` knapp unter einer ganzen Zahl
   liegt: bei `1/136648` ist `150/37,861 = 3,962` → T=3. Schon 1,8 % Abweichung im
   Demand kippt das Ergebnis auf 4.

## Entwicklung

```bash
uv run pytest                                  # 106 Tests
uv run python tools/make_sample_workbook.py    # Sammeldatei mit Kantenfällen neu bauen
```

Die App-Tests fahren den kompletten Upload-Pfad über Streamlits `AppTest` — inklusive
Fehlerbehandlung für kaputte Dateien und unlesbare Stichtage.

`docs/archiv/` enthält die erste Iteration der Engine und eine Zuordnungstabelle
alt → neu.
