# Reblogging & Sharkey-Publisher

Dieses Repository enthält ein kleines Python-Skript, das alte Artikel aus dem RSS-Feed `https://dasnetzundich.de/category/anleitung/feed/` herausfiltert und direkt auf eine Sharkey/Misskey-Instanz veröffentlicht. Optional lässt sich ein Dry-Run durchführen. Bereits gepostete URLs werden in einer Log-Datei gespeichert, sodass Artikel, die in den letzten 180 Tagen veröffentlicht wurden, nicht erneut gepostet werden.

## Voraussetzungen
- Python 3.11 oder neuer
- Internetzugang, um den Feed abzurufen und die Sharkey-API zu erreichen
- Optional: OpenAI-API-Schlüssel, falls die Textgenerierung über `gpt-5-mini` erfolgen soll

## Einrichtung
1. Erstelle und aktiviere eine virtuelle Umgebung:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
2. Installiere die Abhängigkeiten:
   ```bash
   pip install -r requirements.txt
   ```
3. Kopiere die Beispiel-Umgebungsvariablen und passe sie an, falls nötig:
   ```bash
   cp .env.example .env
   ```
4. Hinterlege in der `.env` mindestens `SHARKEY_INSTANCE_URL` (z. B. `https://example.social`) und `SHARKEY_TOKEN` (persönliches API-Token mit Schreibrechten für Notizen).
5. Für KI-generierte Texte trage zusätzlich `OPENAI_API_KEY` ein. Das verwendete Modell kann über `OPENAI_MODEL` angepasst werden (Standard: `gpt-5-mini`).

## Nutzung
Das Skript lädt den Feed, filtert Beiträge, die älter als 180 Tage sind, und veröffentlicht sie als Notizen auf der angegebenen Sharkey/Misskey-Instanz. Im Dry-Run werden nur Vorschauen ausgegeben.

### Beispiele
- Standardlauf mit den Werten aus der `.env`:
  ```bash
  python reblog.py
  ```
- Spezifische Parameter angeben (z. B. anderes Logfile und Trockenlauf):
  ```bash
  python reblog.py --posted-log ./data/posted_urls.json --dry-run
  ```

## Parameter
| Option | Beschreibung | Standard |
| --- | --- | --- |
| `--feed-url` | Feed-URL, aus der Artikel geladen werden | Wert aus `FEED_URL` oder der vorgegebene Feed |
| `--days-old` | Mindestalter der Beiträge in Tagen | `DAYS_OLD` oder `180` |
| `--max-posts` | Maximale Anzahl verarbeiteter Beiträge (0 = alle) | `MAX_POSTS` oder `0` |
| `--posted-log` | Datei zum Speichern bereits geposteter URLs | `POSTED_LOG_PATH` oder `./posted_urls.json` |
| `--dry-run` | Nur auflisten, keine Veröffentlichung | `False` |

### Sharkey-spezifische Variablen
- `SHARKEY_INSTANCE_URL`: Basis-URL der Instanz, z. B. `https://example.social`
- `SHARKEY_TOKEN`: API-Token (persönliches Token mit Schreibrechten)
- `SHARKEY_VISIBILITY`: Sichtbarkeit der Notiz (`public`, `home`, `followers`); Default: `public`

### OpenAI-Variablen (optional)
- `OPENAI_API_KEY`: API-Schlüssel zum Aufruf von OpenAI
- `OPENAI_MODEL`: Modellname, z. B. `gpt-5-mini`

### KI-Einleitung & Hashtags
Wenn du OpenAI nutzt, weise das Modell an, eine kurze, einladende Einleitung mit genau 50 Wörtern zu verfassen, die Lust aufs Lesen des Artikels macht. Ergänze anschließend ein paar thematisch passende Hashtags, zum Beispiel:

- `#Fediverse` `#Misskey` `#Sharkey` `#RSS` `#Blogging` `#Automation`
- Optional weitere Hashtags passend zum Artikelinhalt

**Beispiel-Prompt:**
> Schreibe eine freundliche, neugierig machende Einleitung in exakt 50 Wörtern für den folgenden Artikel und füge danach fünf passende Hashtags hinzu.

## Verhalten
- Nur Artikel mit Veröffentlichungsdatum, die älter als das konfigurierte Mindestalter sind, werden berücksichtigt.
- URLs, die bereits in der Log-Datei stehen und innerhalb der letzten `days_old` Tage gepostet wurden, werden übersprungen.
- Im Dry-Run wird die vorbereitete Notiz angezeigt, aber nicht veröffentlicht und nicht ins Log übernommen.

## Fehlerbehandlung
- Beiträge ohne ermittelbares Veröffentlichungsdatum werden übersprungen.
- Bei Netzwerkausfällen, einem ungültigen Feed oder einer fehlgeschlagenen Sharkey-Verbindung bricht das Skript mit einer verständlichen Fehlermeldung ab.
