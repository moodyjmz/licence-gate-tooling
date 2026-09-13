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

import json
import os
import re
import shutil
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


MARKER = "<!-- licence-gate -->"

NODE = shutil.which("node")
if NODE is None and os.environ.get("CI") == "true":
    # Skipping here would delete this file's coverage the day a runner image drops
    # node, and the suite would still print OK. It is a dependency, so say so.
    raise RuntimeError("node is required to run the github-script step bodies")

# The harness. `actions/github-script` evaluates the body as an async function with
# `github`, `context`, `core` and `require` in scope; this builds exactly that, so the
# text that runs here is the text that runs on the runner.
#
# The stubs model the API rather than being convenient. `listComments` answers with a
# single page of `per_page` entries - 30 when nobody asks, which is the REST default -
# and a comment body over the 65536-character cap is rejected the way the API rejects
# it. A stub that returned everything at once would let an unpaginated call pass, and
# a stub that accepted any length would let an unbounded body pass.
HARNESS = r"""
const fs = require('fs');
const body = fs.readFileSync(process.argv[2], 'utf8');
const cfg = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));

const result = {posted: [], updated: [], failed: [], logged: [], summary: '',
                listed: [], threw: null};

const comments = (cfg.comments || []).map((c, i) => ({id: 1000 + i, body: c}));

const listComments = async (params) => {
  const per = params.per_page || 30;
  const page = params.page || 1;
  result.listed.push({per_page: per, page: page});
  return {data: comments.slice((page - 1) * per, page * per)};
};

const cap = (text) => {
  if (text.length > 65536) {
    // What the API does: 422, the step throws, and no comment appears at all.
    const e = new Error('Validation Failed: body is too long (maximum is 65536 characters)');
    e.status = 422;
    throw e;
  }
};

const github = {
  paginate: async (fn, params) => {
    const per = params.per_page || 30;
    let page = 1, out = [], batch;
    do {
      batch = (await fn(Object.assign({}, params, {page: page}))).data;
      out = out.concat(batch);
      page += 1;
    } while (batch.length === per);
    return out;
  },
  rest: {
    issues: {
      listComments: listComments,
      createComment: async (p) => { cap(p.body); result.posted.push(p.body); },
      updateComment: async (p) => {
        cap(p.body);
        result.updated.push({comment_id: p.comment_id, body: p.body});
      },
    },
  },
};

const summary = {
  addHeading: (t) => { result.summary += t + '\n'; return summary; },
  addRaw: (t) => { result.summary += t; return summary; },
  addCodeBlock: (t) => { result.summary += t; return summary; },
  addTable: () => summary,
  addSeparator: () => summary,
  write: async () => summary,
};

const core = {
  setFailed: (m) => { result.failed.push(String(m)); },
  error: (m) => { result.logged.push(String(m)); },
  warning: (m) => { result.logged.push(String(m)); },
  notice: (m) => { result.logged.push(String(m)); },
  info: (m) => { result.logged.push(String(m)); },
  debug: () => {},
  setOutput: () => {},
  summary: summary,
};

const context = {
  repo: {owner: 'an-org', repo: 'a-repo'},
  issue: {number: 7},
  runId: 4242,
  serverUrl: 'https://github.example',
  payload: {},
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

(async () => {
  try {
    await new AsyncFunction('github', 'context', 'core', 'require', body)(
      github, context, core, require);
  } catch (e) {
    // A throw out of the body is how the step fails on the runner. Recorded
    // separately from setFailed: a step that chose to fail and a step that fell over
    // are different outcomes and must not be asserted on interchangeably.
    result.threw = String((e && e.message) || e);
  }
  process.stdout.write(JSON.stringify(result));
})();
"""


def step_script(action, step_name):
    """The `script:` body of a named github-script step, as the runner evaluates it."""
    definition = yaml.safe_load((ACTIONS / action / "action.yml").read_text())
    for step in definition["runs"]["steps"]:
        if step.get("name") == step_name:
            return step["with"]["script"]
    raise AssertionError(f"{action}: no step named {step_name!r}")


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


