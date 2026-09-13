#!/usr/bin/env python3
"""Integration tests: the gate against real git repositories.

These exist because every defect found in the two security audits lived in the
git-interacting code, and none of them were reachable from the unit tests. Parsing
`git diff` output is where this tool meets input it does not control, so it is where
the bugs were: paths taken from diff content, paths silently mangled by git's own
quoting, structure inferred from how a line starts. A pure-function test suite could
not have caught a single one of them.

Each test reproduces an attack or failure that actually got through, named so the
next person can see why deleting it would be a bad idea. Slow by unit-test standards
and worth every millisecond.

stdlib only; no fixtures beyond a temporary directory.
"""

import os
import subprocess
import tempfile
import unittest

GATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "licence-gate.py")
STD_LICENCE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "apply-std-licence.py")

LICENSED = "/*\n * Copyright (c) 2020 Example Corp\n * Licensed under the Example License 1.0\n */\n"


class GateCase(unittest.TestCase):
    """A throwaway repository with two commits, and the gate's verdict on the diff."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._git("init", "-q", "-b", "main")

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

    def run_gate(self):
        """Returns (exit_code, report)."""
        p = subprocess.run(["python3", GATE, "HEAD~1", "HEAD"],
                           cwd=self.dir, capture_output=True, text=True)
        return p.returncode, p.stdout

    def assertBlocks(self, why):
        code, report = self.run_gate()
        self.assertEqual(code, 1, f"{why}\n--- gate said ---\n{report}")
        return report

    def assertClean(self, why):
        code, report = self.run_gate()
        self.assertEqual(code, 0, f"{why}\n--- gate said ---\n{report}")
        return report


class TestDiffStructureCannotBeForged(GateCase):
    def test_content_that_looks_like_a_diff_header_does_not_relabel_the_file(self):
        """The first audit's critical finding. A line reading `-- a/docs/decoy` in a
        file becomes `--- a/docs/decoy` in the diff once the deletion marker is
        prepended. Two of those above the real edit repointed the parser at docs/,
        which is ignored, and the copyright rewrite below was skipped - exit 0, with
        the report stating no copyright line had been altered."""
        self.write("src/real.js", "// top\n-- a/docs/decoy\n" + LICENSED + "const x = 1;\n")
        self.commit("base")
        self.write("src/real.js", "// top\n++ b/docs/decoy\n"
                   + LICENSED.replace("2020 Example Corp", "2099 Attacker") + "const x = 1;\n")
        self.commit("rewrite the holder")
        report = self.assertBlocks("a forged diff header must not hide a copyright rewrite")
        self.assertIn("removed or altered", report)

    def test_a_deleted_line_beginning_with_dashes_is_still_content(self):
        """The second audit's critical finding, one layer down. A deleted line whose
        own text starts `--` (SQL, Lua, Haskell, Ada, or a Markdown rule) arrives as
        `--- ...` and was skipped as though it were a file header. Deleting a copyright
        line from a .sql file passed, certified as unaltered."""
        self.write("src/schema.sql", "-- Copyright (c) 2020 Example Corp\nSELECT 1;\n")
        self.commit("base")
        self.write("src/schema.sql", "SELECT 1;\n")
        self.commit("drop the copyright line")
        self.assertBlocks("a SQL-style comment is content, not a diff header")


class TestHeadersThatAreNotSpelledCopyright(GateCase):
    """HEADER_RE required the literal word "copyright" before any (c)/©/year, so the
    three commonest bare forms matched nothing at all. Not a hypothetical: deleting a
    `© 2020 Example Corp` line passed the gate, green, with the report stating under
    "you need not check these" that no copyright line had been altered."""

    def _drop_the_header_line(self, header):
        self.write("src/sym.js", "/*\n" + header + "\n */\nconst x = 1;\n")
        self.commit("base")
        self.write("src/sym.js", "/*\n */\nconst x = 1;\n")
        self.commit("drop the header line")

    def test_a_bare_copyright_sign_line_cannot_be_deleted_silently(self):
        self._drop_the_header_line(" * © 2020 Example Corp")
        report = self.assertBlocks("deleting a © line must block")
        self.assertIn("removed or altered", report)
        self.assertNotIn("no licence or copyright line deleted", report,
                         "the verified box must not certify what was not checked")

    def test_a_paren_c_line_cannot_be_deleted_silently(self):
        self._drop_the_header_line(" * (c) 2020 Example Corp")
        self.assertIn("removed or altered",
                      self.assertBlocks("deleting a (c) line must block"))

    def test_an_all_rights_reserved_line_cannot_be_deleted_silently(self):
        self._drop_the_header_line(" * (C) 2020 Example Corp, all rights reserved")
        self.assertIn("removed or altered",
                      self.assertBlocks("deleting an (C)/all-rights-reserved line must block"))


class TestCheckBWithoutADiffParser(GateCase):
    """check_b compares the base blob against the head blob. It used to read structure
    off `git diff` text, and two of the sixteen defects found in this tool existed only
    because that hand-rolled state machine over diff syntax existed at all."""

    def test_a_deleted_file_is_not_a_gate_error(self):
        """The blob comparison asks git for both ends. A deletion has no head blob, and
        asking for one would turn an ordinary `git rm` into "the gate could not run" -
        trading a false all-clear for a false alarm on every PR that removes a file."""
        self.write("src/gone.js", LICENSED + "const x = 1;\n")
        self.write("src/stay.js", "const y = 1;\n")
        self.commit("base")
        os.remove(os.path.join(self.dir, "src/gone.js"))
        self.commit("remove the licensed file")
        code, report = self.run_gate()
        self.assertNotIn("could not run", report)
        self.assertEqual(code, 1, report)
        self.assertIn("removed or altered", report,
                      "deleting a licensed file deletes its licence lines")

    def test_a_pure_addition_is_not_read_as_a_removal(self):
        """The other end of the same asymmetry: an added file has no base blob. It must
        not be looked for, and its lines cannot have been removed from anywhere."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self.write("src/new.js", LICENSED + "const n = 1;\n")
        self.commit("add a licensed file")
        report = self.assertClean("adding an already-licensed file is not a violation")
        self.assertNotIn("removed or altered", report,
                         "adding a header must never read as removing one")

    def test_a_reordered_header_line_is_not_a_false_positive(self):
        """The stated cost of dropping positional diffing, pinned so it is a decision
        rather than a surprise: a header line that merely MOVES is still present in the
        head blob, so it no longer reports. The line is there, byte for byte."""
        self.write("src/a.js", "/*\n * Copyright (c) 2020 Example Corp\n"
                               " * Licensed under the Example License 1.0\n"
                               " * Modified by the Example project.\n */\nconst x = 1;\n")
        self.commit("base")
        self.write("src/a.js", "/*\n * Licensed under the Example License 1.0\n"
                               " * Copyright (c) 2020 Example Corp\n"
                               " * Modified by the Example project.\n */\nconst x = 2;\n")
        self.commit("swap the two header lines round")
        code, report = self.run_gate()
        self.assertEqual(code, 0, report)

    def test_altering_a_header_line_still_blocks(self):
        """The case the reorder test must not be allowed to weaken. Rewriting the
        holder changes the line's text, so the old text is absent from the head blob."""
        self.write("src/a.js", LICENSED + "const x = 1;\n")
        self.commit("base")
        self.write("src/a.js", LICENSED.replace("2020 Example Corp", "2099 Attacker")
                   + "const x = 1;\n")
        self.commit("rewrite the holder")
        self.assertIn("2020 Example Corp",
                      self.assertBlocks("rewriting the holder must block"))

    def test_replacing_a_licensed_file_with_a_submodule_still_blocks(self):
        """A regression introduced BY the blob rewrite and caught before it shipped.
        `changed_files` flags a pair as a gitlink when EITHER end has mode 160000, so
        skipping the pair symmetrically made `git rm` + a gitlink at the same path -
        git calls it T - pass in silence. The whole header ceased to exist and the gate
        exited 0. The diff-text version it replaced blocked this, so the fix was weaker
        than the bug. Which end has no blob decides: no base blob means nothing to
        delete from; no head blob means everything was deleted."""
        self.write("src/licensed.js", LICENSED + "const x = 1;\n")
        self.commit("base")
        self._git("rm", "-q", "--cached", "src/licensed.js")
        os.remove(os.path.join(self.dir, "src/licensed.js"))
        self._git("update-index", "--add", "--cacheinfo",
                  f"160000,{'9' * 40},src/licensed.js")
        self._git("commit", "-q", "-m", "replace the licensed file with a submodule")
        status = self._git("diff", "--raw", "HEAD~1", "HEAD").stdout
        self.assertIn("160000", status, f"expected a gitlink, got {status!r}")
        report = self.assertBlocks("a file replaced by a gitlink loses its whole header")
        self.assertIn("removed or altered", report)
        self.assertIn("2020 Example Corp", report)

    def test_a_font_swap_reaches_a_human(self):
        """The known open cost of dropping the diff parser, pinned at the level that
        survives whatever is decided about it.

        `git diff` said `Binary files … differ`, so check_b never saw binary content.
        A blob comparison reads it under errors="replace", and a TrueType `name` table
        carries `Copyright (c) 2011 …` as plain ASCII - so a font swap now trips
        check_b as well as raising a candidate, and check_b's advice ("restore the
        original line exactly") cannot be followed inside a .woff2.

        Asserted here: the path reaches a human. NOT the exit code, and not which list
        it lands in - routing a textually-unfixable violation to the candidate list
        instead of the blocking one is an open product decision, and a test that pinned
        today's answer would have to be edited to make that change, which is how tests
        stop meaning anything. Excluding binaries from check_b is not on the table: a
        font's copyright string is licence text and swapping it is the event."""
        ttf = os.path.join(self.dir, "assets/brand.ttf")
        os.makedirs(os.path.dirname(ttf), exist_ok=True)
        with open(ttf, "wb") as fh:
            fh.write(b"\x00\x01\x00\x00Copyright (c) 2011 Example Foundry\xff\xfe\x00")
        self.commit("base")
        with open(ttf, "wb") as fh:
            fh.write(b"\x00\x01\x00\x00Copyright (c) 2024 Other Foundry\xff\xfe\x00")
        self.commit("swap the font")
        _code, report = self.run_gate()
        self.assertNotIn("could not run", report)
        self.assertIn("assets/brand.ttf", report)
        self.assertIn("assets/brand.ttf", self._candidates(),
                      "a font swap is the canonical replacement event; it must reach "
                      "the acknowledgement gate whatever else happens to it")

    def _candidates(self):
        p = subprocess.run(["python3", GATE, "--candidates", "HEAD~1", "HEAD"],
                           cwd=self.dir, capture_output=True, text=True)
        return p.stdout.split()

    def test_a_submodule_bump_is_not_a_gate_error(self):
        """A gitlink has no blob, so the blob comparison must skip it BY MODE. Watching
        `git show` fail instead is precisely how submodules became invisible to every
        check. It is skipped here and raised by check_c, not dropped."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self._git("update-index", "--add", "--cacheinfo",
                  f"160000,{'5' * 40},vendor/dep")
        self._git("commit", "-q", "-m", "add a submodule")
        self._git("update-index", "--cacheinfo",
                  f"160000,{'6' * 40},vendor/dep")
        self._git("commit", "-q", "-m", "bump the submodule")
        report = self.assertClean("a submodule bump prompts a human; it does not error")
        self.assertNotIn("could not run", report)
        self.assertIn("vendor/dep", report)


class TestPathsGitQuotes(GateCase):
    def test_a_non_ascii_filename_is_not_invisible(self):
        """No attacker needed for this one. git C-quotes any path with a non-ASCII
        byte, so `src/héader.py` was handed to `git show` as the literal string
        `"src/h\\303\\251ader.py"`, which names no file. git wrote its error to stderr,
        the gate read only stdout, and an empty result was treated as nothing to check
        - while the report certified that the checks had passed."""
        self.write("src/héader.py", "# Copyright (c) 2020 Example Corp\nk = 1\n")
        self.commit("base")
        self.write("src/héader.py", "k = 2\n")
        self.commit("drop the copyright line")
        report = self.assertBlocks("an accented filename must not be skipped")
        self.assertIn("héader.py", report)

    def test_a_filename_with_a_quote_and_a_backslash_is_not_invisible(self):
        self.write('src/o"dd\\name.py', "# Copyright (c) 2020 Example Corp\nk = 1\n")
        self.commit("base")
        self.write('src/o"dd\\name.py', "k = 2\n")
        self.commit("drop the copyright line")
        self.assertBlocks("git-quoted paths must still be checked")


class TestRenameAndEdit(GateCase):
    def test_a_renamed_and_edited_file_still_needs_its_notice(self):
        """git reports a rename INSTEAD of a modification, never alongside it, so a
        file renamed and edited in one commit never reached check A. It kept its
        header, gained content, needed a notice - and the report said every modified
        file with a header carried one."""
        self.write("src/licensed.py", LICENSED + "v = 1\n")
        self.commit("base")
        self._git("mv", "src/licensed.py", "src/licensed_v2.py")
        self.write("src/licensed_v2.py", LICENSED + "v = 1\nw = 4\n")
        self.commit("rename and edit")
        report = self.assertBlocks("a rename must not shed the notice requirement")
        self.assertIn("licensed_v2.py", report)


class TestTheGateStaysUsable(GateCase):
    def test_a_readme_saying_copyright_does_not_block_for_ever(self):
        """Broadening the header pattern swept documentation into check A, which has
        no ignore list of its own. A README whose first line reads `# Copyright (c)
        2020 Example Corp` - an entirely ordinary thing to write - then demanded a
        modification notice on every edit, and /auto-fix could not supply one because
        Markdown has no comment block to close. A requirement nothing can satisfy is
        how a gate loses the people it is meant to serve."""
        self.write("README.md", "# Copyright (c) 2020 Example Corp\n\nDocs.\n")
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self.write("README.md", "# Copyright (c) 2020 Example Corp\n\nDocs, edited.\n")
        self.commit("edit the readme")
        self.assertClean("documentation must not be swept into the notice requirement")


class TestTheGateNeverFailsQuiet(GateCase):
    def test_an_unreachable_base_blocks_rather_than_certifying(self):
        """sh() returned stdout and ignored exit codes, so an unreachable base commit
        - a force-push between event and job, a checkout without full history -
        produced empty output, empty file lists, and a report certifying a clean tree
        having analysed nothing at all. No attacker required."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self.write("src/a.js", "const a = 2;\n")
        self.commit("edit")
        p = subprocess.run(["python3", GATE, "0" * 40, "HEAD"],
                           cwd=self.dir, capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("could not run", p.stdout)
        self.assertNotIn("Verified automatically", p.stdout)

    def test_an_unhandled_status_letter_is_not_a_skipped_file(self):
        """Replacing a licensed file with a symlink is reported as T, not D+A. The
        status loop named only A/D/M/R/C, so the file entered no list and reached no
        check - the entire header ceased to exist and the report stated that no
        copyright line had been altered."""
        self.write("src/real.js", LICENSED + "const x = 1;\n")
        self.commit("base")
        os.remove(os.path.join(self.dir, "src/real.js"))
        os.symlink("/etc/passwd", os.path.join(self.dir, "src/real.js"))
        self.commit("swap the file for a symlink")
        status = self._git("diff", "--name-status", "HEAD~1", "HEAD").stdout
        self.assertTrue(status.startswith("T"), f"expected a type change, got {status!r}")
        self.assertBlocks("a type change must not bypass every check")


class TestAutoFixDoesWhatItSays(GateCase):
    def _fix(self):
        return subprocess.run(["python3", GATE, "--fix", "HEAD~1", "HEAD"],
                              cwd=self.dir, capture_output=True, text=True)

    def test_a_line_comment_header_can_actually_be_fixed(self):
        """apply_fix could only insert before */ or -->, the closing tokens of block
        comments. Nine of the eleven extensions this tool stamps use LINE comments and
        have no closing token, so the file was left untouched and nothing recorded:
        the gate blocked, told the reviewer to run /auto-fix, and /auto-fix answered
        "Nothing to auto-fix". A remedy that quietly does nothing is worse than none -
        it gives the reviewer a reason to stop looking."""
        self.write("src/a.py", "# Copyright (c) 2020 Example Corp\n# Licensed under the Example License 1.0\nk = 1\n")
        self.commit("base")
        self.write("src/a.py", "# Copyright (c) 2020 Example Corp\n# Licensed under the Example License 1.0\nk = 2\n")
        self.commit("edit")
        self.assertBlocks("a line-comment header still needs a notice")
        self._fix()
        with open(os.path.join(self.dir, "src/a.py"), encoding="utf-8") as fh:
            fixed = fh.read()
        self.assertIn("Modified by the Example project.", fixed)
        self.assertIn("# Modified by", fixed, "the notice must use the file's own comment marker")
        self.assertIn("k = 2", fixed, "the fix must not eat the file")

    def test_a_file_it_cannot_fix_is_reported_not_swallowed(self):
        self.write("src/real.js", LICENSED + "const x = 1;\n")
        self.commit("base")
        os.remove(os.path.join(self.dir, "src/real.js"))
        os.symlink("/etc/passwd", os.path.join(self.dir, "src/real.js"))
        self.commit("swap for a symlink")
        p = self._fix()
        self.assertEqual(p.returncode, 3, "an unfixable file must not look like success")
        self.assertIn("could not auto-fix", p.stderr)

    def test_a_bare_copyright_sign_header_is_remediable(self):
        """Broadening HEADER_RE widened check_a's input set, and check_a's report tells
        the reviewer to run /auto-fix. A newly-detected header form that /auto-fix
        cannot place a notice in would be a requirement nothing can satisfy - the exact
        way this gate loses the people it serves - so every form the gate started
        blocking on has to be one the remedy can finish."""
        header = "// © 2020 Example Corp\n"
        self.write("src/c.js", header + "const x = 1;\n")
        self.commit("base")
        self.write("src/c.js", header + "const x = 2;\n")
        self.commit("edit")
        self.assertBlocks("a bare © header still needs a notice")
        p = self._fix()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(self.dir, "src/c.js"), encoding="utf-8") as fh:
            fixed = fh.read()
        self.assertIn("// Modified by the Example project.", fixed,
                      "a form the gate blocks on must be one /auto-fix can finish")
        self.assertIn("const x = 2;", fixed)

    def test_a_block_comment_header_still_works(self):
        self.write("src/a.js", LICENSED + "const x = 1;\n")
        self.commit("base")
        self.write("src/a.js", LICENSED + "const x = 2;\n")
        self.commit("edit")
        self.assertBlocks("block-comment header needs a notice")
        self._fix()
        with open(os.path.join(self.dir, "src/a.js"), encoding="utf-8") as fh:
            fixed = fh.read()
        self.assertIn(" * Modified by the Example project.", fixed)
        self.assertIn("const x = 2;", fixed)
        self.assertLess(fixed.index("Modified by"), fixed.index("*/"),
                        "the notice belongs inside the comment block")


