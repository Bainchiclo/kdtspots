import requests

def scrape_m3u(url, keywords):
    try:
        # Fetch the content from the URL
        response = requests.get(url)
        response.raise_for_status()
        content = response.text
        
        lines = content.splitlines()
        filtered_output = ["#EXTM3U"] # Standard M3U header
        
        # Iterate through the lines to find matches
        for i in range(len(lines)):
            if lines[i].startswith("#EXTINF:"):
                # Check if any keyword exists in the current EXTINF line
                if any(key in lines[i] for key in keywords):
                    # Found a match, now collect everything until the next URL
                    temp_entry = []
                    curr_idx = i
                    
                    # Grab the EXTINF line and any subsequent options/metadata
                    # until we hit the actual stream URL
                    while curr_idx < len(lines):
                        line = lines[curr_idx]
                        temp_entry.append(line)
                        
                        # A URL usually doesn't start with # and is the end of an entry
                        if not line.startswith("#"):
                            break
                        curr_idx += 1
                    
                    filtered_output.extend(temp_entry)
        
        return "\n".join(filtered_output)

    except requests.exceptions.RequestException as e:
        return f"Error fetching the source: {e}"

# Configuration
SOURCE_URL = "https://raw.githubusercontent.com/Bainchiclo/kdtspots/refs/heads/main/liveeventsfilter.m3u8"
TARGET_KEYWORDS = ["(FAWA)", "(TVAPP)", "(ROXIE)"]

# Execute and save
result = scrape_m3u(SOURCE_URL, TARGET_KEYWORDS)

with open("livesmart.m3u8", "w", encoding="utf-8") as f:
    f.write(result)

print("Filtering complete. Results saved to filtered_list.m3u")
