#!/usr/bin/env python3
"""The modification-notice generator, against real git repositories.

The rules worth testing here are the ones with no natural coverage in any repository
you happen to have: the scope filter that drops upstream-authored history, the merge
commit whose real title is in its body, and the seam between this generator and the
gate's append-only enforcement - two things built hours apart with nothing standing
between them.

Every failure mode gets its own test and its own distinguishable message, because the
whole point is that "could not work out the baseline" and "this fork changed nothing"
must never arrive looking the same.

stdlib only; no fixtures beyond a temporary directory.
"""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
GEN = os.path.join(HERE, "modifications.py")
GATE = os.path.join(HERE, "licence-gate.py")

sys.path.insert(0, HERE)

from modifications import (  # noqa: E402
    ConfigError, GateError, introduced_commits, parse_toml)

REPO = "example-owner/example-fork"


class RepoCase(unittest.TestCase):
    """A throwaway repository, a config file beside it, and the generator's output."""

    upstream_authors = []

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        # Outside the repository, and not by accident: the baseline defines what
        # "modified" means, so a pull request able to edit it could redefine its own
        # history. The config lives in the tooling repo, never in the described one.
        self.config = os.path.join(tempfile.mkdtemp(), "modifications.toml")
        self.baseline = ""
        self._git("init", "-q", "-b", "main")

    # ------------------------------------------------------------------ fixtures

    def write_config(self, repo=REPO, upstream="Example Upstream", baseline=None,
                     date="2026-01-01", authors=None):
        authors = self.upstream_authors if authors is None else authors
        rows = ", ".join(f'"{a}"' for a in authors)
        with open(self.config, "w", encoding="utf-8") as fh:
            fh.write(f'[{repo}]\n'
                     f'upstream = "{upstream}"\n'
                     f'baseline = "{baseline if baseline else self.baseline}"\n'
                     f'baseline_date = "{date}"\n'
                     f'upstream_authors = [{rows}]\n')

    def _git(self, *args, author=None, date=None):
        name, email = author or ("Fork Dev", "dev@example-fork.test")
        env = dict(os.environ)
        if date:
            env["GIT_AUTHOR_DATE"] = date
            env["GIT_COMMITTER_DATE"] = date
        p = subprocess.run(
            ["git", "-c", f"user.name={name}", "-c", f"user.email={email}", *args],
            cwd=self.dir, capture_output=True, text=True, env=env)
        return p

    def write(self, path, content):
        full = os.path.join(self.dir, path)
        os.makedirs(os.path.dirname(full) or self.dir, exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)

    def commit(self, message, author=None, date=None):
        self._git("add", "-A")
        self._git("commit", "-q", "-m", message, author=author, date=date)
        return self.sha("HEAD")

    def sha(self, rev):
        return self._git("rev-parse", rev).stdout.strip()

    def merge(self, branch, subject, body=None, author=None, date=None):
        """A true merge commit: subject from git's template, title in the body."""
        args = ["merge", "--no-ff", "-q", "-m", subject]
        if body is not None:
            args += ["-m", body]
        self._git(*args, branch, author=author, date=date)
        return self.sha("HEAD")

    def branch_commit(self, branch, path, content, message, author=None, date=None):
        self._git("checkout", "-q", "-b", branch)
        self.write(path, content)
        self.commit(message, author=author, date=date)
        self._git("checkout", "-q", "main")

    def on_branch(self, branch, *commits, base="main"):
        """A branch off `base` carrying (path, message, author) commits."""
        self._git("checkout", "-q", "-b", branch, base)
        for path, message, author in commits:
            self.write(path, "x\n")
            self.commit(message, author=author)
        self._git("checkout", "-q", "main")

    def octopus(self, subject, body, branches, author=None):
        args = ["merge", "--no-ff", "-q", "-m", subject]
        if body is not None:
            args += ["-m", body]
        self._git(*args, *branches, author=author)
        return self.sha("HEAD")

    def merge_introducing_nothing(self, subject, body, ancestor, author=None):
        """A merge whose second parent is already reachable from its first.

        git refuses to record one with `merge --no-ff` (it is already up to date),
        so it is built by hand - but a rebase, a revert of a merge and a hand-made
        integration commit all produce this shape in real history."""
        args = ["commit-tree", self.sha("HEAD^{tree}"), "-p", "HEAD", "-p", ancestor,
                "-m", subject]
        if body is not None:
            args += ["-m", body]
        new = self._git(*args, author=author).stdout.strip()
        self._git("reset", "--hard", "-q", new)
        return new

    def drop_object(self, sha):
        """Delete a loose object, which is how a range stops being computable.

        A garbage-collected object, a shallow clone and a partial clone all present
        the same way to `git log`: the commit is named in its child's parent list
        and cannot be read."""
        os.remove(os.path.join(self.dir, ".git", "objects", sha[:2], sha[2:]))

    # --------------------------------------------------------------------- runner

    def run_gen(self, *args, repo=REPO):
        cmd = ["python3", GEN, "--repo", repo, "--config", self.config, *args]
        p = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    def generated(self):
        code, out, err = self.run_gen()
        self.assertEqual(code, 0, f"generation failed: {err}")
        return out

    def subjects(self):
        return [line.split("  ", 2)[2]
                for line in self.generated().splitlines() if line.startswith("- ")]


