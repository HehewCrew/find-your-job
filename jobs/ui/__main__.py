"""Start the web page for the daily loop.

    python -m jobs.ui                 # opens http://127.0.0.1:8765/ in your browser
    python -m jobs.ui --port 9000
    python -m jobs.ui --no-browser    # just print the address

Only this computer can reach it. Press Ctrl+C in this window to stop it.
"""

from __future__ import annotations

import argparse
import sys
import webbrowser

from jobs.ui.paths import Paths
from jobs.ui.server import make_server

TRIES = 20


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobs.ui", description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765, help="first port to try (default 8765)")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = ap.parse_args(argv)

    paths = Paths.default()
    srv = None
    for port in range(args.port, args.port + TRIES):
        try:
            srv = make_server(paths, port)
            break
        except OSError:
            continue
    if srv is None:
        print(
            f"error: ports {args.port}-{args.port + TRIES - 1} are all in use; "
            "pass --port with a free one.",
            file=sys.stderr,
        )
        return 1

    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"Find Your Job is running at {url}")
    print("Press Ctrl+C here to stop it.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
