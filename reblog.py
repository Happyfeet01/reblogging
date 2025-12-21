import argparse
import json
import os
import pathlib
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse, urlunparse

import feedparser
import httpx
import requests
from dotenv import load_dotenv
from openai import OpenAI

DEFAULT_FEED_URL = "https://dasnetzundich.de/category/anleitung/feed/"
DEFAULT_DAYS_OLD = 180
DEFAULT_MAX_POSTS = 0
DEFAULT_POSTED_LOG = "./posted_urls.json"
DEFAULT_VISIBILITY = "public"
DEFAULT_LLM_MODEL = "gpt-5-mini"


def ensure_httpx_proxy_support() -> None:
    """Abort early if httpx version lacks ``proxies`` support.

    OpenAI's client injects a ``proxies`` argument for compatibility with
    environment-based proxy settings. httpx 0.28+ removed this parameter,
    which results in ``TypeError: Client.__init__() got an unexpected keyword
    argument 'proxies'``. Enforcing a compatible version up front provides a
    clearer error message than failing deep inside the OpenAI client.
    """

    version = getattr(httpx, "__version__", "0")
    parts = re.split(r"\D+", version)
    parsed = tuple(int(p) for p in parts if p.isdigit())

    if parsed and (parsed[0] > 0 or (parsed[0] == 0 and len(parsed) > 1 and parsed[1] >= 28)):
        raise RuntimeError(
            "httpx >= 0.28 entfernt die Unterstützung für 'proxies'. "
            "Bitte installiere httpx<0.28 (z.B. per 'pip install -r requirements.txt')."
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Lädt einen RSS-Feed, filtert alte Beiträge und veröffentlicht sie auf "
            "einer Sharkey/Misskey-Instanz."
        )
    )
    parser.add_argument("--feed-url", help="RSS-Feed-URL", default=None)
    parser.add_argument(
        "--days-old",
        type=int,
        help="Mindestalter der Beiträge in Tagen",
        default=None,
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        help="Maximale Anzahl der zu verarbeitenden Beiträge (0 = alle)",
        default=None,
    )
    parser.add_argument(
        "--posted-log",
        help="Datei zum Speichern bereits geposteter URLs",
        default=None,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Listet nur die gefundenen Beiträge, ohne zu veröffentlichen",
    )
    return parser.parse_args()


def normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""

    parsed = urlparse(url)
    scheme = "https" if parsed.scheme == "http" else parsed.scheme
    path = parsed.path.rstrip("/")
    normalized = parsed._replace(scheme=scheme, path=path, query="", fragment="")
    return urlunparse(normalized)


def load_config(args: argparse.Namespace) -> dict:
    load_dotenv()
    return {
        "feed_url": args.feed_url or os.getenv("FEED_URL", DEFAULT_FEED_URL),
        "days_old": int(os.getenv("DAYS_OLD", args.days_old or DEFAULT_DAYS_OLD)),
        "max_posts": int(os.getenv("MAX_POSTS", args.max_posts or DEFAULT_MAX_POSTS)),
        "posted_log": args.posted_log or os.getenv("POSTED_LOG_PATH", DEFAULT_POSTED_LOG),
        "dry_run": args.dry_run,
        "sharkey_instance": os.getenv("SHARKEY_INSTANCE_URL"),
        "sharkey_token": os.getenv("SHARKEY_TOKEN"),
        "sharkey_visibility": os.getenv(
            "SHARKEY_VISIBILITY", DEFAULT_VISIBILITY
        ),
        "openai_api_key": os.getenv("OPENAI_API_KEY"),
        "openai_model": os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL),
    }


def fetch_feed(feed_url: str) -> feedparser.FeedParserDict:
    try:
        parsed = feedparser.parse(
            feed_url,
            request_headers={
                "User-Agent": "reblogging-script/1.0 (+https://dasnetzundich.de)"
            },
        )
    except Exception as exc:  # pragma: no cover - defensive Netzwerkanpassung
        raise ConnectionError(f"Feed konnte nicht geladen werden: {exc}") from exc

    status = parsed.get("status")
    if status and status >= 400:
        raise ConnectionError(f"Feed antwortete mit HTTP-Status {status}")

    if parsed.bozo:
        raise ValueError(f"Feed konnte nicht gelesen werden: {parsed.bozo_exception}")
    return parsed


def parse_entry_date(entry) -> Optional[datetime]:
    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            try:
                timestamp = timegm(parsed)
            except (TypeError, ValueError, OverflowError):
                continue

            offset = getattr(parsed, "tm_gmtoff", None)
            if isinstance(offset, (int, float)):
                timestamp -= offset

            try:
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                continue
    return None


def select_old_entries(entries: Iterable, cutoff: datetime) -> List[feedparser.FeedParserDict]:
    selected = []
    for entry in entries:
        published = parse_entry_date(entry)
        if not published:
            continue
        if published <= cutoff:
            selected.append((published, entry))
    selected.sort(key=lambda item: item[0])
    return [entry for _, entry in selected]


