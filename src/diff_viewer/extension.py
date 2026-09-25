from __future__ import annotations

import typing

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


class DiffViewer(Extension):
    """Side-by-side visual difference viewer for open editors"""

    def __init__(self, api: "ExtensionsAPI") -> None:
        super().__init__(api)
        self.api = api
        self.api.logger.info("Diff Viewer extension initialised.")

    def install(self) -> None:
        """Run when extension is loaded."""
        self.api.logger.info("Diff Viewer installed successfully.")
