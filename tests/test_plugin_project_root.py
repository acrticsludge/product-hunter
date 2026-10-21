"""The plugin's project-root resolver, exercised against the real plugin file.

The repository has no JavaScript test runner, so this module drives node
directly. The check is skipped when node is missing or when the node on PATH
cannot run a TypeScript file, because that is a runtime capability of the
machine and not a property of the change under test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from product_hunter.config import repo_root

PLUGIN = repo_root() / ".opencode" / "plugins" / "product-hunter.ts"

# The plugin file is not tracked, so a clone without it cannot run the check.
# That is an environment fact, the same class as a missing node.
if not PLUGIN.is_file():
    pytest.skip(f"the plugin file is not present at {PLUGIN}", allow_module_level=True)

# The plugin file is TypeScript and is loaded by opencode, so the check runs
# it the way the host does: node reads the file and strips the types.
DRIVER = """\
import { pathToFileURL } from "node:url"

const [pluginPath, start] = process.argv.slice(2)
const { findProjectRoot } = await import(pathToFileURL(pluginPath).href)
process.stdout.write(JSON.stringify(findProjectRoot(start) ?? null))
"""


@pytest.fixture
def node(tmp_path) -> str:
    """The node executable, when it can run a TypeScript file."""
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("node is not on PATH, so the plugin resolver cannot be exercised")
    probe = tmp_path / "probe.ts"
    probe.write_text(
        "const value: number = 1\nprocess.stdout.write(String(value))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, str(probe)], capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0 or result.stdout.strip() != "1":
        pytest.skip(
            "node on PATH cannot run a TypeScript file, which is a runtime "
            "capability and not a property of this change: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    return executable


@pytest.fixture
def driver(tmp_path) -> Path:
    """The node driver that calls the resolver in the real plugin file."""
    path = tmp_path / "driver.mjs"
    path.write_text(DRIVER, encoding="utf-8")
    return path


def _resolve(node: str, driver: Path, start: Path) -> str | None:
    """The directory the resolver returns for ``start``, or nothing."""
    result = subprocess.run(
        [node, str(driver), str(PLUGIN), str(start)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"node failed: {result.stderr.strip()}"
    value = json.loads(result.stdout)
    return None if value is None else str(value)


def test_the_plugin_directory_resolves_to_the_repository_root(node, driver) -> None:
    found = _resolve(node, driver, PLUGIN.parent)
    assert found is not None, "the plugin directory resolved to nothing"
    assert Path(found) == repo_root()
    assert (repo_root() / "pyproject.toml").is_file()


def test_a_directory_without_a_manifest_never_resolves_to_one(
    node, driver, tmp_path
) -> None:
    start = tmp_path / "no-manifest"
    start.mkdir()
    found = _resolve(node, driver, start)
    if found is not None:
        resolved = Path(found)
        assert resolved == start or resolved in start.parents
        assert (resolved / "pyproject.toml").is_file()
