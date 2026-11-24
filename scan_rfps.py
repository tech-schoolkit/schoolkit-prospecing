import hashlib
import json
import os
from datetime import datetime, timezone
import feedparser
import gspread
from google.oauth2.service_account import Credentials

# Broad keyword set (prefers false positives so you can filter manually)
SCHOOLKIT_KEYWORDS = [
    "school",
    "district",
    "board",
    "education",
    "international program",
    "web",
    "website",
    "cms",
    "content management",
    "redesign",
    "hosting",
    "portal",
    "intranet",
]

APPAZUR_KEYWORDS = [
    "mass notification",
    "notification",
    "parent",
    "communication",
    "app",
    "mobile app",
    "absence",
    "attendance",
    "digital signage",
    "digital display",
    "signage",
    "display",
]

US_WEST_COAST_STATES = [
    "washington",
    "oregon",
    "california",
    "nevada",
    "alaska",
    "hawaii",
]

# Add RSS/Atom feed URLs here
RSS_FEEDS = [
    # Example: "https://canadabuys.canada.ca/..."
]


def classify_match(text: str) -> str:
    low = text.lower()
    sk = any(k in low for k in SCHOOLKIT_KEYWORDS)
    az = any(k in low for k in APPAZUR_KEYWORDS)
    if sk and az:
        return "Both (Schoolkit & Appazur)"
    if sk:
        return "Schoolkit"
    if az:
        return "Appazur"
    return ""


def make_id(source: str, title: str, link: str) -> str:
    raw = f"{source}|{title}|{link}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def fetch_rss_items() -> list[dict]:
    items = []
    for url in RSS_FEEDS:
        feed = feedparser.parse(url)
        source_name = feed.feed.get("title", url)
        for entry in feed.entries:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", "").strip()
            link = entry.get("link", "").strip()
            published = entry.get("published", "")
            items.append(
                {
                    "source": source_name,
                    "issuer": source_name,
                    "title": title,
                    "description": summary,
                    "closing_date": published,
                    "link": link,
                }
            )
    return items


def get_sheet():
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    sheet_name = os.environ["SHEET_NAME"]
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1


def main():
    seen_path = "seen_rfps.json"
    with open(seen_path, "r", encoding="utf-8") as f:
        seen = json.load(f)

    candidates = fetch_rss_items()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_rows = []

    for item in candidates:
        combined = f"{item['title']} {item['description']} {item['issuer']}"
        target = classify_match(combined)
        if not target:
            continue

        item_id = make_id(item["source"], item["title"], item["link"])
        if item_id in seen:
            continue

        seen[item_id] = today_str
        new_rows.append(
            [
                today_str,
                item["source"],
                item["issuer"],
                item["title"],
                target,
                item["closing_date"],
                item["link"],
            ]
        )

    if new_rows:
        ws = get_sheet()
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")

    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)

    print(f"Found {len(new_rows)} new matching RFPs.")


if __name__ == "__main__":
    main()
