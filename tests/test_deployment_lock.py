"""A frozen production install must include every declared runtime dependency."""

import re
import tomllib
from pathlib import Path


def test_production_lock_contains_current_runtime_dependencies():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    lock = tomllib.loads((root / "uv.lock").read_text())
    declared = project["project"]["dependencies"]
    package = next(p for p in lock["package"] if p["name"] == project["project"]["name"])
    runtime_names = {p["name"] for p in package["dependencies"]}
    locked_names = {p["name"] for p in lock["package"]}
    metadata = package["metadata"]["requires-dist"]
    for requirement in declared:
        name, specifier = re.fullmatch(r"([\w-]+)(.*)", requirement).groups()
        assert name in runtime_names, f"Frozen production install omits {name}"
        assert name in locked_names
        assert any(p["name"] == name and p.get("specifier", "") == specifier
                   and not p.get("marker") for p in metadata)
