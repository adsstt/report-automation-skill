"""Small compatibility helpers shared by the public command-line scripts."""

from __future__ import annotations

import sys


def configure_console() -> None:
    """Keep Unicode status output from crashing legacy Windows consoles.

    Some Windows installations still expose a GBK/CP936 terminal.  The
    scripts use a few Unicode status symbols, so replace only characters the
    active console cannot encode instead of aborting the report workflow.
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
