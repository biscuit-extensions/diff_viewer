from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import tomllib

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.diff_viewer import setup
from src.diff_viewer.extension import (
    AlignedRow,
    DiffHunk,
    DiffViewer,
    compute_word_diff,
    tokenize_for_diff,
)

SAMPLE_ORIGINAL = """\
def calculate_area(radius):
    pi = 3.14
    return pi * radius * radius

def print_result():
    print("Done")
"""

SAMPLE_MODIFIED = """\
def calculate_area(radius):
    pi = 3.14159265
    return pi * (radius ** 2)

def print_header():
    print("Starting")

def print_result():
    print("Done")
"""


class TestDiffViewerExtension:
    def test_extension_setup_and_registration(self, app_instance) -> None:
        setup(app_instance.api)
        assert app_instance.api is not None

    def test_manifest_schema_and_dependencies(self) -> None:
        toml_path = Path(__file__).parent.parent / "extension.toml"
        assert toml_path.exists(), "extension.toml must exist"
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        assert data["extension"]["id"] == "diff_viewer"
        assert data["extension"]["version"] == "0.2.0"
        assert data["extension"]["author"] == "tomlin7"
        assert "dependencies" in data
        assert isinstance(data["dependencies"]["python"], list)
        assert isinstance(data["dependencies"]["tools"], list)
        assert "contributes" in data
        assert len(data["contributes"]["commands"]) >= 4

    def test_tokenize_for_diff(self) -> None:
        tokens = tokenize_for_diff('val = "hello, world!" + 42')
        assert "val" in tokens
        assert "=" in tokens
        assert "hello" in tokens
        assert "world" in tokens
        assert "42" in tokens

    def test_compute_word_diff(self) -> None:
        hl_a, hl_b = compute_word_diff("let count = 10", "let count = 20")
        assert len(hl_a) >= 1
        assert len(hl_b) >= 1
        # "10" vs "20" changed
        span_a = hl_a[0]
        assert "10" in "let count = 10"[span_a[0] : span_a[1]]
        span_b = hl_b[0]
        assert "20" in "let count = 20"[span_b[0] : span_b[1]]

    def test_compute_side_by_side_diff_identical(self, app_instance) -> None:
        ext = DiffViewer(app_instance.api)
        rows = ext.compute_side_by_side_diff("alpha\nbeta\ngamma", "alpha\nbeta\ngamma")
        assert len(rows) == 3
        for r in rows:
            assert r.row_type == "EQUAL"
            assert r.left_text == r.right_text

    def test_compute_side_by_side_diff_changes(self, app_instance) -> None:
        ext = DiffViewer(app_instance.api)
        rows = ext.compute_side_by_side_diff(SAMPLE_ORIGINAL, SAMPLE_MODIFIED)
        types = [r.row_type for r in rows]
        assert "EQUAL" in types
        assert "MODIFY" in types
        assert "INSERT" in types

        stats = ext.compute_stats(rows)
        assert stats["equal_lines"] > 0
        assert stats["modifications"] > 0
        assert stats["additions"] > 0
        assert stats["total_changes"] > 0

    def test_generate_and_parse_unified_diff(self, app_instance) -> None:
        ext = DiffViewer(app_instance.api)
        diff_str = ext.generate_unified_diff(
            SAMPLE_ORIGINAL, SAMPLE_MODIFIED, fromfile="a/calc.py", tofile="b/calc.py"
        )
        assert "--- a/calc.py" in diff_str
        assert "+++ b/calc.py" in diff_str
        assert "@@" in diff_str

        files = ext.parse_unified_diff(diff_str)
        assert len(files) == 1
        assert "calc.py" in files[0].source_file
        assert len(files[0].hunks) >= 1

        hunk = files[0].hunks[0]
        assert hunk.start_a >= 1
        assert len(hunk.lines) > 0
        line_types = {t for t, _ in hunk.lines}
        assert "+" in line_types
        assert "-" in line_types

    def test_apply_patch(self, app_instance) -> None:
        ext = DiffViewer(app_instance.api)
        diff_str = ext.generate_unified_diff(SAMPLE_ORIGINAL, SAMPLE_MODIFIED)
        patched = ext.apply_patch(SAMPLE_ORIGINAL, diff_str)
        assert patched.strip() == SAMPLE_MODIFIED.strip()

    def test_next_and_prev_diff_navigation(self, app_instance) -> None:
        ext = DiffViewer(app_instance.api)
        diff_str = ext.generate_unified_diff(SAMPLE_ORIGINAL, SAMPLE_MODIFIED)
        ext.parse_unified_diff(diff_str)

        assert len(ext.current_hunks) > 0
        initial_idx = ext.current_hunk_index
        next_h = ext.next_diff()
        assert next_h is not None

        prev_h = ext.prev_diff()
        assert prev_h is not None
        assert ext.current_hunk_index == initial_idx

    def test_compare_files_on_disk(self, app_instance) -> None:
        ext = DiffViewer(app_instance.api)
        ext.install()

        with tempfile.TemporaryDirectory() as tmp_dir:
            file_a = Path(tmp_dir) / "file_a.txt"
            file_b = Path(tmp_dir) / "file_b.txt"
            file_a.write_text("line 1\nline 2 original\nline 3\n", encoding="utf-8")
            file_b.write_text("line 1\nline 2 updated\nline 3\nline 4 added\n", encoding="utf-8")

            rows = ext.compare_files(str(file_a), str(file_b))
            assert len(rows) >= 3
            stats = ext.compute_stats(rows)
            assert stats["modifications"] >= 1 or stats["additions"] >= 1

    def test_compare_files_missing_file_handling(self, app_instance) -> None:
        ext = DiffViewer(app_instance.api)
        rows = ext.compare_files("nonexistent_a.txt", "nonexistent_b.txt")
        assert rows == []

    def test_compare_with_clipboard(self, app_instance) -> None:
        ext = DiffViewer(app_instance.api)
        ext.install()
        # Mock clipboard and active editor
        app_instance.api.base.clipboard_get = MagicMock(return_value="clipboard line 1\nline 2")
        rows = ext.compare_with_clipboard()
        assert isinstance(rows, list)