class TestTheSubjectIsTheChangeNotTheCommit(RepoCase):
    """A merge commit's subject is git's template. The title a human wrote is in the
    body, and a notice full of `Merge pull request #19 from ...` records nothing."""

    def setUp(self):
        super().setUp()
        self.write("README.md", "base\n")
        self.baseline = self.commit("feat: base")
        self.write_config()

    def test_a_pull_request_merge_uses_the_body_line(self):
        self.branch_commit("work/icon-set", "src/icon.js", "x\n", "wip")
        self.merge("work/icon-set", "Merge pull request #19 from owner/work/icon-set",
                   "Introduce the tile icon")
        self.assertEqual(self.subjects(), ["Introduce the tile icon"])

    def test_a_branch_merge_uses_the_body_line(self):
        self.branch_commit("topic", "src/a.js", "x\n", "wip")
        self.merge("topic", "Merge branch 'topic'", "Rework the settings pane")
        self.assertEqual(self.subjects(), ["Rework the settings pane"])

    def test_a_remote_tracking_merge_uses_the_body_line(self):
        self.branch_commit("topic", "src/a.js", "x\n", "wip")
        self.merge("topic", "Merge remote-tracking branch 'origin/topic'",
                   "Rework the settings pane")
        self.assertEqual(self.subjects(), ["Rework the settings pane"])

    def test_a_blank_first_body_line_is_skipped(self):
        self.branch_commit("topic", "src/a.js", "x\n", "wip")
        self.merge("topic", "Merge pull request #2 from owner/topic",
                   "\n\nRework the settings pane\n\nmore prose\n")
        self.assertEqual(self.subjects(), ["Rework the settings pane"])

    def test_only_the_first_body_line_is_taken(self):
        self.branch_commit("topic", "src/a.js", "x\n", "wip")
        self.merge("topic", "Merge pull request #2 from owner/topic",
                   "Rework the settings pane\n\nA paragraph nobody wants in a list.")
        self.assertEqual(self.subjects(), ["Rework the settings pane"])

    def test_an_empty_body_falls_back_to_what_the_merge_brought_in(self):
        """An entry reading `Merge remote-tracking branch 'origin/topic'` tells a
        recipient nothing about what was modified, so the raw template is never the
        answer: the commits behind the merge are."""
        self.branch_commit("topic", "src/a.js", "x\n", "feat: add the settings pane")
        self.merge("topic", "Merge remote-tracking branch 'origin/topic'")
        code, out, err = self.run_gen()
        self.assertEqual(code, 0)
        self.assertEqual(self.subjects(), ["feat: add the settings pane"])
        self.assertNotIn("Merge remote-tracking branch", out)
        self.assertIn("no body", err,
                      "falling back must not happen silently")

    def test_a_squash_merge_keeps_its_own_subject(self):
        self.write("src/a.js", "x\n")
        self.commit("chore: bump the gate pin (#36)")
        self.assertEqual(self.subjects(), ["chore: bump the gate pin (#36)"])

    def test_a_subject_merely_mentioning_a_merge_is_not_rewritten(self):
        """`^Merge pull request #\\d+ from ` is anchored and specific. A squash merge
        whose title happens to start with the word Merge is an ordinary subject."""
        self.write("src/a.js", "x\n")
        self.commit("Merge the two settings panes")
        self.assertEqual(self.subjects(), ["Merge the two settings panes"])

    def test_one_merge_is_one_entry(self):
        """First-parent. A twelve-commit pull request is one change, not twelve."""
        self._git("checkout", "-q", "-b", "topic")
        for i in range(3):
            self.write(f"src/{i}.js", "x\n")
            self.commit(f"wip {i}")
        self._git("checkout", "-q", "main")
        self.merge("topic", "Merge pull request #3 from owner/topic", "Add the panel")
        self.assertEqual(self.subjects(), ["Add the panel"])


