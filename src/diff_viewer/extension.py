from __future__ import annotations

import difflib
import os
import re
import typing
from dataclasses import dataclass, field
from pathlib import Path

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


@dataclass
class AlignedRow:
    """Represents an aligned row in a side-by-side diff view."""

    row_type: str  # "EQUAL", "INSERT", "DELETE", "MODIFY"
    left_lineno: int | None
    right_lineno: int | None
    left_text: str
    right_text: str
    left_highlights: list[tuple[int, int]] = field(default_factory=list)
    right_highlights: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class DiffHunk:
    """Represents a parsed unified diff hunk."""

    start_a: int
    count_a: int
    start_b: int
    count_b: int
    header: str = ""
    lines: list[tuple[str, str]] = field(default_factory=list)  # (type: ' '|'-'|'+', content)


@dataclass
class DiffFile:
    """Represents differences for a single file in a patch."""

    source_file: str
    target_file: str
    hunks: list[DiffHunk] = field(default_factory=list)


def tokenize_for_diff(text: str) -> list[str]:
    """Tokenize a line of code into words and punctuation tokens."""
    return re.findall(r"\w+|\s+|[^\w\s]", text)


def compute_word_diff(
    text_a: str, text_b: str
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Compute character highlight spans for intra-line word-level differences."""
    tokens_a = tokenize_for_diff(text_a)
    tokens_b = tokenize_for_diff(text_b)

    # Map token index to character offset range
    offsets_a: list[tuple[int, int]] = []
    curr = 0
    for tok in tokens_a:
        offsets_a.append((curr, curr + len(tok)))
        curr += len(tok)

    offsets_b: list[tuple[int, int]] = []
    curr = 0
    for tok in tokens_b:
        offsets_b.append((curr, curr + len(tok)))
        curr += len(tok)

    matcher = difflib.SequenceMatcher(None, tokens_a, tokens_b)
    highlights_a: list[tuple[int, int]] = []
    highlights_b: list[tuple[int, int]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            if i1 < len(offsets_a) and i2 <= len(offsets_a) and i1 < i2:
                start_char = offsets_a[i1][0]
                end_char = offsets_a[i2 - 1][1]
                highlights_a.append((start_char, end_char))
        if tag in ("replace", "insert"):
            if j1 < len(offsets_b) and j2 <= len(offsets_b) and j1 < j2:
                start_char = offsets_b[j1][0]
                end_char = offsets_b[j2 - 1][1]
                highlights_b.append((start_char, end_char))

    return highlights_a, highlights_b


class DiffViewer(Extension):
    """Side-by-side visual difference viewer for open editors."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.current_rows: list[AlignedRow] = []
        self.current_hunks: list[DiffHunk] = []
        self.current_hunk_index: int = 0
        self.statusbar_button = None

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("Diff Viewer extension initialised.")

    def compute_side_by_side_diff(
        self, original: str, modified: str
    ) -> list[AlignedRow]:
        """Compute aligned side-by-side rows with intra-line word-level highlights."""
        lines_a = original.splitlines()
        lines_b = modified.splitlines()

        matcher = difflib.SequenceMatcher(None, lines_a, lines_b)
        rows: list[AlignedRow] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for idx in range(i2 - i1):
                    rows.append(
                        AlignedRow(
                            row_type="EQUAL",
                            left_lineno=i1 + idx + 1,
                            right_lineno=j1 + idx + 1,
                            left_text=lines_a[i1 + idx],
                            right_text=lines_b[j1 + idx],
                        )
                    )
            elif tag == "delete":
                for idx in range(i2 - i1):
                    rows.append(
                        AlignedRow(
                            row_type="DELETE",
                            left_lineno=i1 + idx + 1,
                            right_lineno=None,
                            left_text=lines_a[i1 + idx],
                            right_text="",
                        )
                    )
            elif tag == "insert":
                for idx in range(j2 - j1):
                    rows.append(
                        AlignedRow(
                            row_type="INSERT",
                            left_lineno=None,
                            right_lineno=j1 + idx + 1,
                            left_text="",
                            right_text=lines_b[j1 + idx],
                        )
                    )
            elif tag == "replace":
                del_count = i2 - i1
                ins_count = j2 - j1
                paired = min(del_count, ins_count)

                # Pair corresponding modified lines
                for k in range(paired):
                    la = lines_a[i1 + k]
                    lb = lines_b[j1 + k]
                    hl_a, hl_b = compute_word_diff(la, lb)
                    rows.append(
                        AlignedRow(
                            row_type="MODIFY",
                            left_lineno=i1 + k + 1,
                            right_lineno=j1 + k + 1,
                            left_text=la,
                            right_text=lb,
                            left_highlights=hl_a,
                            right_highlights=hl_b,
                        )
                    )

                # Remaining deletions on left
                if del_count > paired:
                    for k in range(paired, del_count):
                        rows.append(
                            AlignedRow(
                                row_type="DELETE",
                                left_lineno=i1 + k + 1,
                                right_lineno=None,
                                left_text=lines_a[i1 + k],
                                right_text="",
                            )
                        )
                # Remaining insertions on right
                elif ins_count > paired:
                    for k in range(paired, ins_count):
                        rows.append(
                            AlignedRow(
                                row_type="INSERT",
                                left_lineno=None,
                                right_lineno=j1 + k + 1,
                                left_text="",
                                right_text=lines_b[j1 + k],
                            )
                        )

        self.current_rows = rows
        return rows

    def compute_stats(self, rows: list[AlignedRow]) -> dict[str, int]:
        """Compute statistical count of changes from aligned rows."""
        additions = sum(1 for r in rows if r.row_type == "INSERT")
        deletions = sum(1 for r in rows if r.row_type == "DELETE")
        modifications = sum(1 for r in rows if r.row_type == "MODIFY")
        equal_lines = sum(1 for r in rows if r.row_type == "EQUAL")

        return {
            "additions": additions,
            "deletions": deletions,
            "modifications": modifications,
            "equal_lines": equal_lines,
            "total_changes": additions + deletions + modifications,
        }

    def generate_unified_diff(
        self,
        original: str,
        modified: str,
        fromfile: str = "original",
        tofile: str = "modified",
        context_lines: int = 3,
    ) -> str:
        """Generate a standard unified diff string."""
        lines_a = original.splitlines(keepends=True)
        lines_b = modified.splitlines(keepends=True)
        diff_iter = difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=fromfile,
            tofile=tofile,
            n=context_lines,
        )
        return "".join(diff_iter)

    def parse_unified_diff(self, diff_text: str) -> list[DiffFile]:
        """Parse unified diff text into structured DiffFile and DiffHunk models."""
        files: list[DiffFile] = []
        current_file: DiffFile | None = None
        current_hunk: DiffHunk | None = None

        hunk_header_re = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)")

        for line in diff_text.splitlines():
            if line.startswith("--- "):
                src_path = line[4:].strip().lstrip("ab/")
                current_file = DiffFile(source_file=src_path, target_file="")
                files.append(current_file)
                current_hunk = None
                continue

            if line.startswith("+++ "):
                if current_file:
                    current_file.target_file = line[4:].strip().lstrip("ab/")
                continue

            match = hunk_header_re.match(line)
            if match:
                start_a = int(match.group(1))
                count_a = int(match.group(2)) if match.group(2) else 1
                start_b = int(match.group(3))
                count_b = int(match.group(4)) if match.group(4) else 1
                header_extra = match.group(5).strip()

                current_hunk = DiffHunk(
                    start_a=start_a,
                    count_a=count_a,
                    start_b=start_b,
                    count_b=count_b,
                    header=header_extra,
                    lines=[],
                )
                if current_file:
                    current_file.hunks.append(current_hunk)
                continue

            if current_hunk:
                if line.startswith("+"):
                    current_hunk.lines.append(("+", line[1:]))
                elif line.startswith("-"):
                    current_hunk.lines.append(("-", line[1:]))
                elif line.startswith(" "):
                    current_hunk.lines.append((" ", line[1:]))
                elif line == "":
                    current_hunk.lines.append((" ", ""))

        # Collect all hunks for navigation
        all_hunks: list[DiffHunk] = []
        for f in files:
            all_hunks.extend(f.hunks)
        self.current_hunks = all_hunks
        self.current_hunk_index = 0
        return files

    def apply_patch(self, source_text: str, patch_text: str) -> str:
        """Apply a unified diff patch to source text and return the patched text."""
        files = self.parse_unified_diff(patch_text)
        if not files or not files[0].hunks:
            return source_text

        source_lines = source_text.splitlines()
        result_lines: list[str] = []
        src_idx = 0

        for hunk in files[0].hunks:
            target_start_a = hunk.start_a - 1 if hunk.start_a > 0 else 0

            # Copy unchanged lines before this hunk
            while src_idx < target_start_a and src_idx < len(source_lines):
                result_lines.append(source_lines[src_idx])
                src_idx += 1

            # Process hunk edits
            for edit_type, line_content in hunk.lines:
                if edit_type == " ":
                    if src_idx < len(source_lines):
                        result_lines.append(source_lines[src_idx])
                        src_idx += 1
                    else:
                        result_lines.append(line_content)
                elif edit_type == "-":
                    # Discard removed line from source
                    src_idx += 1
                elif edit_type == "+":
                    # Insert added line
                    result_lines.append(line_content)

        # Copy any remaining source lines after last hunk
        while src_idx < len(source_lines):
            result_lines.append(source_lines[src_idx])
            src_idx += 1

        return "\n".join(result_lines)

    def next_diff(self) -> DiffHunk | None:
        """Navigate to the next diff hunk."""
        if not self.current_hunks:
            return None
        self.current_hunk_index = (self.current_hunk_index + 1) % len(self.current_hunks)
        hunk = self.current_hunks[self.current_hunk_index]
        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info(
                f"Diff Hunk {self.current_hunk_index + 1} of {len(self.current_hunks)} (Line {hunk.start_b})"
            )
        return hunk

    def prev_diff(self) -> DiffHunk | None:
        """Navigate to the previous diff hunk."""
        if not self.current_hunks:
            return None
        self.current_hunk_index = (self.current_hunk_index - 1) % len(self.current_hunks)
        hunk = self.current_hunks[self.current_hunk_index]
        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info(
                f"Diff Hunk {self.current_hunk_index + 1} of {len(self.current_hunks)} (Line {hunk.start_b})"
            )
        return hunk

    def compare_files(self, file_a: str, file_b: str) -> list[AlignedRow]:
        """Compare two files on disk and open diff editor."""
        path_a = Path(file_a)
        path_b = Path(file_b)

        if not path_a.exists() or not path_b.exists():
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.error("Cannot compare: one or both files do not exist.")
            return []

        text_a = path_a.read_text(encoding="utf-8", errors="replace")
        text_b = path_b.read_text(encoding="utf-8", errors="replace")

        rows = self.compute_side_by_side_diff(text_a, text_b)
        stats = self.compute_stats(rows)

        # Update statusbar statistics if available
        if self.statusbar_button and hasattr(self.statusbar_button, "set_text"):
            self.statusbar_button.set_text(
                f"Diff: +{stats['additions']} -{stats['deletions']} ~{stats['modifications']}"
            )

        # Open in editor if open_diff_editor exists
        if hasattr(self.api, "editors") and hasattr(self.api.editors, "open_diff_editor"):
            try:
                self.api.editors.open_diff_editor(str(path_b), exists=True)
            except Exception:
                pass

        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info(
                f"Compared files: +{stats['additions']} -{stats['deletions']} ~{stats['modifications']} lines changed"
            )

        return rows

    def compare_with_clipboard(self) -> list[AlignedRow]:
        """Compare active editor text against clipboard contents."""
        clipboard_text = ""
        if hasattr(self.api, "base") and hasattr(self.api.base, "clipboard_get"):
            try:
                clipboard_text = self.api.base.clipboard_get()
            except Exception:
                clipboard_text = ""

        active_editor = getattr(self.api.editors, "active_editor", None)
        editor_text = ""
        if active_editor and hasattr(active_editor, "text"):
            try:
                editor_text = active_editor.text.get_all_text()
            except Exception:
                pass

        rows = self.compute_side_by_side_diff(editor_text, clipboard_text)
        stats = self.compute_stats(rows)

        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info(
                f"Clipboard diff: +{stats['additions']} -{stats['deletions']} ~{stats['modifications']}"
            )

        return rows

    def install(self) -> None:
        """Register Diff Viewer commands and statusbar button."""
        if hasattr(self.api, "commands") and hasattr(self.api.commands, "register_command"):
            self.api.commands.register_command("diff_viewer.compare_files", self.compare_files)
            self.api.commands.register_command(
                "diff_viewer.compare_with_clipboard", self.compare_with_clipboard
            )
            self.api.commands.register_command("diff_viewer.next_diff", self.next_diff)
            self.api.commands.register_command("diff_viewer.prev_diff", self.prev_diff)

        if hasattr(self.api, "statusbar") and hasattr(self.api.statusbar, "add_button"):
            try:
                self.statusbar_button = self.api.statusbar.add_button(
                    text="Diff: 0",
                    icon="diff",
                    callback=self.compare_with_clipboard,
                    description="Diff Viewer status and comparison",
                )
            except Exception:
                pass

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("Diff Viewer installed successfully.")