class TestAutoFixNeverWritesInvalidSyntax(GateCase):
    """/auto-fix holds contents:write and pushes what it writes, unreviewed. Every
    test here is about what lands in someone's branch."""

    def _fix(self):
        return subprocess.run(["python3", GATE, "--fix", "HEAD~1", "HEAD"],
                              cwd=self.dir, capture_output=True, text=True)

    def _read(self, path, **kw):
        with open(os.path.join(self.dir, path), encoding="utf-8", **kw) as fh:
            return fh.read()

    def test_a_header_closing_on_its_own_last_line_of_text_stays_a_comment(self):
        """The block-comment branch looked for a closing token ALONE on its line. A
        header closing on the same line as its last text - ` * Licensed under X */`,
        ordinary style - matched nothing, so execution fell through to the
        line-comment branch, whose marker pattern matched the body's leading `*`, and
        the notice was appended AFTER the line holding `*/`: outside the comment, as a
        bare statement. `node --check` rejects the result. /auto-fix committed and
        pushed it."""
        header = ("/*\n * Copyright (c) 2020 Example Corp\n"
                  " * Licensed under the Example License 1.0 */\n")
        self.write("src/a.js", header + "function f() { return 1; }\n")
        self.commit("base")
        self.write("src/a.js", header + "function f() { return 2; }\n")
        self.commit("edit")
        self.assertBlocks("a same-line closer still carries a header")
        self._fix()
        fixed = self._read("src/a.js")
        self.assertIn("Modified by the Example project.", fixed)
        self.assertLess(fixed.index("Modified by"), fixed.index("*/"),
                        "the notice must land INSIDE the comment, not after its closer")
        self.assertIn("function f() { return 2; }", fixed, "the fix must not eat the file")

    def test_a_one_line_block_comment_header_is_not_broken_open(self):
        """The sibling of the case above: a header that opens AND closes on one line.
        Same root cause - the closer was recognised only when it stood alone - so the
        same fall-through wrote the notice outside the comment."""
        self.write("src/a.css", "/* Copyright (c) 2020 Example Corp */\nbody { color: red; }\n")
        self.commit("base")
        self.write("src/a.css", "/* Copyright (c) 2020 Example Corp */\nbody { color: blue; }\n")
        self.commit("edit")
        self.assertBlocks("a one-line block header still needs a notice")
        self._fix()
        fixed = self._read("src/a.css")
        self.assertLess(fixed.index("Modified by"), fixed.index("*/"),
                        "the notice must land inside the comment")
        self.assertIn("body { color: blue; }", fixed)

    def test_a_crlf_file_keeps_its_line_endings(self):
        """Reading with universal newlines translated every CRLF to LF on the way in,
        and the write put LF back - so adding one notice line to a CRLF file rewrote
        every line in it. /auto-fix then committed and pushed a whole-file rewrite as
        a one-line mechanical change, which is both unreviewable and, in a repository
        that has not normalised endings, a functional change to the file."""
        self._git("config", "core.autocrlf", "false")
        header = ("/*\r\n * Copyright (c) 2020 Example Corp\r\n"
                  " * Licensed under the Example License 1.0\r\n */\r\n")
        self.write("src/b.js", header + "function f() { return 1; }\r\n")
        self.commit("base")
        self.write("src/b.js", header + "function f() { return 2; }\r\n")
        self.commit("edit")
        self.assertBlocks("a CRLF file still needs a notice")
        self._fix()
        raw = self._read("src/b.js", newline="")
        self.assertIn("Modified by the Example project.\r\n", raw,
                      "the inserted line must use the file's own ending")
        self.assertNotIn("\n", raw.replace("\r\n", ""), "no line may have lost its CR")

    def test_the_notice_is_never_written_into_a_string_literal(self):
        """The anchor was searched over `max(leading comment region, 40)` lines, so a
        file whose comment region holds no header was still searched 40 lines deep and
        anchored on whatever looked like one. A licence line inside a triple-quoted
        string is program DATA, and the notice was written into the middle of it,
        reported as fixed, committed and pushed. A remedy may refuse; it may not
        guess."""
        self.write("src/d.py", "# Copyright (c) 2020 Example Corp\nx = 1\n")
        self.commit("base")
        self.write("src/d.py", 'x = 2\nDOC = """\n# Copyright (c) 2020 Example Corp\nmore\n"""\n')
        self.commit("move the header into a string")
        self.assertBlocks("a file that had a header still needs its notice")
        p = self._fix()
        self.assertEqual(p.returncode, 3, "an unplaceable notice must not look like success")
        self.assertIn("could not auto-fix", p.stderr)
        self.assertNotIn("Modified by", self._read("src/d.py"),
                         "nothing may be written into a string literal")