class TestTheScopeFilter(RepoCase):
    """The rule that removes most of a real fork's history, and which no repository
    here exercises naturally: commits authored upstream are not OUR modifications.

    An author matching no configured pattern counts as ours. That direction is
    deliberate - a spurious entry is noise somebody deletes, a missing one is a
    compliance gap nobody can see."""

    upstream_authors = ["*@upstream.example.org", "upstream-release-bot"]

    def setUp(self):
        super().setUp()
        self.write("README.md", "base\n")
        self.baseline = self.commit("feat: base")
        self.write_config()

    def test_an_upstream_address_is_excluded_and_an_unknown_author_is_included(self):
        self.write("src/u.js", "x\n")
        self.commit("upstream: rework the parser",
                    author=("Up Stream", "dev@upstream.example.org"))
        self.write("src/o.js", "x\n")
        self.commit("feat: our own change")
        self.write("src/s.js", "x\n")
        self.commit("chore: a drive-by from a stranger",
                    author=("Passing Stranger", "someone@elsewhere.test"))
        self.assertEqual(self.subjects(),
                         ["feat: our own change",
                          "chore: a drive-by from a stranger"])

    def test_a_pattern_matches_the_name_as_well_as_the_address(self):
        self.write("src/u.js", "x\n")
        self.commit("upstream: automated release",
                    author=("upstream-release-bot", "noreply@somewhere.test"))
        self.assertEqual(self.subjects(), [])

    def test_one_person_under_two_addresses_is_matched_by_two_patterns(self):
        self.write_config(authors=["*@upstream.example.org", "alex@personal.test"])
        self.write("src/a.js", "x\n")
        self.commit("upstream: at work", author=("Alex", "alex@upstream.example.org"))
        self.write("src/b.js", "x\n")
        self.commit("upstream: at home", author=("Alex", "alex@personal.test"))
        self.assertEqual(self.subjects(), [])

    def test_a_pattern_is_not_a_substring_match(self):
        """A wildcard-free pattern is an exact match. Substring matching would let
        `upstream.example.org` swallow `upstream.example.org.evil.test`, and an author
        wrongly classed as upstream is an entry that is never written at all."""
        self.write_config(authors=["dev@upstream.example.org"])
        self.write("src/a.js", "x\n")
        self.commit("feat: not actually upstream",
                    author=("Nearly", "dev@upstream.example.org.evil.test"))
        self.assertEqual(self.subjects(), ["feat: not actually upstream"])

    def test_an_empty_author_list_excludes_nobody(self):
        self.write_config(authors=[])
        self.write("src/a.js", "x\n")
        self.commit("feat: ours", author=("Up Stream", "dev@upstream.example.org"))
        self.assertEqual(self.subjects(), ["feat: ours"])


class TestAnEntryMustDescribeTheChange(RepoCase):
    """`- 2026-09-11  9c22a6c  fix/boo` is a line in a shipped compliance notice that
    tells its recipient nothing. A branch name is not a description, and neither is
    git's merge template or an empty string.

    The rule for "merely a branch name" is anchored, not a guess about shape: no
    whitespace AND equal to the branch the merge subject itself names. A subject with
    a slash in it is ordinary English and must survive."""

    def setUp(self):
        super().setUp()
        self.write("README.md", "base\n")
        self.baseline = self.commit("feat: base")
        self.write_config()

    def test_a_body_that_is_only_the_branch_name_is_not_the_entry(self):
        self.branch_commit("fix/boo", "src/a.js", "x\n", "fix: stop the parser crashing")
        self.merge("fix/boo", "Merge pull request #34 from owner/fix/boo", "fix/boo")
        self.assertEqual(self.subjects(), ["fix: stop the parser crashing"])

    def test_a_body_that_is_only_a_plain_branch_name_is_not_the_entry(self):
        self.branch_commit("topic", "src/a.js", "x\n", "feat: rework the settings pane")
        self.merge("topic", "Merge branch 'topic'", "topic")
        self.assertEqual(self.subjects(), ["feat: rework the settings pane"])

    def test_a_real_subject_containing_a_slash_survives(self):
        """`fix: don't crash on a/b paths` is a legitimate title. Rejecting anything
        that looks slashy would delete it."""
        self.branch_commit("fix/boo", "src/a.js", "x\n", "wip")
        self.merge("fix/boo", "Merge pull request #34 from owner/fix/boo",
                   "fix: don't crash on a/b paths")
        self.assertEqual(self.subjects(), ["fix: don't crash on a/b paths"])

    def test_a_single_word_that_is_not_the_branch_survives(self):
        """The rule is anchored to the branch the subject names, so a one-word title
        is kept. Guessing from shape alone would throw it away."""
        self.branch_commit("work/thing", "src/a.js", "x\n", "wip")
        self.merge("work/thing", "Merge pull request #35 from owner/work/thing",
                   "Tidying")
        self.assertEqual(self.subjects(), ["Tidying"])

    def test_several_introduced_subjects_are_listed_and_capped(self):
        """One entry is one line. Three titles is as much as a reader can use, and the
        count says there is more - both fixed by the merge's own history, so the line
        never changes under the append-only rule."""
        self.on_branch("work/lots", *[(f"src/{i}.js", f"feat: change {i}", None)
                                      for i in range(5)])
        self.merge("work/lots", "Merge pull request #36 from owner/work/lots",
                   "work/lots")
        self.assertEqual(
            self.subjects(),
            ["feat: change 0; feat: change 1; feat: change 2 (+2 more)"])

    def test_a_merge_template_is_never_the_entry(self):
        """Not in the subject, and not from the introduced side either: a sync merge
        dragged in by a branch is no more of a description than the outer one."""
        self.branch_commit("topic", "src/a.js", "x\n", "Merge branch 'elsewhere'")
        self.merge("topic", "Merge pull request #37 from owner/topic", "topic")
        self.assertEqual(self.subjects(), ["(no description recorded in the history)"],
                         "no template, no branch name, and nothing invented either")

    def test_an_octopus_template_is_never_the_entry(self):
        self.on_branch("a-one", ("src/a.js", "feat: the first thing", None))
        self.on_branch("b-two", ("src/b.js", "feat: the second thing", None))
        self.octopus("Merge branches 'a-one' and 'b-two'", None, ["a-one", "b-two"])
        subjects = self.subjects()
        self.assertEqual(len(subjects), 1)
        # Order WITHIN a side is oldest first; between two independent sides it is
        # git's own traversal, which is fixed for a given repository but not something
        # this test should pin. What matters is that both sides are named.
        self.assertEqual(sorted(subjects[0].split("; ")),
                         ["feat: the first thing", "feat: the second thing"])

    def test_nothing_usable_names_the_problem_rather_than_shrugging(self):
        """A hard error here would wedge the notice permanently: the cause is a commit
        message that cannot be changed without rewriting history. So the entry is
        written, says plainly that it has no description, and the run warns."""
        self.write("src/a.js", "x\n")
        ancestor = self.commit("feat: something earlier")
        self.write("src/b.js", "x\n")
        self.commit("feat: something later")
        self.merge_introducing_nothing("Merge branch 'stale'", "stale", ancestor)
        code, out, err = self.run_gen()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.subjects()[-1], "(no description recorded in the history)")
        self.assertNotIn("stale", "\n".join(self.subjects()),
                         "the branch name is what must not reach the notice")
        self.assertIn("cannot say what was modified", err)


