"""CLI entry point for Smore ADA Accessibility Checker."""
import asyncio
import os
import sys

# Fix Windows console encoding for emoji/unicode
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

from smore_checker.core import run_audit


def main():
    if len(sys.argv) < 2:
        print("Usage: python check.py <smore-url>")
        print("Example: python check.py https://app.smore.com/n/jtycf")
        sys.exit(1)

    url = sys.argv[1]
    if "smore.com" not in url:
        print("Error: URL must be a Smore page (smore.com)")
        sys.exit(1)

    try:
        asyncio.run(run_audit(url))
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
