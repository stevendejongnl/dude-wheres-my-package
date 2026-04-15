"""Render dashboard / notifications / timeline mockups for gh-pages.

Example:

    uv run python -m dwmp.mockups --out /tmp/dwmp-gh-pages/mockups

The generator overwrites ``dashboard.html``, ``notifications.html`` and
``timeline.html`` inside ``--out``. ``extension.html`` is intentionally
untouched — see :mod:`dwmp.mockups.generate` for why.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from . import build_all


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output directory (typically <gh-pages-checkout>/mockups).",
    )
    args = parser.parse_args()
    written = asyncio.run(build_all(args.out))
    for path in written:
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
