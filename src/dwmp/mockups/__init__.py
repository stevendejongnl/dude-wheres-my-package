"""Static HTML mockup generator for the gh-pages landing site.

Renders the real FastAPI templates — seeded with
:mod:`dwmp.testing.fixtures` — into standalone HTML files suitable for
iframing on ``stevendejongnl.github.io/dude-wheres-my-package``. Lets the
landing site stay in lockstep with the app's UI without copy-pasting
markup.

    from pathlib import Path
    from dwmp.mockups import build_all

    await build_all(Path("/tmp/dwmp-gh-pages/mockups"))

See ``python -m dwmp.mockups --help`` for the CLI.
"""

from .generate import build_all

__all__ = ["build_all"]