def clean_summary(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def build_status(entry, published: datetime) -> str:
    title = entry.get("title", "Ohne Titel")
    link = entry.get("link", "")
    summary = entry.get("summary", entry.get("description", ""))
    cleaned_summary = clean_summary(summary)
    parts = [title.strip(), link]
    if cleaned_summary:
        parts.append(cleaned_summary)
    parts.append(f"(Original veröffentlicht am {published.date().isoformat()})")
    return "\n\n".join([part for part in parts if part])


def generate_with_llm(
    *,
    entry,
    published: datetime,
    api_key: Optional[str],
    model: str,
) -> Optional[str]:
    if not api_key:
        return None

    ensure_httpx_proxy_support()
    client = OpenAI(api_key=api_key)
    title = entry.get("title", "Ohne Titel")
    link = entry.get("link", "")
    summary = clean_summary(entry.get("summary", entry.get("description", "")))
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Du schreibst kurze, sachliche deutsche Notizen für Sharkey/Misskey. "
                        "Fasse den Inhalt eines Blogartikels freundlich zusammen, füge einen Hinweis "
                        "auf das ursprüngliche Veröffentlichungsdatum hinzu und animiere zum Lesen."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Titel: {title}\n"
                        f"Link: {link}\n"
                        f"Veröffentlicht am: {published.date().isoformat()}\n"
                        f"Zusammenfassung: {summary}"
                    ),
                },
            ],
        )
    except Exception as exc:  # pragma: no cover - API-Kommunikation
        print(f"[WARNUNG] OpenAI-Antwort fehlgeschlagen ({exc}). Fallback auf Standardtext.")
        return None

    message = completion.choices[0].message.content if completion.choices else ""
    generated = (message or "").strip()
    return generated or None


def compose_status(entry, published: datetime, config: dict) -> str:
    ai_text = generate_with_llm(
        entry=entry,
        published=published,
        api_key=config.get("openai_api_key"),
        model=config.get("openai_model", DEFAULT_LLM_MODEL),
    )
    if ai_text:
        link = entry.get("link", "")
        parts = [ai_text]
        if link:
            parts.append(f"Mehr lesen: {link}")
        parts.append(f"(Original veröffentlicht am {published.date().isoformat()})")
        return "\n\n".join(parts)

    return build_status(entry, published)


def load_posted_urls(path: str) -> Dict[str, datetime]:
    posted_file = pathlib.Path(path)
    if not posted_file.exists():
        return {}
    try:
        raw = json.loads(posted_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive JSON-Parsing
        raise ValueError(
            f"Log-Datei {posted_file} enthält kein gültiges JSON: {exc}"
        ) from exc
    except Exception as exc:  # pragma: no cover - defensive JSON-Parsing
        raise ValueError(f"Konnte Log-Datei {posted_file} nicht lesen: {exc}") from exc

    if not isinstance(raw, list):
        raise ValueError(f"Log-Datei {posted_file} muss eine Liste enthalten.")

    posted: Dict[str, datetime] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        url = normalize_url(item.get("url", ""))
        posted_at = item.get("posted_at")
        if not url or not posted_at:
            continue
        try:
            ts = datetime.fromisoformat(posted_at)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        posted[url] = ts
    return posted


def save_posted_urls(path: str, posted: Dict[str, datetime]):
    posted_file = pathlib.Path(path)
    posted_file.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {"url": url, "posted_at": ts.isoformat()} for url, ts in sorted(posted.items())
    ]
    posted_file.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def was_posted_ever(url: str, posted: Dict[str, datetime]) -> bool:
    normalized = normalize_url(url)
    return bool(normalized and normalized in posted)


def publish_to_sharkey(
    instance_url: str, token: str, visibility: str, text: str, dry_run: bool
):
    if dry_run:
        print("[DRY RUN] Würde posten:\n---")
        print(text)
        print("---")
        return

    if not instance_url or not token:
        raise ValueError(
            "SHARKEY_INSTANCE_URL und SHARKEY_TOKEN müssen gesetzt sein, um zu posten."
        )

    endpoint = f"{instance_url.rstrip('/')}/api/notes/create"
    response = requests.post(
        endpoint,
        json={"i": token, "text": text, "visibility": visibility},
        timeout=15,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:  # pragma: no cover - Netzwerkanpassung
        raise ConnectionError(
            f"Sharkey-Antwort {response.status_code}: {response.text}"
        ) from exc

    print("Gepostet auf Sharkey: Status 200")


def main():
    args = parse_args()
    config = load_config(args)

    cutoff_date = datetime.now(timezone.utc) - timedelta(days=config["days_old"])
    print(
        f"Lade Feed {config['feed_url']} und filtere Beiträge älter als {config['days_old']} Tage..."
    )

    feed = fetch_feed(config["feed_url"])
    entries = select_old_entries(feed.entries, cutoff_date)

    if not entries:
        print("Keine passenden Beiträge gefunden.")
        return

    posted_log = load_posted_urls(config["posted_log"])
    candidates = []
    for entry in entries:
        url = entry.get("link")
        if not url:
            continue
        if was_posted_ever(url, posted_log):
            print(f"Überspringe bereits geposteten Artikel: {url}")
            continue
        candidates.append(entry)

    if not candidates:
        print("Keine neuen (noch nicht geposteten) Beiträge gefunden.")
        return

    max_posts = config["max_posts"]
    to_post = candidates[: max_posts] if max_posts and max_posts > 0 else candidates[:1]

    for entry in to_post:
        published = parse_entry_date(entry)
        if not published:
            continue

        status = compose_status(entry, published, config)
        publish_to_sharkey(
            config["sharkey_instance"],
            config["sharkey_token"],
            config["sharkey_visibility"],
            status,
            config["dry_run"],
        )

        url = entry.get("link")
        if not config["dry_run"] and url:
            posted_log[normalize_url(url)] = datetime.now(timezone.utc)

    if not config["dry_run"]:
        save_posted_urls(config["posted_log"], posted_log)


if __name__ == "__main__":
    main()
