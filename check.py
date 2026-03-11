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
from smore_checker.vision_router import clear_cache, set_verbose, reset_stats, get_stats, provider_name


def main():
    args = sys.argv[1:]

    # Handle flags
    verbose_mode = "--verbose" in args
    clear_cache_mode = "--clear-cache" in args

    # Remove flags from args
    args = [a for a in args if not a.startswith("--")]

    if clear_cache_mode:
        clear_cache()
        print("Vision cache cleared.")
        if not args:
            return

    if not args:
        print("Usage: python check.py [--verbose] [--clear-cache] <smore-url>")
        print("Example: python check.py https://app.smore.com/n/jtycf")
        print()
        print("Options:")
        print("  --verbose      Show detailed vision API call logging")
        print("  --clear-cache  Clear the vision result cache before running")
        sys.exit(1)

    url = args[0]
    if "smore.com" not in url:
        print("Error: URL must be a Smore page (smore.com)")
        sys.exit(1)

    # Startup message
    print(f"Using vision provider: {provider_name}")

    # Set up verbose vision logging
    if verbose_mode:
        set_verbose(True)
    reset_stats()

    try:
        asyncio.run(run_audit(url))
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)

    # Print stats summary
    stats = get_stats()
    print(f"\nVision stats: {stats['api_calls']} API calls, {stats['cache_hits']} cache hits, {stats['elapsed']:.1f}s total")


if __name__ == "__main__":
    main()
