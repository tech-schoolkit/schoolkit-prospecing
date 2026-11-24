import hashlib
import json
import os
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import bs4
import gspread
import requests
from google.oauth2.service_account import Credentials

# Broad keyword sets (lean toward false positives so you don't miss opportunities)
PROCUREMENT_WORDS = [
    "rfp",
    "request for proposal",
    "tender",
    "tenders",
    "procurement",
    "purchasing",
    "bid",
    "bids",
    "solicitation",
    "vendor",
]

SCHOOLKIT_KEYWORDS = [
    "website",
    "web site",
    "cms",
    "content management",
    "web redesign",
    "website redesign",
    "hosting",
    "portal",
    "intranet",
]

APPAZUR_KEYWORDS = [
    "mass notification",
    "notification",
    "parent",
    "communication",
    "mobile app",
    "app",
    "absence",
    "attendance",
    "digital signage",
    "digital display",
    "signage",
    "display",
]

SUBPATH_CANDIDATES = [
    "/procurement",
    "/tenders",
    "/rfp",
    "/purchasing",
    "/bids",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; SchoolkitProspectingBot/1.0)"
}


def fetch_html(url: str, timeout: int = 10) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if resp.status_code == 200 and "text/html" in resp.headers.get("Content-Type", ""):
            return resp.text
    except Exception:
        return ""
    return ""


def extract_text_and_proc_links(base_url: str, html: str) -> tuple[str, list[str]]:
    soup = bs4.BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    base_netloc = urlparse(base_url).netloc.lower()

    found_links = []
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        label = " ".join(a.get_text(" ", strip=True).split()).lower()
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        if parsed.scheme not in ("http", "https"):
            continue
        combo = f"{full_url.lower()} {label}"
        if any(w in combo for w in ["rfp", "tender", "procurement", "purchasing", "bid", "solicitation"]):
            # Keep same domain or external portal links that clearly relate to tenders/procurement
            if parsed.netloc.lower() == base_netloc or any(
                k in parsed.netloc.lower() for k in ["bonfire", "merx", "bidsandtenders", "biddingo", "bcbid", "sasktenders", "nbon"]
            ):
                if full_url not in seen_urls:
                    seen_urls.add(full_url)
                    found_links.append(full_url)

    for path in SUBPATH_CANDIDATES:
        candidate_url = urljoin(base_url, path)
        if candidate_url not in seen_urls:
            seen_urls.add(candidate_url)
            found_links.append(candidate_url)

    return text, found_links[:8]


def classify_opportunity(text: str) -> str:
    low = text.lower()
    has_proc = any(w in low for w in PROCUREMENT_WORDS)
    if not has_proc:
        return ""

    sk = any(k in low for k in SCHOOLKIT_KEYWORDS)
    az = any(k in low for k in APPAZUR_KEYWORDS)

    if sk and az:
        return "Both (Schoolkit & Appazur)"
    if sk:
        return "Schoolkit"
    if az:
        return "Appazur"
    # Broad fallback: procurement page on a target school site
    return "Review (Procurement Mention)"


def make_id(org_name: str, url: str) -> str:
    raw = f"{org_name}|{url}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


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
    with open("school_targets.json", "r", encoding="utf-8") as f:
        targets = json.load(f)

    seen_path = "seen_rfps.json"
    with open(seen_path, "r", encoding="utf-8") as f:
        seen = json.load(f)

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_rows = []

    for org in targets:
        org_name = org.get("name", "")
        home_url = org.get("website", "").strip()
        province = org.get("province", "")
        if not home_url:
            continue

        home_html = fetch_html(home_url)
        if not home_html:
            continue

        _, candidate_links = extract_text_and_proc_links(home_url, home_html)

        pages_to_check = [(home_url, home_html)]
        for sub_url in candidate_links:
            sub_html = fetch_html(sub_url)
            if sub_html:
                pages_to_check.append((sub_url, sub_html))

        for page_url, page_html in pages_to_check:
            page_text = " ".join(bs4.BeautifulSoup(page_html, "html.parser").get_text(" ", strip=True).split())
            target = classify_opportunity(page_text)
            if not target:
                continue

            item_id = make_id(org_name, page_url)
            if item_id in seen:
                continue

            seen[item_id] = today_str
            issuer_label = f"{org_name} ({province})" if province else org_name
            title_label = f"Possible RFP/Procurement match on {page_url}"
            new_rows.append(
                [
                    today_str,
                    "School District Website",
                    issuer_label,
                    title_label,
                    target,
                    "",
                    page_url,
                ]
            )

    if new_rows:
        ws = get_sheet()
        ws.append_rows(new_rows, value_input_option="USER_ENTERED")

    with open(seen_path, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)

    print(f"Checked {len(targets)} school targets. Added {len(new_rows)} new rows.")


if __name__ == "__main__":
    main()
