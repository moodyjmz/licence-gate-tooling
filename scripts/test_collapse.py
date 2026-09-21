#!/usr/bin/env python3
"""Bulk collapse, tested against the ways collapsing becomes under-reporting.

Collapsing candidates NARROWS what a reviewer is shown, so every test here is a
guard on the narrowing rather than on the formatting. The motivating case is real: a
pull request deleting several thousand generated assets for a single reason produced
a report nobody could read and a disposition block nobody could paste.

Each test states the invariant it defends, not the implementation. If a rewrite of
_collapse_bulk keeps these green, the rewrite is fine.
"""

import importlib.util
import pathlib
import sys
import unittest

# By path, and by an ABSOLUTE one: the hyphen in the filename makes the gate
# unimportable by name, and a bare relative path only resolves when the runner
# happens to start inside scripts/. test_licence_map loads it the same way.
_spec = importlib.util.spec_from_file_location(
    "lg", pathlib.Path(__file__).with_name("licence-gate.py"))
lg = importlib.util.module_from_spec(_spec)
sys.modules["lg"] = lg
_spec.loader.exec_module(lg)

collapse = lg._collapse_bulk

# Counts here are deliberately LITERAL rather than derived from COLLAPSE_THRESHOLD.
# They were written as T-1 and T+1 first, which made every case scale with the
# constant: dropping the threshold to 2 left the whole file green, because the
# fixtures shrank with it. A test parameterised by the value it is checking cannot
# detect that value changing. If the threshold is deliberately changed, these
# numbers are meant to be re-chosen by hand.
FEW, MANY = 3, 9

R = "file deleted"
OTHER = "binary content changed"


def keys(out):
    return [k for k, _ in out]


class TestNoCandidateIsLost(unittest.TestCase):
    """Collapsing may summarise findings; it may never discard one."""

    def test_the_collapsed_line_accounts_for_every_path_behind_it(self):
        paths = ["a/%d.png" % i for i in range(MANY)]
        out = collapse([(p, R) for p in paths])
        self.assertEqual(len(out), 1)
        self.assertIn(str(len(paths)), out[0][1],
                      "a collapsed line without its count reads as a single file")

    def test_paths_below_the_threshold_are_still_named_individually(self):
        paths = ["a/%d.png" % i for i in range(FEW)]
        out = collapse([(p, R) for p in paths])
        self.assertEqual(sorted(keys(out)), sorted(paths))


class TestCollapsingNeverMergesDistinctTrees(unittest.TestCase):
    """A common PREFIX spans siblings; an exact directory cannot.

    If this ever collapses to a shared ancestor, deletions in vendor/ and in src/
    report as one decision and a reviewer answers for a tree they never saw.
    """

    def test_sibling_directories_stay_separate(self):
        cands = ([("vendor/%d.png" % i, R) for i in range(MANY)] +
                 [("src/%d.png" % i, R) for i in range(MANY)])
        self.assertEqual(sorted(keys(collapse(cands))), ["src/*", "vendor/*"])

    def test_nested_siblings_do_not_merge_into_their_shared_parent(self):
        """The case that distinguishes an exact dirname from any prefix rule.

        Both trees live under vendor/, so anything keying on a path PREFIX - the
        first segment, a longest-common-prefix - reports one `vendor/*` decision
        covering files the reviewer was never shown.
        """
        cands = ([("vendor/a/%d.png" % i, R) for i in range(MANY)] +
                 [("vendor/b/%d.png" % i, R) for i in range(MANY)])
        got = sorted(keys(collapse(cands)))
        self.assertEqual(got, ["vendor/a/*", "vendor/b/*"])
        self.assertNotIn("vendor/*", got)

    def test_repository_root_files_never_collapse(self):
        paths = ["%d.png" % i for i in range(MANY)]
        out = collapse([(p, R) for p in paths])
        self.assertEqual(sorted(keys(out)), sorted(paths),
                         "a bare /* at the root would claim the whole tree")


class TestEveryCandidateKeepsItsOwnKey(unittest.TestCase):
    """The acknowledgement gate matches a disposition line by key.

    Two candidates sharing one key are both satisfied by one answer: a reviewer
    dispositions one reason and the other passes silently. That is the failure
    this rule exists for, so a directory holding two reasons stays enumerated.
    """

    def test_a_directory_with_two_reasons_is_not_collapsed(self):
        cands = ([("img/a%d.png" % i, R) for i in range(MANY)] +
                 [("img/z%d.png" % i, OTHER) for i in range(MANY)])
        out = collapse(cands)
        self.assertNotIn("img/*", keys(out))
        self.assertEqual(len(out), len(cands))

    def test_keys_are_unique_across_a_mixed_changeset(self):
        cands = ([("clean/%d.png" % i, R) for i in range(MANY)] +
                 [("dirty/%d.png" % i, R) for i in range(MANY)] +
                 [("dirty/odd.png", OTHER)])
        k = keys(collapse(cands))
        self.assertEqual(len(k), len(set(k)), "a duplicate key closes two findings at once")
        self.assertIn("clean/*", k, "a clean directory should still collapse")


class TestTheReasonSurvivesCollapsing(unittest.TestCase):
    def test_the_original_reason_is_still_readable(self):
        out = collapse([("a/%d.png" % i, R) for i in range(MANY)])
        self.assertIn(R, out[0][1])


if __name__ == "__main__":
    unittest.main()
