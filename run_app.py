"""
Querity App Launcher
Starts the FastAPI backend and automatically opens the Home Screen in your default browser.
"""

import sys
import time
import webbrowser
import threading
from pathlib import Path

def open_browser():
    time.sleep(1.5)
    home_path = Path(__file__).resolve().parent / "frontend" / "index.html"
    if home_path.exists():
        webbrowser.open(home_path.as_uri())

if __name__ == "__main__":
    import uvicorn

    print("=" * 65)
    print("Starting Querity Text-to-SQL Server on http://127.0.0.1:8000")
    print("Opening frontend/index.html (Home Screen) in your default browser...")
    print("=" * 65)

    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=True)

