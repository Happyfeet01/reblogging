import argparse
import json
import os
import pathlib
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import feedparser
import requests
from dotenv import load_dotenv
from openai import OpenAI

DEFAULT_FEED_URL = "https://dasnetzundich.de/category/anleitung/feed/"
DEFAULT_DAYS_OLD = 180
DEFAULT_MAX_POSTS = 0
DEFAULT_POSTED_LOG = "./posted_urls.json"
DEFAULT_VISIBILITY = "public"
DEFAULT_LLM_MODEL = "gpt-5-mini"


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


def select_old_entries(
    entries: Iterable, cutoff: datetime, max_posts: int
) -> List[feedparser.FeedParserDict]:
    selected = []
    for entry in entries:
        published = parse_entry_date(entry)
        if not published:
            continue
        if published <= cutoff:
            selected.append((published, entry))
    selected.sort(key=lambda item: item[0])

    if max_posts and max_posts > 0:
        selected = selected[:max_posts]

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
            temperature=0.6,
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
    except Exception as exc:  # pragma: no cover - defensive JSON-Parsing
        raise ValueError(f"Konnte Log-Datei {posted_file} nicht lesen: {exc}") from exc

    posted: Dict[str, datetime] = {}
    for item in raw:
        url = item.get("url")
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
    posted_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def was_recently_posted(url: str, posted: Dict[str, datetime], cutoff: datetime) -> bool:
    if not url:
        return False
    posted_at = posted.get(url)
    return bool(posted_at and posted_at >= cutoff)


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
    posted_cutoff = datetime.now(timezone.utc) - timedelta(days=config["days_old"])
    print(
        f"Lade Feed {config['feed_url']} und filtere Beiträge älter als {config['days_old']} Tage..."
    )

    feed = fetch_feed(config["feed_url"])
    entries = select_old_entries(feed.entries, cutoff_date, config["max_posts"])

    if not entries:
        print("Keine passenden Beiträge gefunden.")
        return

    posted_log = load_posted_urls(config["posted_log"])
    for entry in entries:
        published = parse_entry_date(entry)
        if not published:
            continue
        url = entry.get("link")
        if was_recently_posted(url, posted_log, posted_cutoff):
            print(f"Überspringe bereits geposteten Artikel: {url}")
            continue

        status = compose_status(entry, published, config)
        publish_to_sharkey(
            config["sharkey_instance"],
            config["sharkey_token"],
            config["sharkey_visibility"],
            status,
            config["dry_run"],
        )

        if not config["dry_run"] and url:
            posted_log[url] = datetime.now(timezone.utc)

    if not config["dry_run"]:
        save_posted_urls(config["posted_log"], posted_log)


if __name__ == "__main__":
    main()
