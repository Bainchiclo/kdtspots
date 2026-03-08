import asyncio
import os
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser, Page, TimeoutError
from selectolax.parser import HTMLParser

# Maintaining your internal imports
from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "ROXIE"
BASE_URL = "https://roxiestreams.info"

# Using your Cache classes
CACHE_FILE = Cache(TAG, exp=10_800)
HTML_CACHE = Cache(f"{TAG}-html", exp=19_800)

SPORT_URLS = {
    "Racing": urljoin(BASE_URL, "motorsports"),
} | {
    sport: urljoin(BASE_URL, sport.lower())
    for sport in ["Fighting", "MLB", "NBA", "NHL", "Soccer"]
}

async def refresh_html_cache(url: str, sport: str, now_ts: float) -> dict:
    events = {}
    if not (html_data := await network.request(url, log=log)):
        return events

    soup = HTMLParser(html_data.content)
    for row in soup.css("table#eventsTable tbody tr"):
        if not (a_tag := row.css_first("td a")):
            continue

        event = a_tag.text(strip=True)
        if not (href := a_tag.attributes.get("href")):
            continue

        if not (span := row.css_first("span.countdown-timer")):
            continue

        data_start = span.attributes["data-start"].rsplit(":", 1)[0]
        # Using your Time util
        event_dt = Time.from_str(data_start, timezone="PST")
        event_sport = next((k for k, v in SPORT_URLS.items() if v == url), "Live Event")

        key = f"[{event_sport}] {event} ({TAG})"
        events[key] = {
            "sport": event_sport,
            "event": event,
            "link": href if href.startswith("http") else urljoin(BASE_URL, href),
            "event_ts": event_dt.timestamp(),
            "timestamp": now_ts,
        }
    return events

async def process_event(url: str, url_num: int, page: Page) -> str | None:
    captured: list[str] = []
    got_one = asyncio.Event()

    # Using your network handler
    handler = partial(network.capture_req, captured=captured, got_one=got_one)
    page.on("request", handler)

    try:
        # Increased timeout for NBA pages which can be heavy
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=12_000)
        if not resp or resp.status != 200:
            return None

        # Roxie specific: Interaction often required to trigger manifest
        try:
            if btn := await page.wait_for_selector("button.streambutton:nth-of-type(1)", timeout=5000):
                await btn.click(force=True, click_count=2)
        except TimeoutError:
            pass

        try:
            if player := await page.wait_for_selector(".play-wrapper", timeout=5000):
                # Double click instead of triple to ensure play action
                await player.click(force=True, click_count=2)
        except TimeoutError:
            pass

        try:
            # Wait for manifest capture
            await asyncio.wait_for(got_one.wait(), timeout=10)
            return captured[0]
        except asyncio.TimeoutError:
            return None

    except Exception as e:
        log.warning(f"URL {url_num}) Error: {e}")
        return None
    finally:
        page.remove_listener("request", handler)

async def get_events(cached_keys: list[str]) -> list[dict]:
    now = Time.clean(Time.now())
    if not (events := HTML_CACHE.load()):
        log.info("Refreshing HTML cache")
        tasks = [refresh_html_cache(url, sport, now.timestamp()) for sport, url in SPORT_URLS.items()]
        results = await asyncio.gather(*tasks)
        events = {k: v for data in results for k, v in data.items()}
        HTML_CACHE.write(events)

    live = []
    # Generous 4-hour start window for active games
    start_ts = (now.delta(hours=-4)).timestamp()
    end_ts = (now.delta(minutes=5)).timestamp()

    for k, v in events.items():
        if k in cached_keys or not (start_ts <= v["event_ts"] <= end_ts):
            continue
        live.append(v)
    return live

def save_to_m3u8(filename="rox.m3u8"):
    """Saves streams with the requested group-title=Roxiestreams"""
    if not urls:
        log.warning("No streams found to save.")
        return

    m3u_lines = ["#EXTM3U"]
    for name, data in urls.items():
        if not (stream_url := data.get("url")):
            continue

        tvg_id = data.get("id", "Live.Event.us")
        logo = data.get("logo", "")
        
        # Meta line with group-title
        inf_line = f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-logo="{logo}" group-title="Roxiestreams",{name}'
        m3u_lines.append(inf_line)
        m3u_lines.append(stream_url)

    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_lines))
    log.info(f"Playlist saved to {filename} with group-title 'Roxiestreams'.")

async def scrape(browser: Browser) -> None:
    cached_data = CACHE_FILE.load()
    urls.update({k: v for k, v in cached_data.items() if v.get("url")})

    log.info(f"Loaded {len(urls)} cached events. Scraping {BASE_URL}")

    events = await get_events(list(cached_data.keys()))
    if events:
        log.info(f"Processing {len(events)} new events")
        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    m3u8 = await process_event(ev["link"], i, page)
                    
                    sport, event, ts = ev["sport"], ev["event"], ev["event_ts"]
                    tvg_id, logo = leagues.get_tvg_info(sport, event)
                    key = f"[{sport}] {event} ({TAG})"

                    entry = {
                        "url": m3u8,
                        "logo": logo,
                        "base": BASE_URL,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": ev["link"],
                    }
                    cached_data[key] = entry
                    if m3u8:
                        urls[key] = entry
        
        CACHE_FILE.write(cached_data)
    else:
        log.info("No new events to process.")

    save_to_m3u8()

if __name__ == "__main__":
    # Example local runner (assumes playwright installed)
    from playwright.async_api import async_playwright
    async def main():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            await scrape(browser)
            await browser.close()
    asyncio.run(main())
