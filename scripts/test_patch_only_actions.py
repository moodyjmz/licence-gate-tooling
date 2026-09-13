"""The write-capable commands must stop being write-capable.

Two independent defects meet here.

The first is that `auto-fix` and `std-licence` pushed to the pull request branch as
`github-actions[bot]`. Branch protection's `require_last_push_approval` exists so
that whoever pushed last cannot be the one who approves; a bot push in between hands
that back. A person with write access pushes a commit to somebody else's pull
request, comments `/auto-fix`, and the bot's push makes the bot the last pusher - so
that person may now approve the commit they wrote, and the compliance record says
"approved by them at that sha", which is true and worthless. The same push moves the
head, so under `dismiss_stale_reviews` the tooling destroys the approvals it has just
finished collecting.

The fix removes the push outright rather than gating it. Both commands now post the
change as a patch and the author applies it - an ordinary push by an ordinary
contributor, which every branch-protection rule then judges as designed.

The second is `apply-std-licence.py` resolving its base against the base BRANCH'S
TIP. On a branch that is behind, a file deleted on the base branch after the branch
was cut is reported as ADDED by this pull request - so naming it stamps a first-party
copyright header onto a file the pull request never contributed.

The bash here is extracted from the action definitions and executed, so an edit to an
action that breaks the control fails in this suite rather than on a pull request.
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
EXAMPLES = ROOT / "examples"
STD_LICENCE = str(ROOT / "scripts" / "apply-std-licence.py")

PATCH_STEP = "Build the patch comment"


def action_definitions():
    for path in sorted(ACTIONS.glob("*/action.yml")):
        yield path, yaml.safe_load(path.read_text())


def step_script(action, step_name):
    """The `run:` body of a named step, so the test runs what the runner runs."""
    definition = yaml.safe_load((ACTIONS / action / "action.yml").read_text())
    for step in definition["runs"]["steps"]:
        if step.get("name") == step_name:
            return step["run"]
    raise AssertionError(f"{action}: no step named {step_name!r}")


class TestNothingPushes(unittest.TestCase):
    """No definition anywhere may push, and no caller may hand out `contents: write`.

    The action half is structural - a composite action has no `permissions:` key at
    all, so the only thing assertable there is that no step runs `git push`. The
    permission itself is granted by the caller workflow, which is why the examples
    are checked too: consumers copy those verbatim.
    """

    def test_no_action_pushes(self):
        for path, _definition in action_definitions():
            with self.subTest(action=path.parent.name):
                self.assertNotIn("git push", path.read_text(),
                                 f"{path} pushes; a bot push to a pull request branch "
                                 f"makes the bot the last pusher and unblocks a "
                                 f"self-approval")

    def test_no_action_commits(self):
        for path, _definition in action_definitions():
            with self.subTest(action=path.parent.name):
                self.assertNotRegex(path.read_text(), r"\bgit commit\b",
                                    f"{path} builds a commit it can no longer push")

    def test_no_action_asserts_authorship_in_a_commit_trailer(self):
        """The trailer only ever labelled a bot commit. No bot commit, no trailer.

        The assertion is still recorded - in the posted comment, attributed to the
        person who made it, which is the half that was ever worth anything.
        """
        for path, _definition in action_definitions():
            with self.subTest(action=path.parent.name):
                self.assertNotIn("Authorship-asserted-by", path.read_text())

    def test_no_example_workflow_grants_contents_write(self):
        for path in sorted(EXAMPLES.glob("*.yml")):
            workflow = yaml.safe_load(path.read_text())
            blocks = [workflow.get("permissions") or {}]
            for job in (workflow.get("jobs") or {}).values():
                blocks.append(job.get("permissions") or {})
            for block in blocks:
                with self.subTest(workflow=path.name):
                    self.assertNotEqual(block.get("contents"), "write",
                                        f"{path} hands the workflow token write "
                                        f"access it no longer needs")


class PatchStepCase(unittest.TestCase):
    """Run a patch-comment step against a real repository with a real modification."""

    action = None

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.tmp = tempfile.mkdtemp()
        self.script = step_script(self.action, PATCH_STEP)
        self._git("init", "-q", "-b", "main")
        self.write("src/thing.js", "// Copyright (c) 2020 Example Corp\nconst x = 1;\n")
        self._git("add", "-A")
        self._git("commit", "-q", "-m", "base")

    def _git(self, *args):
        return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                               *args], cwd=self.dir, capture_output=True, text=True)

    def write(self, path, content):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    def modify(self):
        """A working-tree change of the shape the fix scripts leave behind."""
        self.write("src/thing.js",
                   "// Copyright (c) 2020 Example Corp\n// Modified\nconst x = 1;\n")

    def run_step(self, **env):
        result = subprocess.run(
            ["bash", "-c", self.script], cwd=self.dir,
            env={"PATH": os.environ["PATH"], "HOME": self.dir, "RUNNER_TEMP": self.tmp,
                 **{k: v for k, v in env.items() if v is not None}},
            capture_output=True, text=True)
        body_path = os.path.join(self.tmp, "comment-body.md")
        body = ""
        if os.path.exists(body_path):
            with open(body_path, encoding="utf-8") as fh:
                body = fh.read()
        return result, body

    def assertTellsTheReaderToApplyItThemselves(self, body):
        """A gate whose instructions cannot be acted on gets routed around, and being
        routed around is indistinguishable from working."""
        self.assertIn("src/thing.js", body)
        lowered = body.lower()
        self.assertIn("git apply", lowered)
        self.assertRegex(lowered, r"nothing (has been|was) pushed")
        self.assertIn("yourself", lowered)


class TestAutoFixPatchComment(PatchStepCase):
    action = "auto-fix"

    def test_body_names_the_files_and_says_who_applies_them(self):
        self.modify()
        result, body = self.run_step(COUNT="1", UNFIXABLE="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTellsTheReaderToApplyItThemselves(body)

    def test_a_count_with_no_diff_is_a_hard_error(self):
        """"Applied it to one file" with an unchanged tree is a contradiction, not
        "nothing to do". The two must never share a code path."""
        result, _body = self.run_step(COUNT="1", UNFIXABLE="0")
        self.assertNotEqual(result.returncode, 0)

    def test_a_diff_with_no_count_is_a_hard_error(self):
        self.modify()
        result, _body = self.run_step(COUNT="0", UNFIXABLE="0")
        self.assertNotEqual(result.returncode, 0)

    def test_nothing_to_fix_says_so_without_a_patch(self):
        result, body = self.run_step(COUNT="0", UNFIXABLE="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Nothing to auto-fix", body)

    def test_an_oversized_patch_is_not_silently_truncated(self):
        """GitHub caps a comment at 65536 characters. A patch cut off in the middle
        applies cleanly up to the cut, so the author gets a partial fix that looks
        complete. Name the files and refuse instead."""
        self.write("src/thing.js", "// Copyright (c) 2020 Example Corp\n"
                   + "const x = 1;\n" * 9000)
        result, body = self.run_step(COUNT="1", UNFIXABLE="0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(len(body), 65536)
        self.assertIn("src/thing.js", body)
        self.assertNotIn("```diff", body)


class TestStdLicencePatchComment(PatchStepCase):
    action = "std-licence"

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.tmp, "report.md"), "w", encoding="utf-8") as fh:
            fh.write("Applied a header to 1 file(s):\n\n- `src/thing.js` — `Example-1.0`\n")

    def test_body_carries_the_report_and_the_patch(self):
        self.modify()
        result, body = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Applied a header to 1 file(s)", body)
        self.assertTellsTheReaderToApplyItThemselves(body)

    def test_a_report_claiming_headers_with_no_diff_is_a_hard_error(self):
        result, _body = self.run_step()
        self.assertNotEqual(result.returncode, 0)

    def test_a_refusal_only_report_posts_without_a_patch(self):
        with open(os.path.join(self.tmp, "report.md"), "w", encoding="utf-8") as fh:
            fh.write("**Refused 1 file(s)** — these need a person:\n\n- `x` — nope\n")
        result, body = self.run_step()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Refused 1 file(s)", body)
        self.assertNotIn("```diff", body)


class TestStdLicenceResolvesTheBase(unittest.TestCase):
    """A file the pull request never contributed must not be stampable.

    The base handed to the script is `pull_request.base.sha` - the base branch's
    CURRENT TIP, not the point the branch was cut. Diff that against a stale branch
    and everything the base branch has since DELETED comes back as ADDED by this pull
    request. Name one in the command and it gets a first-party copyright header
    stamped onto content this pull request has never touched, on the strength of an
    assertion nobody made about it.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._git("init", "-q", "-b", "main")
        # Cut point: both files exist, neither carries a header.
        self.write("src/theirs.js", "const theirs = 1;\n")
        self.commit("cut point")
        self.cut = self._git("rev-parse", "HEAD").stdout.strip()
        self._git("checkout", "-q", "-b", "feature")
        self.write("src/mine.js", "const mine = 1;\n")
        self.commit("the pull request's own work")
        self.head = self._git("rev-parse", "HEAD").stdout.strip()
        # The base branch moves on without the branch: the file is removed there.
        self._git("checkout", "-q", "main")
        os.remove(os.path.join(self.dir, "src/theirs.js"))
        self.commit("base branch deletes it")
        self.base_tip = self._git("rev-parse", "HEAD").stdout.strip()
        self._git("checkout", "-q", "feature")

    def _git(self, *args):
        return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                               *args], cwd=self.dir, capture_output=True, text=True)

    def write(self, path, content):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    def commit(self, message):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message)

    def apply(self, *paths):
        return subprocess.run(["python3", STD_LICENCE, self.base_tip, self.head, *paths],
                              cwd=self.dir, capture_output=True, text=True)

    def read(self, path):
        with open(os.path.join(self.dir, path), encoding="utf-8") as fh:
            return fh.read()

    def test_the_diff_really_does_offer_the_base_branchs_file(self):
        """The premise, asserted against git rather than assumed: diffed against the
        base TIP, a file only the base branch touched is reported as added here."""
        raw = self._git("diff", "--raw", "-z", f"{self.base_tip}..{self.head}").stdout
        self.assertIn("src/theirs.js", raw)
        self.assertIn("A\x00src/theirs.js", raw)

    def test_a_file_the_base_branch_deleted_is_not_stampable(self):
        result = self.apply("src/theirs.js")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Copyright", self.read("src/theirs.js"))
        self.assertNotIn("src/theirs.js", result.stdout)

    def test_the_pull_requests_own_new_file_is_still_stamped(self):
        """The fix must not be a narrowing. What the branch actually added still gets
        its header on the same command."""
        result = self.apply("src/mine.js")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("src/mine.js", result.stdout)
        self.assertIn("Copyright", self.read("src/mine.js"))

    def test_an_unresolvable_base_stops_the_program(self):
        """"Could not determine the base" and "nothing to do" must not share an exit
        code, a message, or a code path."""
        result = subprocess.run(
            ["python3", STD_LICENCE, "0" * 40, self.head], cwd=self.dir,
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("No new files needing a licence decision", result.stdout)
        self.assertRegex(result.stdout + result.stderr, r"(?i)could not")


class TestTheDocumentedPermissionsMatch(unittest.TestCase):
    """The README's table is what a consumer copies when the examples are not to hand."""

    def test_readme_does_not_advertise_write_for_the_comment_commands(self):
        for row in (ROOT / "README.md").read_text().splitlines():
            if row.startswith("| `actions/auto-fix`") or row.startswith("| `actions/std-licence`"):
                with self.subTest(row=row[:32]):
                    self.assertNotIn("contents: write", row)
                    self.assertIn("contents: read", row)

    def test_readme_says_the_commands_post_a_patch(self):
        text = (ROOT / "README.md").read_text().lower()
        self.assertTrue(re.search(r"patch.*comment|comment.*patch", text),
                        "the README still describes these commands as pushing")


if __name__ == "__main__":
    unittest.main()