class TestOwnershipComesFromWhatAMergeIntroduces(RepoCase):
    """Who authored a merge commit is who pressed the button, not who wrote the code.

    On a fork the filter's whole job is to drop routine upstream syncs, and those are
    merged by whoever is on duty here - so judging a merge by its own author lets every
    sync in, and drops our own work whenever a bot or an upstream maintainer did the
    merging. The second direction is the dangerous one: a missing entry is a compliance
    gap nobody can see.

    So a merge is upstream only when EVERY commit it introduced is upstream-authored.
    Any of ours on that side and the whole merge is ours."""

    upstream_authors = ["*@upstream.example.org", "upstream-release-bot"]
    UPSTREAM = ("Up Stream", "dev@upstream.example.org")
    ALSO_UPSTREAM = ("Other Stream", "other@upstream.example.org")
    OURS = ("Fork Dev", "dev@example-fork.test")

    def setUp(self):
        super().setUp()
        self.write("README.md", "base\n")
        self.baseline = self.commit("feat: base")
        self.write_config()

    def test_an_upstream_sync_merged_by_one_of_us_is_excluded(self):
        """The defect. Somebody here merges the weekly sync, and the sync becomes a
        modification of ours - in the large majority of a real fork's history."""
        self.on_branch("sync", ("src/u.js", "upstream: rework the parser", self.UPSTREAM),
                       ("src/v.js", "upstream: tidy the parser", self.ALSO_UPSTREAM))
        self.merge("sync", "Merge branch 'sync'", "Sync with upstream")
        self.assertEqual(self.subjects(), [],
                         "the merge author is irrelevant; both sides are upstream's work")

    def test_a_merge_introducing_a_mix_is_included(self):
        self.on_branch("mixed", ("src/u.js", "upstream: rework the parser", self.UPSTREAM),
                       ("src/o.js", "feat: our own change", self.OURS))
        self.merge("mixed", "Merge pull request #7 from owner/mixed", "Land the mixed branch",
                   author=self.UPSTREAM)
        self.assertEqual(self.subjects(), ["Land the mixed branch"],
                         "one commit of ours on that side makes the whole merge ours")

    def test_our_work_merged_by_an_upstream_identity_is_included(self):
        self.on_branch("ours", ("src/o.js", "feat: our own change", self.OURS))
        self.merge("ours", "Merge pull request #8 from owner/ours", "Introduce the tile icon",
                   author=("upstream-release-bot", "bot@upstream.example.org"))
        self.assertEqual(self.subjects(), ["Introduce the tile icon"],
                         "an upstream bot doing the merging must not erase our entry")

    def test_a_direct_commit_is_judged_by_its_own_author(self):
        """No second parent, nothing introduced: the author IS the person who wrote it."""
        self.write("src/u.js", "x\n")
        self.commit("upstream: rework the parser", author=self.UPSTREAM)
        self.write("src/o.js", "x\n")
        self.commit("feat: our own change", author=self.OURS)
        self.assertEqual(self.subjects(), ["feat: our own change"])

    def test_an_octopus_merge_is_upstream_only_when_every_side_is(self):
        self.on_branch("up-a", ("src/a.js", "upstream: one", self.UPSTREAM))
        self.on_branch("up-b", ("src/b.js", "upstream: two", self.ALSO_UPSTREAM))
        self.octopus("Merge branches 'up-a' and 'up-b'", "Sync two upstream branches",
                     ["up-a", "up-b"])
        self.assertEqual(self.subjects(), [])

    def test_an_octopus_merge_with_one_side_of_ours_is_included(self):
        """The union of every non-first parent, `P2 P3 ... ^P1`. A bigger introduced
        set makes "all upstream" harder to satisfy, which leans to inclusion."""
        self.on_branch("up-a", ("src/a.js", "upstream: one", self.UPSTREAM))
        self.on_branch("our-b", ("src/b.js", "feat: ours", self.OURS))
        self.octopus("Merge branches 'up-a' and 'our-b'", "Land two branches at once",
                     ["up-a", "our-b"], author=self.UPSTREAM)
        self.assertEqual(self.subjects(), ["Land two branches at once"])

    def test_a_merge_that_introduces_nothing_is_ours(self):
        """Vacuously, every commit it introduced is upstream's - which would exclude it
        on a technicality. There is no evidence either way, and the chosen failure
        direction is over-reporting, so it stays."""
        self.write("src/a.js", "x\n")
        ancestor = self.commit("feat: something earlier", author=self.OURS)
        self.write("src/b.js", "x\n")
        self.commit("feat: something later", author=self.OURS)
        self.merge_introducing_nothing("Merge branch 'stale'", "Fold in the stale branch",
                                       ancestor, author=self.UPSTREAM)
        self.assertIn("Fold in the stale branch", self.subjects())

    def test_an_empty_range_and_an_unreadable_one_are_not_the_same_answer(self):
        """The distinction the whole module turns on, at the one seam that decides
        whether an entry is written: a merge that introduced nothing answers with an
        empty list, and a merge whose range cannot be READ raises.

        Driven directly because end to end the two cannot be told apart from outside:
        every way of making a range unreadable - a pruned object, a shallow clone -
        also breaks the merge-base this tool computes first, so the run stops earlier
        with that message instead. This guard catches the case where availability
        changes mid-run: a concurrent gc, or a partial clone whose lazy fetch fails."""
        self.write("src/a.js", "x\n")
        ancestor = self.commit("feat: something earlier", author=self.OURS)
        self.write("src/b.js", "x\n")
        self.commit("feat: something later", author=self.OURS)
        merge = self.merge_introducing_nothing("Merge branch 'stale'", None, ancestor)
        first = self.sha("HEAD^1")

        here = os.getcwd()
        os.chdir(self.dir)
        try:
            self.assertEqual(introduced_commits(merge, [first, ancestor]), [],
                             "introduced nothing: an answer, and a legitimate one")
            with self.assertRaises(GateError) as caught:
                introduced_commits(merge, [first, "b" * 40])
        finally:
            os.chdir(here)
        message = str(caught.exception)
        self.assertIn("brought in", message)
        self.assertIn("fetch-depth: 0", message)
        self.assertNotIn("cannot be resolved", message,
                         "distinguishable from an unresolvable baseline")
        self.assertNotIn("NOT an ancestor", message,
                         "distinguishable from a baseline off the branch")

    def test_an_unreadable_object_writes_no_notice_at_all(self):
        """Whatever stops the history being read, the answer is never a shorter
        notice."""
        self.on_branch("gone", ("src/a.js", "feat: ours", self.OURS))
        self.merge("gone", "Merge pull request #9 from owner/gone", "Introduce the thing")
        self.drop_object(self.sha("HEAD^2"))
        code, out, err = self.run_gen()
        self.assertEqual(code, 2, f"a history that cannot be read is not an empty one\n{out}")
        self.assertIn("fetch-depth: 0", err)
        self.assertNotIn("Forked from", out)


