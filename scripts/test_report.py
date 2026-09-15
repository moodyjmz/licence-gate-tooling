#!/usr/bin/env python3
"""The report, tested as the pure function it now is.

WHY THESE ARE NOT IN test_gate_integration. Every report assertion there has to
build a throwaway repository, commit into it, and run the gate, to reach one string
in one section - about 0.3 s each against 60 microseconds here. That cost is why the
renderer went untested in its own right: a case nobody can add cheaply is a case
nobody adds. The integration suite keeps proving the report is produced from a REAL
diff; what belongs here is what it says once it has one.

Each test names the report defect it guards against. The ones with a history behind
them say so; a test whose purpose is not obvious gets deleted by someone who cannot
see why it matters.
"""

import unittest

from report import render

NOTICE = "Modified by the Example project."


def report(**over):
    """A report with no findings, plus whatever this test is about."""
    args = dict(a=[], b=[], c=[], g=[], d_src=[], d_assets=[], d_links=[],
                d_foreign=[], claims=[], binary_skips=[], deleted_headers=[],
                register_touched=False, head_short="abc123def", author=None,
                notice=NOTICE)
    args.update(over)
    return render(**args)


class TestTheFirstLineAnswersTheFirstQuestion(unittest.TestCase):
    """"Does this merge?" is what the reader opens the comment to find out, and the
    report used to answer it in the third paragraph, in prose, by implication."""

    def test_a_blocking_finding_says_blocked(self):
        self.assertIn("blocked", report(a=["src/a.js"]).split("\n")[0])

    def test_candidates_alone_do_not_say_blocked(self):
        first = report(c=[("logo.png", "asset modified")]).split("\n")[0]
        self.assertNotIn("blocked", first)
        self.assertIn("reviewer", first)

    def test_nothing_at_all_says_nothing_to_do(self):
        self.assertIn("Nothing to do", report().split("\n")[0])

    def test_a_candidate_report_says_it_does_not_block(self):
        """A reviewer reading "waiting on a reviewer" must not think the merge is
        stopped by it; only the four blocking checks do that."""
        self.assertIn("Nothing here blocks the merge",
                      report(c=[("logo.png", "asset modified")]))


class TestEveryFindingNamesItsFiles(unittest.TestCase):
    """A count is not actionable. The reader has to go and find the files, and the
    report has the list already."""

    def test_the_notice_step_lists_them(self):
        out = report(a=["src/a.js", "src/b.js"])
        self.assertIn("- `src/a.js`", out)
        self.assertIn("- `src/b.js`", out)

    def test_a_removed_licence_line_is_quoted_not_counted(self):
        out = report(b=[("src/a.js", "Copyright (c) 2020 Example Corp")])
        self.assertIn("Copyright (c) 2020 Example Corp", out)

    def test_the_register_finding_carries_its_reason(self):
        out = report(g=[("MODIFICATIONS.md", "line 4 no longer matches")])
        self.assertIn("line 4 no longer matches", out)

    def test_a_new_file_is_named_and_says_why_it_is_listed(self):
        """The step heading says what to DO; the line says why this file is in it. A
        pull request mixing headerless new source with new assets needs both, and the
        first draft of this trim kept only the heading - caught by mutation, not by
        review."""
        out = report(d_src=["src/new.js"])
        self.assertIn("- `src/new.js`", out)
        self.assertIn("no licence header", out)

    def test_an_ownership_claim_quotes_the_line_that_was_added(self):
        """"A file now claims something" is not answerable; the reviewer has to see
        WHICH line appeared to say whether it is true."""
        out = report(claims=[("src/panel.js", "Copyright (c) 2099 Attacker")])
        self.assertIn("src/panel.js", out)
        self.assertIn("Copyright (c) 2099 Attacker", out)

    def test_the_std_licence_command_names_every_new_file(self):
        """It is an assertion of authorship, so it names what it asserts. A command
        the reader has to assemble by hand is one they get wrong."""
        out = report(d_src=["src/a.js", "src/b.js"])
        self.assertIn("/std-licence src/a.js src/b.js", out)


class TestFindingsAreListedFlush(unittest.TestCase):
    """The bullet shape is a contract, not styling.

    Indenting findings under their heading renders as a nested list and broke the
    integration suite's `_reason` helper, which recovers a path's findings by line
    prefix. A reader skimming a long report scans the same left margin.
    """

    def test_every_finding_line_starts_at_the_margin(self):
        out = report(a=["src/a.js"], c=[("logo.png", "asset modified")],
                     d_src=["src/new.js"])
        found = [l for l in out.split("\n") if l.lstrip().startswith("- `")]
        self.assertTrue(found)
        for line in found:
            self.assertFalse(line.startswith(" "), f"indented finding: {line!r}")


