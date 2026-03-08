import asyncio
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from playwright.async_api import async_playwright, Page, TimeoutError
from selectolax.parser import HTMLParser

# Setup Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ROXIE")

TAG = "ROXIE"
BASE_URL = "https://roxiestreams.info"
# Global dictionary to hold results for the M3U8
captured_streams: dict[str, str] = {}

SPORT_URLS = {
    "NBA": urljoin(BASE_URL, "nba"),
    "MLB": urljoin(BASE_URL, "mlb"),
    "NHL": urljoin(BASE_URL, "nhl"),
    "Soccer": urljoin(BASE_URL, "soccer"),
    "Fighting": urljoin(BASE_URL, "fighting"),
    "Racing": urljoin(BASE_URL, "motorsports"),
}

async def get_active_events(context) -> list[dict]:
    """Scrapes the sport pages and returns currently active or upcoming events."""
    now = datetime.now(timezone.utc)
    # Window: Started up to 4 hours ago, or starting in the next 15 mins
    start_threshold = (now - timedelta(hours=4)).timestamp()
    end_threshold = (now + timedelta(minutes=15)).timestamp()
    
    found_events = []
    
    for sport, url in SPORT_URLS.items():
        log.info(f"Checking {sport} schedule...")
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            html = await page.content()
            soup = HTMLParser(html)
            
            for row in soup.css("table#eventsTable tbody tr"):
                a_tag = row.css_first("td a")
                span = row.css_first("span.countdown-timer")
                
                if not a_tag or not span:
                    continue

                event_name = a_tag.text(strip=True)
                href = a_tag.attributes.get("href")
                data_start = span.attributes.get("data-start", "").rsplit(":", 1)[0]

                if not href or not data_start:
                    continue

                # Convert PST site time to timestamp
                pst = timezone(timedelta(hours=-8))
                try:
                    event_dt = datetime.strptime(data_start, "%Y-%m-%d %H:%M").replace(tzinfo=pst)
                    ts = event_dt.timestamp()
                except ValueError:
                    continue

                if start_threshold <= ts <= end_threshold:
                    found_events.append({
                        "sport": sport,
                        "name": event_name,
                        "link": urljoin(BASE_URL, href) if not href.startswith("http") else href
                    })
        except Exception as e:
            log.warning(f"Error checking {sport}: {e}")
        finally:
            await page.close()
            
    return found_events

async def process_event(event_link: str, page: Page) -> str | None:
    """Navigates to the stream page and intercepts the M3U8 request."""
    captured_url = None
    got_one = asyncio.Event()

    async def request_handler(request):
        nonlocal captured_url
        # Filter for master manifests
        if ".m3u8" in request.url and "chunklist" not in request.url:
            captured_url = request.url
            got_one.set()

    page.on("request", request_handler)
    
    try:
        await page.goto(event_link, wait_until="networkidle", timeout=20000)
        
        # Site often requires interaction to load the player
        for selector in ["button.streambutton", ".play-wrapper", "#player"]:
            try:
                if await page.wait_for_selector(selector, timeout=4000):
                    await page.click(selector, force=True, click_count=2)
                    await asyncio.sleep(1)
            except:
                continue

        # Wait for the network request to be captured
        await asyncio.wait_for(got_one.wait(), timeout=12)
        return captured_url
    except:
        return None
    finally:
        page.remove_listener("request", request_handler)

def save_to_m3u8(streams: dict, filename="rox.m3u8"):
    """Writes the results to a standard M3U8 file."""
    if not streams:
        log.warning("No streams captured. M3U8 not updated.")
        return

    m3u_lines = ["#EXTM3U"]
    for name, stream_url in streams.items():
        # Added group-title="Roxiestreams" as requested
        m3u_lines.append(f'#EXTINF:-1 tvg-id="Live.Event.us" group-title="Roxiestreams",{name}')
        m3u_lines.append(stream_url)
        
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_lines))
    log.info(f"Successfully saved {len(streams)} streams to {filename}")

async def run_scraper():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Use a real user-agent to bypass basic bot detection
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        
        log.info("Starting fresh scrape (Cache Disabled)")
        events = await get_active_events(context)
        
        if not events:
            log.info("No live events found at this time.")
        else:
            log.info(f"Processing {len(events)} events...")
            for i, ev in enumerate(events, 1):
                log.info(f"[{i}/{len(events)}] Pulling: {ev['name']}")
                page = await context.new_page()
                m3u8_link = await process_event(ev["link"], page)
                await page.close()
                
                if m3u8_link:
                    display_name = f"[{ev['sport']}] {ev['name']} ({TAG})"
                    captured_streams[display_name] = m3u8_link
                    log.info(f"Captured: {ev['name']}")
                else:
                    log.warning(f"Failed to capture: {ev['name']}")

        save_to_m3u8(captured_streams)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_scraper())
