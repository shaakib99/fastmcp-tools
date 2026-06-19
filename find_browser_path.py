import subprocess
import platform
import json
import re
from fastmcp.tools import tool

@tool
async def find_browser_path_tool() -> str:
    """
    Use this tool BEFORE opening a browser.
    Finds the user's DEFAULT browser path using OS settings.
    Always call this before control_web_browser_tool.
    Never guess or hardcode a browser path.
    """
    system = platform.system()
    print('find_browser_path_tool called')

    if system == "Linux":
        try:
            # Step 1: get default browser desktop entry
            result = subprocess.run(
                "xdg-settings get default-web-browser",
                shell=True, capture_output=True, text=True, timeout=5
            )
            desktop_entry = result.stdout.strip()
            # e.g. "brave-browser.desktop" or "google-chrome.desktop"

            if not desktop_entry:
                raise ValueError("xdg-settings returned empty")

            # Step 2: find the .desktop file
            desktop_result = subprocess.run(
                f"find /usr/share/applications ~/.local/share/applications -name '{desktop_entry}' 2>/dev/null | head -1",
                shell=True, capture_output=True, text=True, timeout=5
            )
            desktop_file = desktop_result.stdout.strip()

            # Step 3: extract Exec= line from .desktop file
            exec_result = subprocess.run(
                f"grep '^Exec=' '{desktop_file}' | head -1",
                shell=True, capture_output=True, text=True, timeout=5
            )
            exec_line = exec_result.stdout.strip()
            # e.g. "Exec=/usr/bin/brave-browser-stable %U"

            # Step 4: extract just the binary path
            binary = exec_line.replace("Exec=", "").split()[0]

            return json.dumps({
                "os": system,
                "desktop_entry": desktop_entry,
                "recommended": binary  # e.g. /usr/bin/brave-browser-stable
            })

        except Exception as e:
            return json.dumps({"error": str(e), "os": system})

    elif system == "Windows":
        try:
            result = subprocess.run(
                r'reg query HKEY_CURRENT_USER\Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice /v ProgId',
                shell=True, capture_output=True, text=True, timeout=5
            )
            # Returns something like "ChromeHTML" or "BraveHTML" or "FirefoxURL"
            match = re.search(r'ProgId\s+REG_SZ\s+(\S+)', result.stdout)
            browser_id = match.group(1) if match else ""

            # Map ProgId to executable
            mapping = {
                "ChromeHTML":  r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                "BraveHTML":   r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                "FirefoxURL":  r"C:\Program Files\Mozilla Firefox\firefox.exe",
                "MSEdgeHTM":   r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            }
            binary = mapping.get(browser_id, "")

            return json.dumps({
                "os": system,
                "browser_id": browser_id,
                "recommended": binary
            })

        except Exception as e:
            return json.dumps({"error": str(e), "os": system})

    elif system == "Darwin":  # Mac
        try:
            result = subprocess.run(
                "defaults read com.apple.LaunchServices/com.apple.launchservices.secure LSHandlers | grep -A1 'https'",
                shell=True, capture_output=True, text=True, timeout=5
            )
            return json.dumps({
                "os": system,
                "raw": result.stdout.strip(),
                "note": "Parse bundle ID to get browser path"
            })
        except Exception as e:
            return json.dumps({"error": str(e), "os": system})

    else:
        return json.dumps({"error": f"Unsupported OS: {system}"})