class TestTheBarOnTheAuthorIsStatedWhereTheRemedyIs(unittest.TestCase):
    """The report once told the only person reading it to do the one thing they are
    barred from doing, and never mentioned the bar. It then over-corrected and said
    it three times in one comment."""

    def test_the_author_is_named_as_barred(self):
        out = report(c=[("logo.png", "asset modified")], author="devperson")
        self.assertIn("not @devperson", out)

    def test_it_is_said_once(self):
        out = report(c=[("logo.png", "asset modified")], author="devperson")
        self.assertEqual(out.count("@devperson"), 1)

    def test_without_an_author_the_bar_is_still_stated(self):
        """The login is optional input; the bar is not conditional on it."""
        out = report(c=[("logo.png", "asset modified")])
        self.assertIn("not the author", out)


class TestTheTemplateCarriesItsOwnInstructions(unittest.TestCase):
    """Three paragraphs used to follow the block explaining what to type in it, which
    is where someone copying a template has stopped reading."""

    def test_the_commit_is_prefilled(self):
        out = report(c=[("logo.png", "asset modified")])
        self.assertIn("commit: abc123def", out)

    def test_each_candidate_gets_an_answerable_line(self):
        out = report(c=[("logo.png", "asset modified")])
        self.assertIn("logo.png ->", out)

    def test_the_guidance_sits_beside_the_line_it_is_about(self):
        line = next(l for l in report(c=[("logo.png", "x")]).split("\n")
                    if l.strip().startswith("logo.png ->"))
        self.assertIn("not a replacement", line)


class TestTheVerifiedBoxClaimsOnlyWhatRan(unittest.TestCase):
    """Every line in it tells the reader not to look, so it may only carry what this
    run established."""

    def test_a_clean_run_certifies_both_checks(self):
        out = report()
        self.assertIn("no licence or copyright line deleted or altered", out)
        self.assertIn("every modified file with a header carries a notice", out)

    def test_a_failed_check_is_not_certified(self):
        self.assertNotIn("no licence or copyright line deleted or altered",
                         report(b=[("src/a.js", "Copyright 2020 X")]))

    def test_skipped_binaries_qualify_the_claim(self):
        out = report(binary_skips=["logo.png"])
        self.assertIn("no lines to compare", out)
        self.assertNotIn("- no licence or copyright line deleted or altered\n", out)

    def test_a_deleted_licensed_file_qualifies_the_claim(self):
        self.assertIn("deleted outright", report(deleted_headers=["src/gone.js"]))

    def test_the_register_is_only_certified_when_one_was_touched(self):
        """Telling a pull request that never touched a register that its rows
        survived is a claim about a file this run never read."""
        self.assertNotIn("still there", report())
        self.assertIn("still there", report(register_touched=True))


class TestRationaleDoesNotCrowdOutTheAction(unittest.TestCase):
    """The trim's own regression test: the explanation may not outgrow the work."""

    def test_the_why_is_behind_a_fold(self):
        out = report(a=["src/a.js"])
        why = out.index("Why the gate asks")
        self.assertIn("<details>", out[:why][-200:])

    def test_the_actionable_part_comes_first(self):
        out = report(a=["src/a.js"])
        self.assertLess(out.index("/auto-fix"), out.index("Why the gate asks"))

    def test_a_clean_report_explains_nothing(self):
        """With no findings there is nothing to explain, and a fold inviting the
        reader to read about checks that all passed is noise."""
        self.assertNotIn("Why the gate asks", report())

    def test_the_visible_part_stays_short(self):
        """Three findings rendered 671 words before this. The cap is deliberately
        loose - it catches a section growing back, not a sentence."""
        out = report(a=["src/a.js", "src/b.js"], d_src=["src/c.js"],
                     c=[("logo.png", "asset modified")], author="devperson")
        visible = out.split("<details>")[0]
        self.assertLess(len(visible.split()), 200, visible)


class TestNoFindingIsDroppedByDeduplication(unittest.TestCase):
    """Assets, submodules and foreign-headered additions are folded into the
    candidate list rather than repeated in a section of their own. Folding is only
    safe if nothing is lost on the way."""

    def test_an_asset_addition_reaches_the_candidate_list(self):
        self.assertIn("logo.png", report(d_assets=["logo.png"]))

    def test_a_submodule_addition_reaches_it_too(self):
        self.assertIn("vendor/thing", report(d_links=["vendor/thing"]))

    def test_a_path_already_a_candidate_is_not_listed_twice(self):
        out = report(d_assets=["logo.png"], c=[("logo.png", "asset modified")])
        self.assertEqual(out.count("- `logo.png`"), 1)

    def test_the_more_specific_reason_survives(self):
        """check_c already ordered its reasons most-specific-first; folding here must
        not put a generic one back in front."""
        out = report(d_assets=["logo.png"], c=[("logo.png", "asset modified")])
        self.assertIn("asset modified", out)


if __name__ == "__main__":
    unittest.main()
