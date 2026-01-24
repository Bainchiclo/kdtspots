import requests
import re

PLAYLIST_URL = "https://raw.githubusercontent.com/doms9/iptv/refs/heads/default/M3U8/events.m3u8"
OUTPUT_FILE = "cdntv.m3u8"

KEYWORDS = ["(TOTALSPRTK)", "(STRMHUB)","(ROXIE)","(TVPASS)"]


def download_m3u(url: str) -> str:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.text


def extract_tvg_name(extinf_line: str) -> str:
    match = re.search(r'tvg-name="([^"]*)"', extinf_line, re.IGNORECASE)
    return match.group(1) if match else ""


def filter_m3u(content: str) -> str:
    lines = content.splitlines()
    filtered_lines = ["#EXTM3U"]

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("#EXTINF"):
            tvg_name = extract_tvg_name(line)

            # Keep if ANY keyword is present
            if any(keyword.lower() in tvg_name.lower() for keyword in KEYWORDS):
                filtered_lines.append(lines[i])
                i += 1

                # Keep all lines until the next #EXTINF (URL, #EXTVLCOPT, etc.)
                while i < len(lines) and not lines[i].strip().startswith("#EXTINF"):
                    filtered_lines.append(lines[i])
                    i += 1
                continue

        i += 1

    return "\n".join(filtered_lines) + "\n"


def main():
    try:
        m3u_content = download_m3u(PLAYLIST_URL)
        filtered_m3u = filter_m3u(m3u_content)

        if len(filtered_m3u.strip()) > len("#EXTM3U"):
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                f.write(filtered_m3u)
            print(f"Filtered playlist saved to: {OUTPUT_FILE}")
        else:
            print("No channels matched the filter.")

    except requests.RequestException as e:
        print(f"Download error: {e}")
    except OSError as e:
        print(f"File error: {e}")


if __name__ == "__main__":
    main()
