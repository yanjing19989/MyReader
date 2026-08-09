import argparse
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from app.main import app


def open_browser_when_ready(host: str, port: int) -> None:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::", "[::]"} else host
    if ":" in browser_host and not browser_host.startswith("["):
        browser_host = f"[{browser_host}]"
    url = f"http://{browser_host}:{port}"

    def wait_for_server() -> None:
        for _ in range(150):
            try:
                with urllib.request.urlopen(f"{url}/api/health", timeout=0.2) as response:
                    if response.status == 200:
                        webbrowser.open(url)
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)

    threading.Thread(target=wait_for_server, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(prog="MyReader")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not args.no_browser:
        open_browser_when_ready(args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