class TestEntriesThatAreNotFiles(GateCase):
    def _candidates(self):
        p = subprocess.run(["python3", GATE, "--candidates", "HEAD~1", "HEAD"],
                           cwd=self.dir, capture_output=True, text=True)
        return p.stdout.split()

    def test_a_submodule_is_visible_to_every_check(self):
        """`git show head:<gitlink>` writes to stderr and returns empty stdout, and the
        checks read that as "an empty file, nothing to check". A submodule therefore
        reached no list, raised no candidate, and the acknowledgement gate reported
        "No replacement candidates detected" - a green tick on unrecorded vendored
        content, which is the one thing this tool exists to prevent.

        Both names are here on purpose. `.py` matched SOURCE_RE and was read as an
        empty source file; the extensionless one matched TEXT_RE's no-extension rule
        and fell through every branch. Classifying by extension is what hid them, so
        the fix reads git's mode and the name says nothing."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        for i, path in enumerate(("vendor/thing.py", "vendor/noext"), start=1):
            self._git("update-index", "--add", "--cacheinfo",
                      f"160000,{str(i) * 40},{path}")
        self._git("commit", "-q", "-m", "vendor two submodules")
        report = self.assertClean("a submodule prompts a human; it does not block")
        self.assertEqual(sorted(self._candidates()), ["vendor/noext", "vendor/thing.py"],
                         "every gitlink must reach the acknowledgement gate")
        for path in ("vendor/thing.py", "vendor/noext"):
            self.assertIn(path, report)
        self.assertIn("submodule", report)

    def test_a_genuinely_empty_file_does_not_crash_the_gate(self):
        """The other half of the same fix. "git show failed" and "the file is empty"
        had to stop being the same answer - and an empty file is perfectly legal, so
        making the gate strict about the first must not make it fall over on the
        second."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self.write("src/empty.py", "")
        self.write("src/a.js", "const a = 2;\n")
        self.commit("add an empty file")
        report = self.assertBlocks("an empty new source file needs a licence decision")
        self.assertIn("src/empty.py", report)


