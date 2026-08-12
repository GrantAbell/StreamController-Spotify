"""Make the plugin importable as a package outside StreamController.

StreamController imports the plugin as `plugins.<plugin_id>`, so every internal
import is relative and the package has to be loaded under some valid name. The
repository directory contains hyphens, so it is loaded here as
`spotify_essentials`.

Only spotify/ and rendering/ are imported by these tests. actions/, ui/ and
main.py pull in GTK and StreamController and cannot load in a bare process,
which is exactly why the rules those actions rely on live in spotify/state.py,
spotify/format.py and spotify/cache.py where they can be tested.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "spotify_essentials"

if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = module
    spec.loader.exec_module(module)
