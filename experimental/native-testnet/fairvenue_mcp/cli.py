"""``fairvenue-mcp`` entry point with a clear message when the extra is missing."""

from __future__ import annotations

import sys


def main() -> None:
    try:
        from .server import main as serve
    except ModuleNotFoundError as error:
        if (error.name or "").split(".")[0] not in ("mcp", "mcp_types", "pydantic"):
            raise
        print(
            "fairvenue-mcp needs the mcp extra. Install it with "
            "python -m pip install './experimental/native-testnet[mcp]' "
            "or run it through uvx (see docs/MCP.md).",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    serve()