class TestTheFileShape(RepoCase):
    def setUp(self):
        super().setUp()
        self.write("README.md", "base\n")
        self.baseline = self.commit("feat: base", date="2026-01-01T00:00:00+00:00")
        self.write_config()

    def test_oldest_first_with_the_author_date(self):
        self.write("a", "1\n")
        self.commit("feat: first", date="2026-02-01T00:00:00+00:00")
        self.write("b", "1\n")
        self.commit("feat: second", date="2026-03-02T00:00:00+00:00")
        lines = [l for l in self.generated().splitlines() if l.startswith("- ")]
        self.assertEqual(lines, ["- 2026-02-01  " + self.sha("HEAD~1")[:7] + "  feat: first",
                                 "- 2026-03-02  " + self.sha("HEAD")[:7] + "  feat: second"])

    def test_the_heading_and_the_baseline_line(self):
        out = self.generated().splitlines()
        self.assertEqual(out[0], "# Modifications")
        self.assertEqual(
            out[2], f"Forked from Example Upstream at {self.baseline}, dated 2026-01-01.")

    def test_the_abbreviation_length_does_not_follow_the_repository(self):
        """`%h` scales with the repository and obeys `core.abbrev`, so an unpinned
        length silently rewrites every earlier line the day it changes - and a
        rewritten line is no longer a prefix of anything, which breaks the gate's
        append-only rule on the generator's own output. No fixture here is big enough
        to reach the threshold naturally, so the setting stands in for it."""
        self.write("a", "1\n")
        self.commit("feat: first")
        self._git("config", "core.abbrev", "16")
        short = [l.split("  ")[1] for l in self.generated().splitlines()
                 if l.startswith("- ")]
        self.assertEqual([len(s) for s in short], [7])

    def test_the_default_mode_writes_no_file(self):
        self.write("a", "1\n")
        self.commit("feat: first")
        self.generated()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "MODIFICATIONS.md")),
                         "the default mode has no side effects")

    def test_write_creates_the_file_and_commits_nothing(self):
        self.write("a", "1\n")
        self.commit("feat: first")
        code, out, err = self.run_gen("--write")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.dir, "MODIFICATIONS.md"), encoding="utf-8") as fh:
            self.assertIn("feat: first", fh.read())
        self.assertIn("MODIFICATIONS.md", self._git("status", "--porcelain").stdout,
                      "--write leaves the change uncommitted; it does not commit or push")

    def test_write_puts_the_notice_at_the_repository_root(self):
        """A blob path is relative to the root, so a notice written relative to the
        process is one `--check` can never see - and the gate would enforce
        append-only on it anyway, its pattern matching any path component."""
        self.write("src/a.js", "x\n")
        self.commit("feat: first")
        sub = os.path.join(self.dir, "src")
        p = subprocess.run(["python3", GEN, "--repo", REPO, "--config", self.config,
                            "--write"], cwd=sub, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "MODIFICATIONS.md")))
        self.assertFalse(os.path.exists(os.path.join(sub, "MODIFICATIONS.md")))

    def test_a_fork_with_no_modifications_yet_is_a_notice_with_no_entries(self):
        """Zero entries is a legitimate answer - and it is reached only after the
        baseline resolved, was an ancestor, and the log was read successfully."""
        out = self.generated()
        self.assertIn("Forked from Example Upstream", out)
        self.assertEqual([l for l in out.splitlines() if l.startswith("- ")], [])