class TestBinaryFilesAreOrdinary(GateCase):
    def test_editing_a_png_produces_a_report_rather_than_a_traceback(self):
        """`text=True` with no `errors=` raises UnicodeDecodeError INSIDE
        subprocess.run, before any returncode is looked at, so it escaped the handler
        wrapped around main. Editing any PNG - an entirely ordinary pull request -
        killed the gate with a traceback and the report file was never written. The
        action posts that file as the review comment, so the comment body was EMPTY,
        which reads exactly like "ran, found nothing"."""
        png = os.path.join(self.dir, "src/i.png")
        os.makedirs(os.path.dirname(png), exist_ok=True)
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n\xff\xfe\xfd\xfc")
        self.commit("base")
        with open(png, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n\x01\x02\x03\xf0\x9f")
        self.commit("edit the png")
        p = subprocess.run(["python3", GATE, "HEAD~1", "HEAD"],
                           cwd=self.dir, capture_output=True, text=True)
        self.assertNotIn("Traceback", p.stderr)
        self.assertTrue(p.stdout.strip(), "an empty report reads as a clean result")
        self.assertIn("src/i.png", p.stdout, "an edited asset is still a candidate")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


class TestStdLicenceNeverCertifiesWhatItDidNotRead(GateCase):
    def test_an_unreachable_base_is_not_no_new_files(self):
        """apply-std-licence.py never adopted the strict git wrapper the gate got. With
        an unreachable base - a force-push between the comment and the job, a checkout
        without full history - the enumeration came back empty and it printed "No new
        files needing a licence decision." to a reviewer who had just asserted
        authorship over specific files, having examined none of them. It holds
        contents:write."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self.write("src/b.js", "const b = 1;\n")
        self.commit("add a file")
        p = subprocess.run(["python3", STD_LICENCE, "0" * 40, "HEAD", "src/b.js"],
                           cwd=self.dir, capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertNotIn("No new files needing", p.stdout)
        self.assertIn("could not run", p.stdout)
        self.assertNotIn("Traceback", p.stderr)


class TestDotlessAdditionsAreNotInvisible(GateCase):
    """TEXT_RE's `^[^.]+$` classified an extensionless path as plain text. It was
    therefore not an asset, did not match SOURCE_RE, and fell off the end of check_d
    with NO branch taken: never listed as needing a decision, never a candidate, never
    even mentioned under "not checked". A newly added dotless binary - a vendored
    tool, a font renamed without its suffix - was simply invisible."""

    def _candidates(self):
        p = subprocess.run(["python3", GATE, "--candidates", "HEAD~1", "HEAD"],
                           cwd=self.dir, capture_output=True, text=True)
        return p.stdout.split()

    def test_a_new_extensionless_binary_reaches_a_human(self):
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        with open(os.path.join(self.dir, "VENDORTOOL"), "wb") as fh:
            fh.write(b"\x7fELF\x02\x01\x01\x00\xff\xfe")
        self.commit("vendor a tool with no extension")
        report = self.assertClean("an added asset prompts a human; it does not block")
        self.assertIn("VENDORTOOL", report,
                      "an extensionless addition must not vanish from the report")
        self.assertIn("VENDORTOOL", self._candidates(),
                      "it must reach the acknowledgement gate, not just the comment")

    def test_a_directory_with_a_dot_in_its_name_does_not_hide_the_file(self):
        """`^[^.]+$` is tested against the whole path, so `src/v1.2/tool` has a dot and
        was "not extensionless" while still having no extension. The test belongs on
        the basename."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self.write("src/v1.2/tool", "binary-ish\n")
        self.commit("add a dotless file under a dotted directory")
        self.assertIn("src/v1.2/tool", self._candidates())

    def test_a_dotless_gitlink_is_still_reported_as_a_submodule(self):
        """The dotless branch must keep `and p not in gitlinks`. The dedup that adds
        the submodule reason runs AFTER the added-asset loop, so a gitlink admitted
        as "asset added" would suppress the one sentence that tells the reviewer the
        content lives in another repository entirely."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self._git("update-index", "--add", "--cacheinfo",
                  f"160000,{'7' * 40},vendor/noext")
        self._git("commit", "-q", "-m", "vendor a submodule with no extension")
        report = self.assertClean("a submodule prompts a human; it does not block")
        self.assertIn("submodule", report)
        self.assertNotIn("asset or extensionless file added", report,
                         "a gitlink must not be mislabelled as an ordinary asset")


class TestOneDiffEnumerator(GateCase):
    """licence-gate.py and apply-std-licence.py kept two independent enumerators. The
    gate's read `git diff --raw -z` and could see modes; this one read `--name-status
    -z`, which carries none, so it could not see a submodule at all."""

    def test_std_licence_refuses_a_submodule_for_the_true_reason(self):
        """It always refused - but by ACCIDENT. `open()` on the submodule path raised,
        has_header read an unreadable file as "leave alone", and the reported reason
        was "already carries a header; not overwriting an existing claim": a statement
        about content that does not exist in this repository at all. Two unrelated
        behaviours lining up is not a design, nothing tested it, and this is the script
        that holds contents:write."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self._git("update-index", "--add", "--cacheinfo",
                  f"160000,{'8' * 40},vendor/dep")
        self._git("commit", "-q", "-m", "vendor a submodule")
        p = subprocess.run(["python3", STD_LICENCE, "HEAD~1", "HEAD", "vendor/dep"],
                           cwd=self.dir, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("vendor/dep", p.stdout)
        self.assertIn("submodule", p.stdout,
                      "the reason must name what the entry actually is")
        self.assertNotIn("already carries a header", p.stdout,
                         "refusing for an untrue reason is how the accident hid")

    def test_std_licence_sees_a_non_ascii_added_path(self):
        """The shared enumerator brings -z with it. The point of moving it is that a
        fix made in one place is not a fix someone has to remember to copy."""
        self.write("src/a.js", "const a = 1;\n")
        self.commit("base")
        self.write("src/héader.py", "k = 1\n")
        self.commit("add an accented filename")
        p = subprocess.run(["python3", STD_LICENCE, "HEAD~1", "HEAD", "src/héader.py"],
                           cwd=self.dir, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("héader.py", p.stdout)
        with open(os.path.join(self.dir, "src/héader.py"), encoding="utf-8") as fh:
            self.assertIn("SPDX-License-Identifier", fh.read(),
                          "a path git would have C-quoted must still be stamped")


class TestAutomatedDependencyUpdates(GateCase):
    """A gate that blocks the bot everybody has running is a gate that gets disabled."""

    def test_a_dependency_bump_does_not_block(self):
        """Verified against a realistic Dependabot-shaped change. Rewriting a vendored
        file removes upstream's old copyright line and adds their new one, so the gate
        demanded two things nobody could do: "restore the licence line" - upstream
        changed their own year, legitimately - and "add the modification notice" - we
        did not modify it. The author is a bot that cannot answer either, so the only
        exit was a human overriding the gate, which teaches the team that overriding
        the gate is routine."""
        self.write("vendor/leftpad/index.js",
                   "/*\n * Copyright (c) 2019 Upstream Authors\n"
                   " * Licensed under the MIT License\n */\nmodule.exports=1;\n")
        self.write("package-lock.json", '{"packages":{"leftpad":{"version":"1.2.0"}}}\n')
        self.commit("base")
        self.write("vendor/leftpad/index.js",
                   "/*\n * Copyright (c) 2024 Upstream Authors\n"
                   " * Licensed under the MIT License\n */\nmodule.exports=2;\n")
        self.write("package-lock.json", '{"packages":{"leftpad":{"version":"1.3.0"}}}\n')
        self.commit("bump leftpad 1.2.0 -> 1.3.0")
        report = self.assertClean("a dependency bump must not block")
        self.assertIn("vendor/leftpad/index.js", report,
                      "it must still reach a reviewer - third-party content landing in "
                      "our tree is the most interesting thing the register records")

    def test_a_lockfile_only_change_is_quiet(self):
        """The common case. A gate that prompts on every lockfile edit is noise."""
        self.write("package-lock.json", '{"packages":{"a":{"version":"1.0.0"}}}\n')
        self.write("src/app.js", "const a = 1;\n")
        self.commit("base")
        self.write("package-lock.json", '{"packages":{"a":{"version":"1.1.0"}}}\n')
        self.commit("bump a")
        report = self.assertClean("a lockfile edit must not prompt")
        # Not a bare substring test: "candidate" appears in the standing boilerplate
        # under "not checked". What matters is that no candidates SECTION was emitted.
        self.assertNotIn("A reviewer must answer these", report)
        self.assertIn("Nothing to do", report)

    def test_our_own_code_is_still_checked(self):
        """The exemption is for trees we do not maintain. Moving a licensed file into
        vendor/ must not be a way to stop it being checked."""
        self.write("src/mine.js", LICENSED + "const x = 1;\n")
        self.commit("base")
        self.write("src/mine.js", "/*\n */\nconst x = 2;\n")
        self.commit("strip the header from our own file")
        self.assertBlocks("our own files are not vendored")


class TestAddedOwnershipClaims(GateCase):
    """Found by a reviewer, who merged it to main inside an hour of being given access.
    A red-team run had named the mechanism the round before and it was written off as a
    documented limitation - "we only report deletions" describes the code, not the
    guarantee anyone believes they have."""

    def test_an_added_copyright_claim_reaches_a_reviewer(self):
        """The live bypass: append a second header claiming someone else's ownership,
        add the modification notice correctly, and the gate said "Nothing to do - both
        checks pass" with the verified box confirming no copyright line had been
        altered. True, and useless: nothing was altered because something was invented."""
        self.write("src/panel.js", LICENSED + 'export function panel(){ return 1; }\n')
        self.commit("base")
        self.write("src/panel.js",
                   LICENSED.replace(" */", " * Modified by the Example project.\n */")
                   + "\n/**\n * Copyright (c) 2022 Evil Corp\n"
                     " * Licensed under the Evil License 6.66\n */\n"
                     'export function panel(){ return 1; }\n')
        self.commit("claim ownership")
        code, report = self.run_gate()
        self.assertIn("Ownership claimed", report,
                      "an added ownership claim must be surfaced, not ignored")
        self.assertIn("Evil Corp", report, "say which line, or the reviewer cannot judge")
        self.assertNotIn("Nothing to do", report)

    def test_the_modification_notice_is_not_an_ownership_claim(self):
        """The tool's own addition must not trip its own check, or /auto-fix creates
        work for a reviewer every time it runs."""
        self.write("src/a.js", LICENSED + "const x = 1;\n")
        self.commit("base")
        self.write("src/a.js",
                   LICENSED.replace(" */", " * Modified by the Example project.\n */")
                   + "const x = 2;\n")
        self.commit("add the notice")
        code, report = self.run_gate()
        self.assertNotIn("Ownership claimed", report)
        self.assertEqual(code, 0, report)

    def test_an_unchanged_file_claims_nothing(self):
        self.write("src/a.js", LICENSED + "const x = 1;\n")
        self.write("src/b.js", "const y = 1;\n")
        self.commit("base")
        self.write("src/b.js", "const y = 2;\n")
        self.commit("touch an unlicensed file")
        code, report = self.run_gate()
        self.assertNotIn("Ownership claimed", report)


class TestTheBaseIsTheMergeBase(GateCase):
    """The base sha an event hands the gate is the base BRANCH'S TIP, not the point
    the pull request branched from. Diffing `base..head` therefore replays every
    commit the base branch gained since the author branched, in reverse, and attributes
    it to them: a licence header added on main after they branched is reported as a
    licence line THEY removed, on a file their pull request never touched. Two blocking
    failures, neither of them about the contribution. Across a dozen repositories every
    stale branch showed the base branch's own recent work as its crimes, and the noise
    was read as the gate working.

    The fix is not `...`. Three-dot syntax fixes the enumeration and leaves every
    `git show` in check A and check B reading the base branch's tip, so the diff and
    the blobs disagree about what "base" means - a quieter version of the same bug.
    The base is resolved once, to the merge-base, and that one commit is what both the
    enumeration and the blob reads see."""

    def gate(self, base, head):
        p = subprocess.run(["python3", GATE, base, head],
                           cwd=self.dir, capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    def _diverge(self):
        """A base commit, then a `pr` branch off it. Leaves the checkout on main."""
        self.write("src/f.js", "const f = 1;\n")
        self.write("src/g.js", "const g = 1;\n")
        self.commit("base")
        self._git("branch", "pr")

    def test_a_header_added_on_the_base_branch_is_not_a_removal_by_the_author(self):
        self._diverge()
        self.write("src/f.js", LICENSED + "const f = 1;\n")
        self.commit("main licenses a file the pull request never touches")
        self._git("checkout", "-q", "pr")
        self.write("src/g.js", "const g = 2;\n")
        self.commit("edit an unrelated file")

        # Assert the topology, or this test can pass for the wrong reason.
        two_dot = self._git("diff", "--name-only", "main..pr").stdout
        three_dot = self._git("diff", "--name-only", "main...pr").stdout
        self.assertIn("src/f.js", two_dot, "the defect needs f.js in the two-dot diff")
        self.assertNotIn("src/f.js", three_dot, "f.js is not part of the contribution")

        code, report, _err = self.gate("main", "pr")
        self.assertNotIn("src/f.js", report,
                         "the base branch's own work must not be reported as the "
                         "author's removal")
        self.assertEqual(code, 0, report)

    def test_a_removal_by_the_pull_request_itself_still_blocks(self):
        self.write("src/f.js", LICENSED + "const f = 1;\n")
        self.write("src/g.js", "const g = 1;\n")
        self.commit("base")
        self._git("branch", "pr")
        self.write("src/g.js", "const g = 2;\n")
        self.commit("main moves on")
        self._git("checkout", "-q", "pr")
        self.write("src/f.js", "const f = 2;\n")
        self.commit("drop the header")
        code, report, _err = self.gate("main", "pr")
        self.assertEqual(code, 1, report)
        self.assertIn("removed or altered", report)
        self.assertIn("src/f.js", report)

    def test_a_file_changed_on_both_branches_is_judged_only_on_the_authors_change(self):
        """Both sides touch one file, and both touch its HEADER - a licence-shaped
        change on each end, or the two-dot diff produces nothing check B cares about
        and the test proves nothing."""
        self.write("src/shared.js", LICENSED + "const x = 1;\n")
        self.commit("base")
        self._git("branch", "pr")
        self.write("src/shared.js",
                   LICENSED.replace(" */", " * Copyright (c) 2021 Later Corp\n */")
                   + "const x = 1;\n")
        self.commit("main records a second holder")
        self._git("checkout", "-q", "pr")
        self.write("src/shared.js",
                   LICENSED.replace(" */", " * Modified by the Example project.\n */")
                   + "const x = 2;\n")
        self.commit("edit, with the notice")
        code, report, _err = self.gate("main", "pr")
        self.assertNotIn("Later Corp", report,
                         "a line the base branch added is not a line the author removed")
        self.assertEqual(code, 0, report)

    def test_an_uncomputable_merge_base_blocks_rather_than_reporting_a_clean_tree(self):
        """A shallow checkout - `actions/checkout` defaults to depth 1 - leaves the two
        commits with no common history, and so does a genuinely unrelated branch. git
        says so by exiting 1 with NOTHING on stderr, which is indistinguishable from
        "they have no common ancestor, carry on" unless someone looks. Empty output
        here would mean an empty diff, every check finding nothing, and a report
        certifying a tree it never read."""
        self.write("src/a.js", LICENSED + "const a = 1;\n")
        self.commit("base")
        self._git("checkout", "-q", "--orphan", "unrelated")
        self._git("rm", "-rq", "--cached", ".")
        self.write("src/b.js", "const b = 1;\n")
        self.commit("an unrelated history")
        self.assertNotEqual(self._git("rev-parse", "--verify", "unrelated").returncode, 1,
                            "the orphan branch must actually carry a commit")
        self.assertNotEqual(self._git("merge-base", "main", "unrelated").returncode, 0,
                            "these histories must genuinely have no merge-base")

        code, report, err = self.gate("main", "unrelated")
        self.assertEqual(code, 2, report + err)
        self.assertIn("could not run", report)
        self.assertIn("merge-base", report)
        self.assertIn("depth", report, "name the likely cause; the operator has to act")
        self.assertNotIn("Verified automatically", report)
        self.assertNotIn("Nothing to do", report)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
