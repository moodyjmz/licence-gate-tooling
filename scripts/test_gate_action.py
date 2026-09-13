"""Tests for the guard steps inside the composite actions.

The actions were untested. Every test in this suite exercised Python, and the
controls written in YAML - which are the ones a pull request can reach first - were
read by people and never run by anything. The caller-input bypass lived there.

These tests extract a step's `run:` body from the action definition and execute it
under bash with the environment GitHub would give it. That keeps the test honest:
it runs the same text that runs on the runner, so an edit to the action that breaks
the control fails here rather than in production.
"""

import os
import subprocess
import unittest
from pathlib import Path

import yaml

ACTIONS = Path(__file__).resolve().parent.parent / "actions"

GOOD_BASE = "1111111111111111111111111111111111111111"
GOOD_HEAD = "2222222222222222222222222222222222222222"


def step_script(action, step_name):
    """The `run:` body of a named step, so the test runs what the runner runs."""
    definition = yaml.safe_load((ACTIONS / action / "action.yml").read_text())
    for step in definition["runs"]["steps"]:
        if step.get("name") == step_name:
            return step["run"]
    raise AssertionError(f"{action}: no step named {step_name!r}")


def run_guard(script, **env):
    """Run the guard with a clean environment; unset variables are empty, as on a runner."""
    return subprocess.run(
        ["bash", "-c", script],
        env={"PATH": os.environ["PATH"], **{k: v for k, v in env.items() if v is not None}},
        capture_output=True,
        text=True,
    )


class TestGateInputsAreVerified(unittest.TestCase):
    """The gate must not accept the commits it compares from a file the PR can edit.

    Found by red-team review, not by any of the five security audits: on a
    `pull_request` event the workflow that runs is the pull request's own copy, so
    the caller is the thing being gated. The event payload is written by GitHub and
    is the only statement of the pull request that the branch cannot author.
    """

    def setUp(self):
        self.script = step_script("gate", "Verify the inputs against the event")

    def guard(self, **overrides):
        env = {
            "EVENT_NAME": "pull_request",
            "INPUT_BASE": GOOD_BASE,
            "INPUT_HEAD": GOOD_HEAD,
            "INPUT_AUTHOR": "contributor",
            "EVENT_BASE": GOOD_BASE,
            "EVENT_HEAD": GOOD_HEAD,
            "EVENT_AUTHOR": "contributor",
        }
        env.update(overrides)
        return run_guard(self.script, **env)

    def test_honest_caller_passes(self):
        self.assertEqual(self.guard().returncode, 0)

    def test_base_equal_to_head_is_refused(self):
        """The bypass itself: base-sha := head.sha makes the diff empty and the gate green."""
        result = self.guard(INPUT_BASE=GOOD_HEAD)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("base-sha", result.stdout + result.stderr)

    def test_substituted_base_is_refused(self):
        """Any other commit works too - an ancestor with nothing interesting in between."""
        self.assertNotEqual(self.guard(INPUT_BASE="3" * 40).returncode, 0)

    def test_substituted_head_is_refused(self):
        self.assertNotEqual(self.guard(INPUT_HEAD="3" * 40).returncode, 0)

    def test_other_events_are_refused(self):
        """No payload to verify against means unverifiable, and unverifiable fails."""
        for event in ("push", "workflow_dispatch", "issue_comment", "schedule", ""):
            with self.subTest(event=event):
                self.assertNotEqual(self.guard(EVENT_NAME=event).returncode, 0)

    def test_pull_request_target_is_accepted(self):
        self.assertEqual(self.guard(EVENT_NAME="pull_request_target").returncode, 0)

    def test_missing_payload_commits_are_refused(self):
        """An empty event value must not compare equal to an empty input."""
        self.assertNotEqual(self.guard(INPUT_BASE="", EVENT_BASE="").returncode, 0)
        self.assertNotEqual(self.guard(INPUT_HEAD="", EVENT_HEAD="").returncode, 0)

    def test_forged_author_is_refused(self):
        """The author decides whose name the report prints as barred from signing off."""
        self.assertNotEqual(self.guard(INPUT_AUTHOR="someone-else").returncode, 0)

    def test_absent_author_is_allowed(self):
        """The input is optional; omitting it is not the same as forging it."""
        self.assertEqual(self.guard(INPUT_AUTHOR="").returncode, 0)

    def test_refusal_says_what_to_do(self):
        """A control the reader cannot act on gets routed around."""
        result = self.guard(INPUT_BASE=GOOD_HEAD)
        self.assertIn("caller workflow", result.stdout + result.stderr)


class TestOnlyTheGateTakesCommitsFromTheCaller(unittest.TestCase):
    """Guard against the same hole being reintroduced in a sibling action.

    The other three actions derive the pull request from the API rather than from
    their inputs, and they are triggered by `issue_comment`, where the workflow that
    runs comes from the default branch and the pull request cannot edit it. Both
    halves of that have to stay true.
    """

    def test_no_action_accepts_a_commit_input_unverified(self):
        for action in sorted(p.name for p in ACTIONS.iterdir() if p.is_dir()):
            definition = yaml.safe_load((ACTIONS / action / "action.yml").read_text())
            commit_inputs = [
                name
                for name in (definition.get("inputs") or {})
                if "sha" in name or "commit" in name
            ]
            if not commit_inputs:
                continue
            with self.subTest(action=action):
                steps = [s.get("name", "") for s in definition["runs"]["steps"]]
                self.assertTrue(
                    any("Verify the inputs against the event" == s for s in steps),
                    f"{action} takes {commit_inputs} from the caller and never verifies them",
                )


if __name__ == "__main__":
    unittest.main()