class TestTheNoticeDoesNotRecordItself(RepoCase):
    """An entry names its commit's short SHA, so the commit that writes the notice can
    never contain its own entry - amending only moves the SHA. Left alone, the file is
    stale the moment it is committed and every regeneration records the previous
    regeneration. A commit touching nothing but the notice is bookkeeping."""

    def setUp(self):
        super().setUp()
        self.write("README.md", "base\n")
        self.baseline = self.commit("feat: base")
        self.write_config()

    def test_a_commit_touching_only_the_notice_is_not_an_entry(self):
        self.run_gen("--write")
        self.commit("docs: record the modifications")
        self.assertEqual(self.subjects(), [])

    def test_a_commit_touching_the_notice_and_source_is_still_an_entry(self):
        """The boundary is "only", not "at all". Editing the notice does not buy a
        source change an exemption from being recorded."""
        self.run_gen("--write")
        self.write("src/a.js", "x\n")
        self.commit("feat: a change that also touches the notice")
        self.assertEqual(self.subjects(),
                         ["feat: a change that also touches the notice"])

    def test_an_empty_commit_is_still_an_entry(self):
        """No files changed is not the same as "only the notice changed", and reading
        one as the other drops a real entry."""
        self._git("commit", "-q", "--allow-empty", "-m", "chore: an empty commit")
        self.assertEqual(self.subjects(), ["chore: an empty commit"])


