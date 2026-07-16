"""Regression guards for keeping legacy Stem code out of production builds."""

from __future__ import annotations

import ast
from pathlib import Path


CORE_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = CORE_ROOT / "src" / "cuegrid"


def test_active_package_has_no_ffmpeg_or_legacy_stem_imports() -> None:
    """The CLI-reachable package must not have an import edge to legacy code."""
    excluded_sources = {
        PACKAGE_ROOT / "audio" / "legacy_stems.py",
        PACKAGE_ROOT / "nml" / "stems.py",
    }
    forbidden_modules = {
        "ffmpeg",
        "cuegrid.audio.legacy_stems",
        "cuegrid.nml.stems",
    }

    for source_path in PACKAGE_ROOT.rglob("*.py"):
        if source_path in excluded_sources:
            continue
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported = {node.module}
            else:
                continue
            assert not (imported & forbidden_modules), (
                f"production import edge in {source_path}: "
                f"{sorted(imported & forbidden_modules)}"
            )


def test_project_manifest_and_pyinstaller_recipe_exclude_legacy_stems() -> None:
    manifest = (CORE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    spec = (CORE_ROOT / "cuegrid.spec").read_text(encoding="utf-8")
    spec_tree = ast.parse(spec)

    collects_cuegrid = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "collect_submodules"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and node.args[0].value == "cuegrid"
        for node in ast.walk(spec_tree)
    )

    assert "ffmpeg-python" not in manifest
    assert not collects_cuegrid
    assert '"cuegrid.audio.legacy_stems"' in spec
    assert '"cuegrid.nml.stems"' in spec
    assert '"ffmpeg"' in spec
