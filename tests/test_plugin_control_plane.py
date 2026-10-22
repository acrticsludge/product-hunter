"""The control plane of the plugin: the action list and the argument vector.

The repository has no JavaScript test runner, so this module drives node
directly. The check is skipped when node is missing or when the node on PATH
cannot run a TypeScript file, because that is a runtime capability of the
machine and not a property of the change under test.
"""

from __future__ import annotations

import json
import re
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

# The driver reads the real plugin file: it imports it, calls the exported
# buildArgs and reads the exported action list. The dynamic import keeps the
# driver runnable as a script and as a module.
DRIVER = """\
async function main() {
  const { pathToFileURL } = await import("node:url")
  const [pluginPath] = process.argv.slice(2)
  const plugin = await import(pathToFileURL(pluginPath).href)
  const build = (input: any, sessionID: string | undefined) =>
    plugin.buildArgs(input, sessionID)
  process.stdout.write(
    JSON.stringify({
      actions: plugin.ACTIONS,
      status: build({ action: "status", research_id: "RH-20261022-0001" }, "session-1"),
      dash: build({ action: "status", research_id: "--not-an-option" }, undefined),
      create: build({ action: "create", objective: "Find the WhatsApp pain" }, undefined),
      other: build({ action: "status", objective: "not sent here" }, undefined),
      recover: build({ action: "recover" }, undefined),
    }),
  )
}

main()
"""


@pytest.fixture
def node(tmp_path) -> str:
    """The node executable, when it can run a TypeScript file."""
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("node is not on PATH, so the plugin cannot be exercised")
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
    """The node driver that reads the real plugin file."""
    path = tmp_path / "driver.ts"
    path.write_text(DRIVER, encoding="utf-8")
    return path


@pytest.fixture
def plugin(node, driver) -> dict:
    """What the driver read out of the plugin file."""
    result = subprocess.run(
        [node, str(driver), str(PLUGIN)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"node failed: {result.stderr.strip()}"
    return json.loads(result.stdout)


def _source() -> str:
    """The text of the plugin file."""
    return PLUGIN.read_text(encoding="utf-8")


def _spawn_call(source: str) -> str:
    """The spawn call of the plugin file, from the call to its closing brace."""
    start = source.index("spawn(")
    depth = 0
    for index in range(start, len(source)):
        if source[index] == "(":
            depth += 1
        elif source[index] == ")":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("the spawn call of the plugin file was never closed")


# --- the action list -------------------------------------------------------


def test_the_action_list_holds_the_nine_actions_and_the_two_new_ones(plugin) -> None:
    assert plugin["actions"] == [
        "create",
        "list",
        "status",
        "continue",
        "resume",
        "pause",
        "retry",
        "cancel",
        "log",
        "doctor",
        "recover",
    ]


# --- the argument vector ---------------------------------------------------


def test_build_args_returns_an_array_and_never_a_string(plugin) -> None:
    args = plugin["status"]
    assert isinstance(args, list)
    assert all(isinstance(item, str) for item in args)
    assert args[0] == "status"


def test_a_value_that_begins_with_a_dash_stays_a_value(plugin) -> None:
    # The equals form is what makes it a value and not an option.
    assert plugin["dash"] == ["status", "--research-id=--not-an-option"]


def test_the_objective_is_only_sent_for_the_create_action(plugin) -> None:
    assert plugin["create"] == ["create", "--objective=Find the WhatsApp pain"]
    assert "--objective" not in " ".join(plugin["other"])
    assert "--objective" not in " ".join(plugin["status"])


def test_the_research_id_is_sent_as_an_argument_and_the_session_with_it(
    plugin,
) -> None:
    assert plugin["status"] == [
        "status",
        "--research-id=RH-20261022-0001",
        "--chat-id=session-1",
    ]


def test_the_recover_action_takes_no_id_and_sends_none(plugin) -> None:
    assert plugin["recover"] == ["recover"]


# --- the shell -------------------------------------------------------------


def test_the_plugin_file_never_uses_a_shell() -> None:
    source = _source()
    assert "shell: true" not in source
    assert "exec(" not in source


def test_the_spawn_call_passes_an_argument_vector_and_no_template() -> None:
    call = _spawn_call(_source())
    assert '"uv"' in call
    # The arguments are an array, so no shell ever reads them.
    assert "[" in call
    assert "`" not in call


def test_the_objective_and_the_research_id_never_reach_the_spawn_call() -> None:
    source = _source()
    call = _spawn_call(source)
    # They travel in the argument vector, which the spawn call spreads.
    assert "--objective=" in source
    assert "--research-id=" in source
    assert "objective" not in call
    assert "research_id" not in call


# --- the tool result -------------------------------------------------------


def test_the_tool_result_is_the_v2_shape() -> None:
    source = _source()
    assert not re.search(r"output:\s*\{", source), "the tool still declares an output"
    assert re.search(r"\bcontent:\s*payload\.text", source)
    assert "metadata:" in source
