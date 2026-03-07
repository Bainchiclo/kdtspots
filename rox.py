import asyncio
import logging
import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, TimeoutError
from selectolax.parser import HTMLParser

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ROXIE")

TAG = "ROXIE"
BASE_URL = "https://roxiestreams.info"
urls: dict[str, dict] = {}

class SimpleCache:
    def __init__(self, name):
        self.path = f"{name}.json"
    def load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, "r") as f: return json.load(f)
        except: pass
        return {}
    def write(self, data):
        with open(self.path, "w") as f: json.dump(data, f, indent=4)

CACHE_FILE = SimpleCache(TAG)
HTML_CACHE = SimpleCache(f"{TAG}-html")

SPORT_URLS = {
    "NBA": urljoin(BASE_URL, "nba"),
    "MLB": urljoin(BASE_URL, "mlb"),
    "NHL": urljoin(BASE_URL, "nhl"),
    "Soccer": urljoin(BASE_URL, "soccer"),
    "Fighting": urljoin(BASE_URL, "fighting"),
    "Racing": urljoin(BASE_URL, "motorsports"),
}

async def get_page_html(context, url):
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        return await page.content()
    except Exception as e:
        log.warning(f"Error loading {url}: {e}")
        return None
    finally:
        await page.close()

async def parse_sport_page(html, sport_name, url):
    events = {}
    if not html: return events
    
    soup = HTMLParser(html)
    # The site uses a specific table ID for events
    for row in soup.css("table#eventsTable tbody tr"):
        a_tag = row.css_first("td a")
        span = row.css_first("span.countdown-timer")
        
        if not a_tag or not span: continue

        event_name = a_tag.text(strip=True)
        href = a_tag.attributes.get("href")
        # data-start is usually "YYYY-MM-DD HH:MM:SS"
        data_start = span.attributes.get("data-start", "").rsplit(":", 1)[0]

        if not href or not data_start: continue

        try:
            # Assuming site is PST (UTC-8)
            pst = timezone(timedelta(hours=-8))
            event_dt = datetime.strptime(data_start, "%Y-%m-%d %H:%M").replace(tzinfo=pst)
            event_ts = event_dt.timestamp()
        except: continue

        key = f"[{sport_name}] {event_name} ({TAG})"
        events[key] = {
            "sport": sport_name,
            "event": event_name,
            "link": href if href.startswith("http") else urljoin(BASE_URL, href),
            "event_ts": event_ts,
        }
    return events

async def process_event(url, page: Page):
    captured = []
    got_one = asyncio.Event()

    async def capture_req(request):
        # Filter for master m3u8 files, ignoring chunks/ads
        if ".m3u8" in request.url and "chunklist" not in request.url:
            captured.append(request.url)
            got_one.set()

    page.on("request", capture_req)
    try:
        await page.goto(url, wait_until="networkidle", timeout=15000)
        
        # Roxie specific: They often hide the player behind buttons
        for selector in ["button.streambutton", ".play-wrapper", "#player"]:
            try:
                if await page.wait_for_selector(selector, timeout=3000):
                    await page.click(selector, force=True, click_count=2)
                    await asyncio.sleep(1)
            except: continue

        await asyncio.wait_for(got_one.wait(), timeout=10)
        return captured[0] if captured else None
    except: return None
    finally: page.remove_listener("request", capture_req)

async def scrape():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        cached_data = CACHE_FILE.load()
        
        log.info("Fetching sports schedules...")
        all_events = {}
        for sport, url in SPORT_URLS.items():
            html = await get_page_html(context, url)
            events = await parse_sport_page(html, sport, url)
            all_events.update(events)

        # Filtering: Current time +/- a few hours
        now_ts = datetime.now(timezone.utc).timestamp()
        # Look for games that started up to 4 hours ago or start in the next 30 mins
        active_events = {k: v for k, v in all_events.items() 
                         if (now_ts - 14400) <= v["event_ts"] <= (now_ts + 1800)}

        log.info(f"Found {len(active_events)} active/upcoming events.")

        for i, (key, ev) in enumerate(active_events.items(), 1):
            if key in cached_data and cached_data[key].get("url"):
                urls[key] = cached_data[key]
                continue

            log.info(f"Processing ({i}/{len(active_events)}): {key}")
            page = await context.new_page()
            m3u8 = await process_event(ev["link"], page)
            await page.close()

            entry = {
                "url": m3u8,
                "id": "Live.Event.us",
                "logo": "",
                "timestamp": ev["event_ts"],
                "link": ev["link"]
            }
            cached_data[key] = entry
            if m3u8:
                urls[key] = entry
                log.info(f"Successfully pulled NBA/Sport stream for {key}")

        # Save Cache
        CACHE_FILE.write(cached_data)

        # Build M3U8
        m3u_lines = ["#EXTM3U"]
        for name, data in urls.items():
            if data.get("url"):
                m3u_lines.append(f'#EXTINF:-1 tvg-id="{data["id"]}" group-title="Roxiestreams",{name}')
                m3u_lines.append(data["url"])
        
        with open("rox.m3u8", "w") as f:
            f.write("\n".join(m3u_lines))
        
        log.info(f"File saved to rox.m3u8 with {len(m3u_lines)//2} streams.")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(scrape())
