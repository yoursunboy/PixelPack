"""Entry point for the frozen build only.

PyInstaller executes its entry script as a top-level module named
``__main__`` with no parent package, so ``pixelpack/__main__.py`` — whose
relative import is exactly right for ``python -m pixelpack`` — cannot be used
as the target. This is the absolute-import equivalent.

Never imported at runtime by the package itself; ``python -m pixelpack`` and
``scripts/entry_point.py`` both end up in ``pixelpack.app.main``.
"""

from __future__ import annotations

import sys

from pixelpack.app import main

if __name__ == "__main__":
    sys.exit(main())