@unittest.skipUnless(NODE, "node is needed to run a github-script step body")
class ScriptStepCase(unittest.TestCase):
    """Run a github-script body against stubs that behave like the API."""

    action = None
    step = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.body = step_script(self.action, self.step)

    def write_report(self, text):
        with open(os.path.join(self.tmp, "licence-gate-report.md"), "w",
                  encoding="utf-8") as fh:
            fh.write(text)

    def run_step(self, comments=(), **env):
        paths = {}
        for name, content in (("step.js", self.body), ("harness.js", HARNESS)):
            paths[name] = os.path.join(self.tmp, name)
            with open(paths[name], "w", encoding="utf-8") as fh:
                fh.write(content)
        cfg = os.path.join(self.tmp, "cfg.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            json.dump({"comments": list(comments)}, fh)
        proc = subprocess.run(
            [NODE, paths["harness.js"], paths["step.js"], cfg],
            env={"PATH": os.environ["PATH"], "RUNNER_TEMP": self.tmp,
                 **{k: v for k, v in env.items() if v is not None}},
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)


class TestTheGateFindsItsOwnCommentOnALongThread(ScriptStepCase):
    """One report, updated in place - not one report per push.

    `listComments` unpaginated answers with the first 30 comments. Past that the
    marker is not found, so every push posts a fresh report and the pull request
    collects one copy per push. That is worst on a long-running pull request, which
    is the one whose report a reviewer most needs to be able to trust as current.
    The sibling action paginates this same call already.
    """

    action = "gate"
    step = "Post or update the review comment"

    def setUp(self):
        super().setUp()
        self.write_report("### Nothing to do — both checks pass\n")

    def thread(self, length, marker_at):
        comments = [f"ordinary discussion {i}" for i in range(length)]
        if marker_at is not None:
            comments[marker_at] = f"{MARKER}\n## Licence gate\n\nan earlier report"
        return comments

    def test_the_marker_is_found_past_the_first_page(self):
        result = self.run_step(self.thread(45, 40), HEAD_SHA="a" * 40)
        self.assertEqual(result["posted"], [],
                         "a second report was posted alongside the one already there")
        self.assertEqual(len(result["updated"]), 1)
        self.assertIn(MARKER, result["updated"][0]["body"])

    def test_the_marker_is_found_several_pages_in(self):
        result = self.run_step(self.thread(260, 250), HEAD_SHA="a" * 40)
        self.assertEqual(result["posted"], [])
        self.assertEqual(len(result["updated"]), 1)

    def test_the_marker_is_still_found_on_a_short_thread(self):
        """The fix must not be a narrowing: the common case still updates."""
        result = self.run_step(self.thread(4, 2), HEAD_SHA="a" * 40)
        self.assertEqual(result["posted"], [])
        self.assertEqual(len(result["updated"]), 1)

    def test_the_first_report_is_posted(self):
        result = self.run_step(self.thread(45, None), HEAD_SHA="a" * 40)
        self.assertEqual(len(result["posted"]), 1)
        self.assertIn(MARKER, result["posted"][0])


class TestTheGateNeverPostsSomethingItHasNotEarned(ScriptStepCase):
    """The report is the gate's voice. Losing it is not the same as having nothing to say.

    Two ways that happened. A report longer than GitHub's 65536-character comment cap
    is rejected with a 422, the step throws and NO comment appears - on the widest
    pull requests, which are the ones a reviewer cannot reconstruct by eye. And a
    report file that is empty, because the script could not be found or run at all,
    was posted as an empty comment: a report saying nothing, which reads as a report
    with nothing to say.

    The patch-comment steps already refuse an oversized patch and already fail on a
    missing body file. This is the same pair of guards on the other side of the tool.
    """

    action = "gate"
    step = "Post or update the review comment"

    def big_report(self, files=4000):
        lines = [
            "### What has to happen before this merges",
            "",
            "1. **the author** — restore 1 licence line",
            f"2. **a reviewer, NOT @someone** — answer every one of the {files} candidates below",
            "",
            f"### Blocking — {files} licence line(s) removed or altered",
            "",
        ]
        lines += [f"- `src/deeply/nested/path-{i}.js`" for i in range(files)]
        lines += ["", "### Candidates — a reviewer has to answer these", ""]
        lines += [f"- `vendor/thing-{i}` — third-party tree changed" for i in range(files)]
        return "\n".join(lines) + "\n"

    def test_an_empty_report_is_a_failure_and_not_an_empty_comment(self):
        """"The gate could not run" and "the gate found nothing" must not share an exit.

        A missing python3 or a wrong script path leaves the redirected file created
        and empty, and the step that produced it exits on `cat`, which succeeds.
        """
        self.write_report("")
        result = self.run_step(HEAD_SHA="a" * 40)
        self.assertEqual(result["posted"], [])
        self.assertEqual(result["updated"], [])
        self.assertTrue(result["failed"], "an empty report was not reported as one")

    def test_a_whitespace_only_report_is_a_failure(self):
        self.write_report("\n  \n\t\n")
        result = self.run_step(HEAD_SHA="a" * 40)
        self.assertEqual(result["posted"], [])
        self.assertTrue(result["failed"])

    def test_a_missing_report_is_a_failure_and_not_a_crash(self):
        result = self.run_step(HEAD_SHA="a" * 40)
        self.assertEqual(result["posted"], [])
        self.assertTrue(result["failed"],
                        "a missing report must be said out loud, not thrown")

    def test_an_oversized_report_still_leaves_a_comment(self):
        report = self.big_report()
        self.assertGreater(len(report), 65536, "the fixture is not actually oversized")
        self.write_report(report)
        result = self.run_step(HEAD_SHA="a" * 40)
        self.assertIsNone(result["threw"], "the post threw; no comment would appear")
        self.assertEqual(len(result["posted"]), 1)
        self.assertLess(len(result["posted"][0]), 65536)

    def test_an_oversized_report_says_it_is_not_the_whole_report(self):
        """A body cut off mid-report applies the same deception the patch guard names:
        it reads as complete. It has to say what it is."""
        self.write_report(self.big_report())
        body = self.run_step(HEAD_SHA="a" * 40)["posted"][0]
        self.assertRegex(body.lower(), r"(does not fit|too large|not the full)")
        self.assertNotIn("path-3999", body,
                         "the body is a truncation of the report, not a summary of it")

    def test_an_oversized_report_still_carries_the_counts_and_what_blocks(self):
        """Degrading to a body that says only "it was too long" would be honest and
        useless. The reviewer needs the counts and the list of what to do."""
        body = None
        self.write_report(self.big_report())
        body = self.run_step(HEAD_SHA="a" * 40)["posted"][0]
        self.assertIn("Blocking — 4000 licence line(s) removed or altered", body)
        self.assertIn("restore 1 licence line", body)
        self.assertIn("answer every one of the 4000 candidates", body)

    def test_an_oversized_report_points_somewhere_the_full_one_actually_is(self):
        """"See the log" is only useful if the full report is in the log."""
        report = self.big_report()
        self.write_report(report)
        result = self.run_step(HEAD_SHA="a" * 40)
        self.assertIn("actions/runs/4242", result["posted"][0])
        self.assertIn("path-3999", result["summary"],
                      "the body points at a step summary that does not hold the report")

    def test_an_oversized_report_keeps_the_marker(self):
        """Without it the next push cannot find this comment and posts another."""
        self.write_report(self.big_report())
        self.assertIn(MARKER, self.run_step(HEAD_SHA="a" * 40)["posted"][0])

    def test_a_report_that_fits_is_posted_whole(self):
        """The guard must not become the common path."""
        self.write_report("### Blocking — 1 licence line(s) removed or altered\n\n"
                          "- `src/thing.js`\n")
        body = self.run_step(HEAD_SHA="a" * 40)["posted"][0]
        self.assertIn("- `src/thing.js`", body)
        self.assertNotRegex(body.lower(), r"(does not fit|too large)")


if __name__ == "__main__":
    unittest.main()
