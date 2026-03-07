import asyncio
import logging
import json
import os
from datetime import datetime, timedelta, timezone
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Browser, Page, TimeoutError
from selectolax.parser import HTMLParser

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger("ROXIE")

# Global storage for the final URL list
urls: dict[str, dict[str, str | float]] = {}

TAG = "ROXIE"
BASE_URL = "https://roxiestreams.info"

# Simple replacement for your custom Cache class
class SimpleCache:
    def __init__(self, name):
        self.filename = f"{name}.json"
        
    def load(self):
        if os.path.exists(self.filename):
            with open(self.filename, "r") as f:
                return json.load(f)
        return {}

    def write(self, data):
        with open(self.filename, "w") as f:
            json.dump(data, f, indent=4)

CACHE_FILE = SimpleCache(TAG)
HTML_CACHE = SimpleCache(f"{TAG}-html")

SPORT_URLS = {
    "Racing": urljoin(BASE_URL, "motorsports"),
} | {
    sport: urljoin(BASE_URL, sport.lower())
    for sport in ["Fighting", "MLB", "NBA", "NHL", "Soccer"]
}

async def refresh_html_cache(url: str, sport: str, now_ts: float, context) -> dict:
    events = {}
    page = await context.new_page()
    try:
        # Using playwright to get content as a replacement for network.request
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        html_data = await page.content()
    except Exception as e:
        log.warning(f"Failed to fetch {url}: {e}")
        return events
    finally:
        await page.close()

    soup = HTMLParser(html_data)
    for row in soup.css("table#eventsTable tbody tr"):
        if not (a_tag := row.css_first("td a")):
            continue

        event = a_tag.text(strip=True)
        href = a_tag.attributes.get("href")
        span = row.css_first("span.countdown-timer")

        if not href or not span:
            continue

        data_start = span.attributes["data-start"].rsplit(":", 1)[0]
        
        # Assume PST (UTC-8)
        pst = timezone(timedelta(hours=-8))
        try:
            event_dt = datetime.strptime(data_start, "%Y-%m-%d %H:%M").replace(tzinfo=pst)
        except ValueError:
            continue

        event_sport = next((k for k, v in SPORT_URLS.items() if v == url), "Live Event")
        key = f"[{event_sport}] {event} ({TAG})"

        events[key] = {
            "sport": event_sport,
            "event": event,
            "link": href,
            "event_ts": event_dt.timestamp(),
            "timestamp": now_ts,
        }
    return events

async def process_event(url: str, url_num: int, page: Page) -> str | None:
    captured: list[str] = []
    got_one = asyncio.Event()

    async def capture_req(request):
        if ".m3u8" in request.url and "chunklist" not in request.url:
            captured.append(request.url)
            got_one.set()

    page.on("request", capture_req)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=10000)

        # Click handling for stream buttons
        try:
            if btn := await page.wait_for_selector("button.streambutton:nth-of-type(1)", timeout=5000):
                await btn.click(force=True, click_count=2)
        except TimeoutError:
            pass

        try:
            if player := await page.wait_for_selector(".play-wrapper", timeout=5000):
                await player.click(force=True, click_count=3)
        except TimeoutError:
            pass

        try:
            await asyncio.wait_for(got_one.wait(), timeout=8)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
            return None

        if captured:
            log.info(f"URL {url_num}) Captured M3U8")
            return captured[0]

    except Exception as e:
        log.warning(f"URL {url_num}) Error: {e}")
    finally:
        page.remove_listener("request", capture_req)
    return None

async def get_events(cached_keys: list[str], context) -> list[dict]:
    now = datetime.now(timezone.utc)

    if not (events := HTML_CACHE.load()):
        log.info("Refreshing HTML cache")
        tasks = [refresh_html_cache(url, sport, now.timestamp(), context) for sport, url in SPORT_URLS.items()]
        results = await asyncio.gather(*tasks)
        events = {k: v for data in results for k, v in data.items()}
        HTML_CACHE.write(events)

    live = []
    start_ts = (now - timedelta(hours=1.5)).timestamp()
    end_ts = (now + timedelta(minutes=5)).timestamp()

    for k, v in events.items():
        if k in cached_keys or not (start_ts <= v["event_ts"] <= end_ts):
            continue
        live.append(v)
    return live

def save_to_m3u8(filename="rox.m3u8"):
    if not urls:
        log.warning("No URLs to save to M3U8.")
        return

    lines = ["#EXTM3U"]
    for name, data in urls.items():
        if stream_url := data.get("url"):
            tvg_id = data.get("id", "Live.Event.us")
            logo = data.get("logo", "")
            # Requirement: group-title set to "Roxiestreams"
            lines.append(f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="Roxiestreams",{name}')
            lines.append(stream_url)

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info(f"Saved {len(urls)} streams to {filename}")

async def scrape():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        cached_data = CACHE_FILE.load()
        urls.update({k: v for k, v in cached_data.items() if v.get("url")})
        
        log.info(f"Loaded {len(urls)} cached events. Scraping {BASE_URL}")

        events = await get_events(list(cached_data.keys()), context)
        if events:
            log.info(f"Processing {len(events)} new events")
            for i, ev in enumerate(events, start=1):
                page = await context.new_page()
                m3u8 = await process_event(ev["link"], i, page)
                await page.close()

                key = f"[{ev['sport']}] {ev['event']} ({TAG})"
                entry = {
                    "url": m3u8,
                    "logo": "", # Placeholder for leagues.get_tvg_info
                    "base": BASE_URL,
                    "timestamp": ev["event_ts"],
                    "id": "Live.Event.us",
                    "link": ev["link"],
                }
                cached_data[key] = entry
                if m3u8:
                    urls[key] = entry
            
            CACHE_FILE.write(cached_data)
        else:
            log.info("No new events to process.")

        save_to_m3u8()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(scrape())