class TestFailureModes(RepoCase):
    """Four ways this cannot run, each with its own message. None of them may look
    like a fork that changed nothing."""

    def setUp(self):
        super().setUp()
        self.write("README.md", "base\n")
        self.baseline = self.commit("feat: base")
        self.write_config()

    def test_no_config_entry_for_this_repo(self):
        code, out, err = self.run_gen(repo="someone/unknown-fork")
        self.assertEqual(code, 2)
        self.assertIn("no config entry for `someone/unknown-fork`", err)
        self.assertNotIn("Forked from", out)

    def test_no_config_file_at_all(self):
        os.remove(self.config)
        code, out, err = self.run_gen()
        self.assertEqual(code, 2)
        self.assertIn("no modifications config at", err)

    def test_an_unresolvable_baseline_names_fetch_depth(self):
        """git will not say this. An unreachable commit and a commit that never
        existed fail identically, and on a runner the overwhelmingly likelier cause
        is `actions/checkout` at its default depth of 1."""
        self.write_config(baseline="0" * 40)
        code, out, err = self.run_gen()
        self.assertEqual(code, 2)
        self.assertIn("cannot be resolved", err)
        self.assertIn("fetch-depth: 0", err)

    def test_a_baseline_that_is_not_an_ancestor(self):
        self._git("checkout", "-q", "-b", "elsewhere")
        self.write("other", "1\n")
        off_history = self.commit("feat: a commit on another branch")
        self._git("checkout", "-q", "main")
        self.write_config(baseline=off_history)
        code, out, err = self.run_gen()
        self.assertEqual(code, 2)
        self.assertIn("NOT an ancestor", err)
        self.assertNotIn("Forked from", out)

    def test_an_unrelated_history_is_not_read_as_no_modifications(self):
        """The baseline resolves and is simply not related to HEAD. git's own answer
        is exit 1 with nothing on stderr, which is why git_io does the talking."""
        other = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "main", other], capture_output=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "--allow-empty", "-m", "unrelated"],
                       cwd=other, capture_output=True)
        self._git("fetch", "-q", other, "main:unrelated")
        self.write_config(baseline="unrelated")
        code, out, err = self.run_gen()
        self.assertEqual(code, 2)
        self.assertIn("fetch-depth: 0", err)
        self.assertNotIn("Forked from", out)

    def test_the_committed_baseline_disagreeing_with_the_config(self):
        self.write("MODIFICATIONS.md",
                   "# Modifications\n\nForked from Example Upstream at "
                   + "b" * 40 + ", dated 2026-01-01.\n\n")
        self.commit("docs: a notice describing a different fork point")
        code, out, err = self.run_gen("--check", "HEAD~1", "HEAD")
        self.assertEqual(code, 2, f"a disagreement is a hard error, not a stale file\n{out}")
        self.assertIn("says the fork starts at", err)

    def test_a_repo_that_cannot_be_identified_is_not_a_missing_config_entry(self):
        """`git config --get` exits 1 when the key is absent, and exit 1 with an empty
        stderr is exactly the shape this tooling keeps mistaking for an answer."""
        cmd = ["python3", GEN, "--config", self.config]
        p = subprocess.run(cmd, cwd=self.dir, capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        self.assertIn("could not read `remote.origin.url`", p.stderr)
        self.assertIn("--repo", p.stderr)
        self.assertNotIn("no config entry", p.stderr)


class TestCheckMode(RepoCase):
    def setUp(self):
        super().setUp()
        self.write("README.md", "base\n")
        self.baseline = self.commit("feat: base")
        self.write_config()
        self.write("src/a.js", "x\n")
        self.commit("feat: the first change")

    def _check(self):
        return self.run_gen("--check", self.baseline, "HEAD")

    def test_a_current_file_passes(self):
        code, out, err = self.run_gen("--write")
        self.assertEqual(code, 0, err)
        self.commit("docs: record the modifications")
        code, out, err = self._check()
        self.assertEqual(code, 0, f"{out}\n{err}")

    def test_a_stale_file_fails_and_names_what_is_missing(self):
        self.run_gen("--write")
        self.commit("docs: record the modifications")
        self.write("src/b.js", "x\n")
        self.commit("feat: a change nobody recorded")
        code, out, err = self._check()
        self.assertEqual(code, 1, err)
        self.assertIn("feat: a change nobody recorded", out)
        self.assertIn("does not block", out,
                      "the check is advisory; it never stops a contributor")

    def test_a_missing_file_is_reported_as_missing_not_as_nothing_to_do(self):
        code, out, err = self._check()
        self.assertEqual(code, 1, err)
        self.assertIn("does not exist", out)
        self.assertIn("1 entry", out)

    def test_trailing_whitespace_and_a_reflowed_baseline_do_not_fail_a_check(self):
        """The question `--check` asks is "are the entries current". A check that
        cries wolf over whitespace is one people learn to ignore, and a gate people
        ignore is indistinguishable from one that does not run."""
        self.run_gen("--write")
        with open(os.path.join(self.dir, "MODIFICATIONS.md"), encoding="utf-8") as fh:
            text = fh.read()
        mangled = "\n".join(
            line.replace("Forked from Example Upstream at",
                         "Forked  from   Example Upstream   at") + "   "
            for line in text.splitlines()) + "\n\n\n"
        self.write("MODIFICATIONS.md", mangled)
        self.commit("docs: reflow the notice")
        code, out, err = self._check()
        self.assertEqual(code, 0, f"whitespace is not staleness\n{out}\n{err}")

    def test_an_entry_nobody_can_derive_is_reported(self):
        self.run_gen("--write")
        with open(os.path.join(self.dir, "MODIFICATIONS.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.write("MODIFICATIONS.md", text + "- 2026-04-04  deadbee  feat: invented\n")
        self.commit("docs: record something that never happened")
        code, out, err = self._check()
        self.assertEqual(code, 1, err)
        self.assertIn("not derivable", out)

    def test_an_unreachable_base_is_a_hard_error_not_a_clean_check(self):
        code, out, err = self.run_gen("--check", "b" * 40, "HEAD")
        self.assertEqual(code, 2, f"advisory covers stale entries, not a broken checkout\n{out}")
        self.assertIn("fetch-depth: 0", err)

    def test_a_file_with_no_baseline_line_is_advisory_not_a_hard_error(self):
        """Absent is not the same as DISAGREEING. A malformed notice is fixed by
        regenerating it; one naming a different fork point is a claim somebody made,
        and overwriting that quietly is how a fork point moves unnoticed."""
        self.write("MODIFICATIONS.md", "# Modifications\n\nsome prose\n")
        self.commit("docs: a notice somebody typed")
        code, out, err = self._check()
        self.assertEqual(code, 1, err)
        self.assertIn("no `Forked from ...` line", out)


class TestTheAppendOnlySeam(RepoCase):
    """The generator's output must satisfy the gate's append-only rule against its own
    previous output: the base blob must be a byte-exact PREFIX of the head blob.

    The two were built separately and nothing stood between them. Oldest-first
    ordering, a pinned abbreviation length and the absence of any footer are what make
    the property hold; each of those is easy to undo without noticing."""

    def setUp(self):
        super().setUp()
        self.write("README.md", "base\n")
        self.baseline = self.commit("feat: base")
        self.write_config()
        self.branch_commit("work/one", "src/one.js", "x\n", "wip")
        self.merge("work/one", "Merge pull request #1 from owner/work/one",
                   "Introduce the tile icon")

    def test_a_regeneration_is_the_old_file_plus_lines(self):
        self.run_gen("--write")
        self.commit("docs: record the modifications")
        with open(os.path.join(self.dir, "MODIFICATIONS.md"), encoding="utf-8") as fh:
            before = fh.read()

        self.branch_commit("work/two", "src/two.js", "x\n", "wip")
        self.merge("work/two", "Merge pull request #2 from owner/work/two",
                   "Add the settings pane")
        code, out, err = self.run_gen("--write")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.dir, "MODIFICATIONS.md"), encoding="utf-8") as fh:
            after = fh.read()

        self.assertTrue(after.startswith(before),
                        "a regeneration must extend the notice, never rewrite it\n"
                        f"--- before ---\n{before}\n--- after ---\n{after}")
        self.assertIn("Add the settings pane", after[len(before):])
        self.assertNotIn("Add the settings pane", before)

    def test_the_gate_accepts_the_regenerated_file(self):
        """Not a restatement of the prefix assertion: this runs the real gate over the
        real commit pair, so a change to either side of the seam shows up here."""
        self.run_gen("--write")
        self.commit("docs: record the modifications")
        self.branch_commit("work/two", "src/two.js", "x\n", "wip")
        self.merge("work/two", "Merge pull request #2 from owner/work/two",
                   "Add the settings pane")
        self.run_gen("--write")
        self.commit("docs: record the modifications")

        p = subprocess.run(["python3", GATE, "HEAD~1", "HEAD"],
                           cwd=self.dir, capture_output=True, text=True)
        # Assert the gate RAN first. `assertNotIn` on an empty string passes
        # cheerfully, so a gate that died on a traceback would certify this seam
        # exactly the way an empty `git show` used to certify a clean tree.
        self.assertIn(p.returncode, (0, 1), f"the gate did not run: {p.stderr}")
        self.assertTrue(p.stdout.strip(), f"the gate produced no report: {p.stderr}")
        self.assertNotIn("append-only", p.stdout,
                         f"the generator's own output must pass the gate\n{p.stdout}")


class TestTheConfigParser(unittest.TestCase):
    """Python is 3.9 here, so `tomllib` does not exist and four keys per repo is not
    worth a dependency. The subset is small; the parser is strict about it, because a
    silently dropped `upstream_authors` line produces a longer notice with nothing to
    show that anything went wrong."""

    def test_tables_strings_arrays_and_comments(self):
        cfg = parse_toml('# a comment\n'
                         '["owner/name"]\n'
                         'upstream = "Example Upstream"\n'
                         'upstream_authors = ["a@example.test", "b*"]\n'
                         '\n'
                         '[other/repo]\n'
                         'upstream = "Another"\n')
        self.assertEqual(cfg["owner/name"]["upstream"], "Example Upstream")
        self.assertEqual(cfg["owner/name"]["upstream_authors"], ["a@example.test", "b*"])
        self.assertEqual(cfg["other/repo"]["upstream"], "Another")

    def test_an_empty_array_is_an_empty_list_not_a_missing_key(self):
        cfg = parse_toml('[a/b]\nupstream_authors = []\n')
        self.assertEqual(cfg["a/b"]["upstream_authors"], [])

    def test_a_value_outside_the_subset_raises_rather_than_being_skipped(self):
        for bad in ("[a/b]\nupstream = Example\n",
                    "[a/b]\nupstream = 'Example'\n",
                    "[a/b]\nupstream = \"a\\\"b\"\n",
                    "[a/b]\nupstream_authors = [\n",
                    "[a/b]\nupstream\n",
                    "upstream = \"x\"\n",
                    "[a/b\nupstream = \"x\"\n"):
            with self.subTest(bad=bad):
                with self.assertRaises(ConfigError):
                    parse_toml(bad)

    def test_a_duplicated_table_or_key_raises(self):
        with self.assertRaises(ConfigError):
            parse_toml('[a/b]\nupstream = "x"\n[a/b]\nupstream = "y"\n')
        with self.assertRaises(ConfigError):
            parse_toml('[a/b]\nupstream = "x"\nupstream = "y"\n')


if __name__ == "__main__":
    unittest.main()
