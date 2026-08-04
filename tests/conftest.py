import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# Typer force-enables a color terminal when GITHUB_ACTIONS is set
# (typer/rich_utils.py FORCE_TERMINAL), which splits string literals like
# "--transfer" with ANSI codes and breaks help-text assertions. Its only
# override is _TYPER_FORCE_DISABLE_TERMINAL; NO_COLOR covers the plain Rich
# consoles. Both are read at import time, so set them before tests import
# anything. This makes CI output match local (non-TTY) output.
os.environ["_TYPER_FORCE_DISABLE_TERMINAL"] = "1"
os.environ["NO_COLOR"] = "1"
