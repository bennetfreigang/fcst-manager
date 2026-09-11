# Archiv

`forecast_engine_iteration1.py` ist die erste Iteration der Engine, unveraendert.
Sie ist nicht mehr Teil des Pakets und wird nicht importiert. Der Code lebt jetzt
verteilt in `src/fcst_manager/`:

| alte Funktion                | neu                                                |
|------------------------------|----------------------------------------------------|
| `to_period` / `to_label`     | `periods.Month.parse` / `Month.label`              |
| `Item`                       | `model.Item` (mit `warnings`, Monate als `Month`)   |
| `classify`                   | `classify.classify` -> `(Segment, Begruendung, Annahmen)` |
| `last_known_commitment`      | `engine.last_known_commitment`                     |
| `lieferluecke`               | `engine.supply_gap` (zusaetzlich Warnungen)        |
| `compute_quantity`           | `estimators.compute_quantity` -> `(Q, Herleitung, Warnungen)` |
| `compute_interval`           | `estimators.compute_interval` -> `(T, Herleitung, Demand, Warnungen)` |
| `compute_anchor`             | `engine.compute_anchor` (Split-Lieferungen geclustert) |
| `generate_fcst`              | `engine.forecast` -> `Decision`                    |

Die Datei ist zusätzlich im git-Index vorhanden und lässt sich jederzeit mit
`git show :src/fcst_manager/forecast_engine.py` im Originalzustand ansehen.
