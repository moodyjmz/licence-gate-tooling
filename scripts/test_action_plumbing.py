"""Tests for the plumbing inside the composite actions: temporary files and comments.

`test_gate_action.py` extracts a step's `run:` body and executes it under bash,
because controls written in YAML are the ones a pull request reaches first and were
the ones nothing ever ran. The same argument covers the two things here.

The first is where a step puts its files. A fixed `/tmp/...` path is fine on a
throwaway hosted runner and wrong everywhere else: two jobs on one self-hosted runner
write the same file, and `/tmp` survives between jobs there, so one pull request's
leftovers are read as the next one's input.

The second is the `script:` bodies. They are the steps that talk to the API, they are
where "posted nothing" and "found nothing" are easiest to confuse, and nothing in this
suite had ever run one.
"""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ACTIONS = ROOT / "actions"

# The two spellings of "a temporary directory, or /tmp when nothing says otherwise" -
# one for bash, one for the javascript steps. Both are already used in the actions.
ALLOWED_TMP = (
    re.compile(r"\$\{RUNNER_TEMP:-/tmp\}"),
    re.compile(r"process\.env\.RUNNER_TEMP\s*\|\|\s*'/tmp'"),
)
# Any other mention of a path under /tmp.
BARE_TMP = re.compile(r"(?<!-)/tmp/\S+")


def step_run(action, step_name):
    """The `run:` body of a named step, so the test runs what the runner runs."""
    definition = yaml.safe_load((ACTIONS / action / "action.yml").read_text())
    for step in definition["runs"]["steps"]:
        if step.get("name") == step_name:
            return step["run"]
    raise AssertionError(f"{action}: no step named {step_name!r}")


class TestTemporaryFilesAreScopedToTheRun(unittest.TestCase):
    """No step may name a file under `/tmp` directly.

    `/tmp/explicit-paths.txt` is the one that matters most: it carries the paths a
    reviewer asserted authorship over. Left on a self-hosted runner, the next pull
    request's run finds it and stamps a copyright header onto files nobody named -
    an assertion of authorship made by a leftover file.
    """

    def test_no_action_writes_to_a_fixed_tmp_path(self):
        for path in sorted(ACTIONS.glob("*/action.yml")):
            text = path.read_text()
            for pattern in ALLOWED_TMP:
                text = pattern.sub("RUNNER_TEMP", text)
            with self.subTest(action=path.parent.name):
                self.assertEqual(
                    sorted(set(BARE_TMP.findall(text))), [],
                    f"{path} names a fixed path under /tmp; concurrent jobs on one "
                    f"self-hosted runner share it, and it outlives the job")

    def test_the_asserted_paths_survive_the_move(self):
        """Moving the file without moving its reader would silently assert nothing.

        The script that reads the asserted paths takes them from `argv` as well, so
        the handoff is argv and the file never leaves the step that wrote it. What is
        being pinned here is that the paths still arrive - a reviewer who names three
        files and is answered "no new files needing a licence decision" has been told
        the gate ran when nothing was examined.
        """
        script = step_run("std-licence", "Apply")
        tmp = tempfile.mkdtemp()
        bin_dir = os.path.join(tmp, "bin")
        os.makedirs(bin_dir)
        seen = os.path.join(tmp, "argv.txt")
        stub = os.path.join(bin_dir, "python3")
        with open(stub, "w", encoding="utf-8") as fh:
            fh.write('#!/bin/sh\nfor a in "$@"; do echo "$a"; done > "%s"\n' % seen)
        os.chmod(stub, 0o755)

        asserted = ["src/a b.js", "src/*.js", "src/plain.js"]
        with open(os.path.join(tmp, "explicit-paths.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(asserted) + "\n")

        result = subprocess.run(
            ["bash", "-c", script], cwd=tmp,
            env={"PATH": bin_dir + os.pathsep + os.environ["PATH"], "RUNNER_TEMP": tmp,
                 "GITHUB_ACTION_PATH": tmp, "BASE_SHA": "b" * 40},
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(seen, encoding="utf-8") as fh:
            argv = fh.read().splitlines()
        self.assertEqual(argv[-3:], asserted,
                         "the asserted paths did not reach the script")

    def test_asserted_paths_that_were_never_written_stop_the_step(self):
        """"Nobody named a file" and "the step that collects them did not run" must not
        share an exit code or a report."""
        script = step_run("std-licence", "Apply")
        tmp = tempfile.mkdtemp()
        result = subprocess.run(
            ["bash", "-c", script], cwd=tmp,
            env={"PATH": os.environ["PATH"], "RUNNER_TEMP": tmp,
                 "GITHUB_ACTION_PATH": tmp, "BASE_SHA": "b" * 40},
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("No new files needing a licence decision",
                         result.stdout + result.stderr)

    def test_every_action_still_parses(self):
        for path in sorted(ACTIONS.glob("*/action.yml")):
            with self.subTest(action=path.parent.name):
                self.assertIn("steps", yaml.safe_load(path.read_text())["runs"])


if __name__ == "__main__":
    unittest.main()
