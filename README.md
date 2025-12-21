# Reblogging & Sharkey-Publisher

Ein kleines Python-Skript, das täglich ältere Artikel aus einem RSS-Feed erneut veröffentlicht. Pro Lauf wird deterministisch der jeweils älteste, noch nicht gepostete Beitrag ausgewählt und als Notiz auf einer Sharkey/Misskey-Instanz geteilt. Bereits veröffentlichte Links werden in `posted_urls.json` festgehalten, damit nichts doppelt erscheint.

## Voraussetzungen
- Python 3.11 oder neuer
- Internetzugang zum Abrufen des Feeds und zur Sharkey-API
- Optional: OpenAI-API-Schlüssel, falls Texte mit `OPENAI_MODEL` generiert werden sollen (Standard: `gpt-5-mini`)

## Einrichtung
1. Virtuelle Umgebung anlegen und aktivieren:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Abhängigkeiten installieren:
   ```bash
   pip install -r requirements.txt
   ```
3. `.env` anlegen (siehe Konfiguration unten) und mindestens die Sharkey-Zugangsdaten setzen.

## Konfiguration (.env)
Folgende Variablen können in der `.env` hinterlegt werden:

- `FEED_URL`: RSS-Feed-URL (z. B. `https://dasnetzundich.de/category/anleitung/feed/`)
- `DAYS_OLD`: Mindestalter in Tagen, ab wann Beiträge repostet werden
- `MAX_POSTS`: Anzahl der Posts pro Lauf (Empfehlung: `1` für genau einen täglichen Post)
- `POSTED_LOG_PATH`: Pfad zur Logdatei (Standard: `./posted_urls.json`)
- `SHARKEY_INSTANCE_URL`: Basis-URL der Instanz (z. B. `https://example.social`)
- `SHARKEY_TOKEN`: Persönliches Token mit Schreibrechten
- `SHARKEY_VISIBILITY`: Sichtbarkeit (`public`, `home`, `followers`); Standard: `public`
- `OPENAI_API_KEY`: API-Schlüssel für OpenAI (optional)
- `OPENAI_MODEL`: Modellname für die Zusammenfassung (Standard: `gpt-5-mini`)

## Nutzung
Das Skript lädt den Feed, wählt den ältesten passenden Beitrag, erzeugt den Posting-Text (optional per OpenAI) und veröffentlicht ihn.

### Dry-run
Zeigt nur die ausgewählten Beiträge und den geplanten Posting-Text, ohne zu veröffentlichen oder das Log zu schreiben:

```bash
python reblog.py --dry-run
```

### Regulärer Lauf
Verwendet die Werte aus der `.env` und schreibt ins Log:

```bash
python reblog.py
```

### Parameter (CLI oder `.env`)
| Option | Beschreibung | Standard |
| --- | --- | --- |
| `--feed-url` | RSS-Feed-URL | `FEED_URL` oder eingebauter Default |
| `--days-old` | Mindestalter der Beiträge in Tagen | `DAYS_OLD` oder `180` |
| `--max-posts` | Anzahl Posts pro Lauf (0 = alle passenden) | `MAX_POSTS` oder `0` |
| `--posted-log` | Pfad zur Logdatei | `POSTED_LOG_PATH` oder `./posted_urls.json` |
| `--dry-run` | Nur anzeigen, nichts posten | `False` |

## Verhalten & Auswahlregeln
- Es wird immer der älteste Eintrag gepostet, der **älter als `DAYS_OLD`** ist und dessen URL noch nie im Log auftauchte.
- Unterschiedliche Schreibweisen derselben URL werden durch Normalisierung erkannt und nicht doppelt gepostet.
- Bei `MAX_POSTS=1` entsteht so genau ein neuer Post pro Tag (der Standardlauf arbeitet das Archiv nach und nach ab).
- `MAX_POSTS>1` postet mehrere alte Einträge, beginnend mit dem ältesten noch nicht geposteten.

## Format von `posted_urls.json`
Die Datei enthält ein Array von Objekten. Jedes Objekt mindestens:

- `url`: normalisierte URL (ohne Query/Fragment, ohne abschließenden Slash, http→https)
- `posted_at`: Zeitpunkt des Posts im ISO-8601-Format (z. B. `2025-12-22T23:00:00+01:00` oder `2025-12-22T22:00:00Z`)

Beispiel:

```json
[
  {
    "url": "https://example.de/post-1",
    "posted_at": "2024-03-01T10:00:00Z"
  },
  {
    "url": "https://example.de/post-2",
    "posted_at": "2024-03-02T10:00:00+01:00"
  }
]
```

Normalisierung bedeutet:
- Query-Strings und Fragmente werden entfernt.
- Abschließende Slashes werden entfernt.
- Falls möglich, wird `http` zu `https` umgeschrieben.

Beispiel: `https://example.de/post/?utm_source=x#section` wird als `https://example.de/post` gespeichert. Die Datei muss gültiges JSON enthalten und darf nicht leer sein.

## Troubleshooting
- **“Es wird immer derselbe Artikel gepostet.”** Stelle sicher, dass `MAX_POSTS` auf `1` steht **und** die Logdatei gültig ist. Die Auswahl erfolgt nach dem Entfernen bereits geposteter URLs.
- **Log leer oder ungültig?** Prüfe, ob `posted_urls.json` gültiges JSON ist und die Struktur wie oben gezeigt hat.
- **Keine neuen Beiträge gefunden?** Dann waren entweder alle alten Beiträge schon im Log oder es gibt keine Beiträge, die älter als `DAYS_OLD` sind.

