#!/usr/bin/env python3
"""Tests for the licence decision logic.

Deliberately stdlib-only so they run anywhere with no install step.

Each test names the real failure it guards against. A test whose purpose is not
obvious gets deleted by someone six months from now who cannot see why it matters.
"""

import contextlib
import io
import unittest

import importlib.util
import pathlib

from licence_map import (APPLY, REFUSE, classify, has_licence_header, header_block,
                         header_texts, insert_header, leading_comment_region,
                         licence_for)

# The gate is not importable by name - the filename has a hyphen - so load it by path.
_spec = importlib.util.spec_from_file_location(
    "licence_gate", pathlib.Path(__file__).with_name("licence-gate.py"))
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


class TestLicenceForPath(unittest.TestCase):
    def test_default_applies_to_ordinary_paths(self):
        self.assertEqual(licence_for("src/widget.js"), "Example-1.0")

    def test_subtree_overrides_the_default(self):
        """The real case: upstream licenses an example tree permissively while the
        product is copyleft. A file added there must not inherit the repo default."""
        self.assertEqual(licence_for("src/permissive/page.html"), "Permissive-2.0")

    def test_longest_prefix_wins(self):
        self.assertEqual(licence_for("src/permissive/deep/nested/x.js"), "Permissive-2.0")

    def test_vendored_paths_are_undetermined_not_defaulted(self):
        """Guessing here is how third-party code acquires your licence."""
        self.assertIsNone(licence_for("vendor/lib/thing.js"))
        self.assertIsNone(licence_for("third_party/x.c"))


class TestClassify(unittest.TestCase):
    def test_source_file_gets_the_repo_default_when_named(self):
        action, lic, _ = classify("src/parser.js", explicitly_named=True, has_header=False)
        self.assertEqual((action, lic), (APPLY, "Example-1.0"))

    def test_source_file_is_refused_when_not_named(self):
        """The attack that killed the old rule: third-party code in a .js file, with
        a comment saying so, stamped as ours by a blanket command that named nothing.
        Source files were exempt from naming because contributors "usually" write
        their own - true on average, worthless as a safeguard."""
        action, _, reason = classify("src/parser.js",
                                     explicitly_named=False, has_header=False)
        self.assertEqual(action, REFUSE)
        self.assertIn("must name the file", reason)

    def test_source_file_in_permissive_subtree_gets_that_licence(self):
        """A single pull request can add files under two different licences; the
        decision is per path, never per pull request."""
        action, lic, _ = classify("src/permissive/banner.html",
                                  explicitly_named=True, has_header=False)
        self.assertEqual((action, lic), (APPLY, "Permissive-2.0"))

    def test_asset_is_refused_unless_named(self):
        """The real failure: two icons named as first-party work were unmodified
        third-party icons. Blanket-stamping assets asserts copyright over them."""
        action, _, reason = classify("assets/icon.svg",
                                     explicitly_named=False, has_header=False)
        self.assertEqual(action, REFUSE)
        self.assertIn("must name the file", reason)

    def test_asset_is_applied_when_named(self):
        action, lic, _ = classify("assets/icon.svg",
                                  explicitly_named=True, has_header=False)
        self.assertEqual((action, lic), (APPLY, "Example-1.0"))

    def test_unmapped_path_is_refused_even_when_named(self):
        """An explicit assertion of authorship does not make the licence knowable."""
        action, _, reason = classify("vendor/lib.js",
                                     explicitly_named=True, has_header=False)
        self.assertEqual(action, REFUSE)
        self.assertIn("by hand", reason)

    def test_existing_header_is_never_overwritten(self):
        action, _, reason = classify("src/widget.js",
                                     explicitly_named=True, has_header=True)
        self.assertEqual(action, REFUSE)
        self.assertIn("existing claim", reason)

    def test_unknown_file_type_is_refused(self):
        """README, config and data files mostly need no header; decide by hand
        rather than littering."""
        action, _, _ = classify("docs/notes.md", explicitly_named=True, has_header=False)
        self.assertEqual(action, REFUSE)


class TestMixedPullRequest(unittest.TestCase):
    def test_a_single_pull_request_resolves_each_file_independently(self):
        """The whole point: one pull request, several answers, no human needing to
        know which subtree is which."""
        files = [
            ("src/parser.js", True, False),
            ("src/permissive/banner.html", True, False),
            ("assets/spinner.svg", True, False),
            ("vendor/lib.js", True, False),
        ]
        got = [(p, classify(p, n, h)[0], classify(p, n, h)[1]) for p, n, h in files]
        self.assertEqual(got, [
            ("src/parser.js", APPLY, "Example-1.0"),
            ("src/permissive/banner.html", APPLY, "Permissive-2.0"),
            ("assets/spinner.svg", APPLY, "Example-1.0"),
            ("vendor/lib.js", REFUSE, None),
        ])


class TestHeaderFormatting(unittest.TestCase):
    """The comment syntax was previously produced by splitting a "// ..." string on its
    first space and keeping the remainder. It worked, but any edit to the header text
    would have broken block comments silently - and a malformed comment in a .css or
    .svg file is a syntax error in someone else's build, not ours."""

    def test_line_comment_styles(self):
        for path in ("src/a.js", "src/a.go", "src/a.py"):
            with self.subTest(path=path):
                block = header_block(path, "Example-1.0")
                self.assertTrue(all(l.startswith(("//", "#")) for l in block), block)
                self.assertEqual(len(block), 2)

    def test_block_comment_is_opened_and_closed(self):
        block = header_block("src/a.css", "Example-1.0")
        self.assertEqual(block[0], "/*")
        self.assertEqual(block[-1], " */")
        self.assertIn("SPDX-License-Identifier: Example-1.0", "\n".join(block))

    def test_markup_comment_is_opened_and_closed(self):
        block = header_block("assets/a.svg", "Example-1.0")
        self.assertEqual(block[0], "<!--")
        self.assertEqual(block[-1], "-->")
        self.assertNotIn("//", "\n".join(block))

    def test_unknown_extension_falls_back_to_hash(self):
        self.assertTrue(header_block("thing.conf", "Example-1.0")[0].startswith("# "))


class TestInsertionPoint(unittest.TestCase):
    """Prepending unconditionally is the obvious implementation and it is wrong for
    two file types this tool actually handles."""

    def test_shebang_stays_on_line_one(self):
        """A header above the shebang stops the script executing - and the diff looks
        perfectly correct, so nobody spots it until something fails to run."""
        out = insert_header("#!/bin/sh\necho hi\n", "scripts/x.sh", "Example-1.0")
        self.assertTrue(out.startswith("#!/bin/sh\n"))
        self.assertIn("SPDX-License-Identifier: Example-1.0", out)

    def test_xml_declaration_stays_on_line_one(self):
        """An XML declaration must be the first thing in the document; a comment above
        it makes the SVG invalid."""
        out = insert_header('<?xml version="1.0"?>\n<svg/>\n', "a.svg", "Example-1.0")
        self.assertTrue(out.startswith('<?xml version="1.0"?>\n'))
        self.assertIn("<!--", out)

    def test_ordinary_file_gets_the_header_first(self):
        out = insert_header("const a = 1;\n", "src/a.js", "Example-1.0")
        self.assertTrue(out.startswith("// SPDX-FileCopyrightText:"))

    def test_original_content_is_never_lost(self):
        for content, path in [("#!/usr/bin/env python3\nx = 1\n", "s.py"),
                              ("body { color: red }\n", "a.css"),
                              ("<svg/>\n", "a.svg")]:
            with self.subTest(path=path):
                out = insert_header(content, path, "Example-1.0")
                for line in content.strip().split("\n"):
                    self.assertIn(line, out)


class TestLeadingCommentRegion(unittest.TestCase):
    """Guards the defeat of the old fixed 40-line window."""

    def test_header_below_forty_lines_is_still_found(self):
        """The live attack: 45 lines of filler comment above the header. The gate saw
        no header, demanded no modification notice, and told the reviewer in its
        verified box that every modified file with a header carried one."""
        content = "\n".join(["// filler"] * 45
                            + ["/*", " * Copyright (c) 2020 Example Corp", " */",
                               "const x = 1;"])
        self.assertIn("Copyright (c) 2020", leading_comment_region(content))

    def test_prose_below_the_code_is_not_part_of_the_region(self):
        """Why the whole file is not scanned. A licence quoted in prose partway down a
        file is not that file's header, and treating it as one produces notice demands
        on files that need none - which is how people learn to click past the gate."""
        content = "const x = 1;\n" + "\n".join(["// Copyright (c) 1999 Someone"] * 5)
        self.assertNotIn("Copyright", leading_comment_region(content))

    def test_region_stops_at_the_first_code_line(self):
        content = "// header\nconst a = 1;\n// Copyright (c) 2020 Elsewhere"
        region = leading_comment_region(content)
        self.assertIn("header", region)
        self.assertNotIn("Copyright", region)

    def test_shebang_does_not_end_the_region(self):
        content = "#!/usr/bin/env python3\n# Copyright (c) 2020 Example Corp\nx = 1"
        self.assertIn("Copyright", leading_comment_region(content))

    def test_file_with_no_leading_comment_has_an_empty_region(self):
        self.assertEqual(leading_comment_region("const a = 1;").strip(), "")


class TestAssetDetection(unittest.TestCase):
    """An extension allow-list fails by omission every time somebody invents a format."""

    def test_formats_missing_from_the_old_list_are_assets(self):
        """.otf and .webp were absent, so a font swap and an image swap were neither
        asset nor source. They fell through every check and the acknowledgement gate
        reported 'No replacement candidates detected' on two third-party binaries."""
        for path in ("assets/brand.otf", "assets/hero.webp",
                     "assets/x.avif", "assets/y.eot"):
            with self.subTest(path=path):
                self.assertTrue(gate.is_asset(path))

    def test_source_and_text_are_not_assets(self):
        for path in ("src/a.js", "src/b.py", "README.md", "config.yaml", "LICENSE"):
            with self.subTest(path=path):
                self.assertFalse(gate.is_asset(path))

    def test_an_unheard_of_extension_is_treated_as_an_asset(self):
        """Over-detection is the safe direction: a reviewer dismisses a spurious
        candidate in seconds, and nobody ever notices a missing one."""
        self.assertTrue(gate.is_asset("assets/thing.qoi"))


class TestRenameLaundering(unittest.TestCase):
    def test_rename_into_an_ignored_path_does_not_launder_the_change(self):
        """The live attack: git mv src/panel.js docs/panel.js in the same commit that
        rewrote the copyright holder. The diff hunk is labelled +++ b/docs/panel.js,
        so taking the path from that alone skipped the removed line as documentation.
        The gate passed and stated that no copyright line had been altered."""
        self.assertFalse(gate.both_ends_ignorable("src/panel.js", "docs/panel.js"))

    def test_rename_out_of_an_ignored_path_is_also_watched(self):
        self.assertFalse(gate.both_ends_ignorable("docs/panel.js", "src/panel.js"))

    def test_change_wholly_inside_ignored_paths_is_ignored(self):
        self.assertTrue(gate.both_ends_ignorable("docs/a.md", "docs/b.md"))
        self.assertTrue(gate.both_ends_ignorable("scripts/x.py", "scripts/x.py"))

    def test_ordinary_edit_is_watched(self):
        self.assertFalse(gate.both_ends_ignorable("src/a.js", "src/a.js"))


class TestOneHeaderPredicate(unittest.TestCase):
    """There were three implementations of "has this file got a header" and they
    disagreed. The gate looked for `Copyright (c)`; the stamping tool wrote SPDX tags;
    a third check searched for the literal string SPDX-License-Identifier in the first
    fifteen lines."""

    def test_the_gate_recognises_the_headers_we_ourselves_write(self):
        """The consequence of the disagreement: a file stamped by our own tool matched
        none of the gate's patterns, so it was invisible to both blocking checks
        forever after. Its licence could then be deleted outright and the report would
        certify that nothing had been altered."""
        for text in header_texts("Example-1.0"):
            with self.subTest(text=text):
                self.assertTrue(has_licence_header(text))

    def test_conventional_headers_still_recognised(self):
        self.assertTrue(has_licence_header(" * Copyright (c) 2020 Example Corp"))
        self.assertTrue(has_licence_header(" * Licensed under the Example License"))

    def test_prose_about_licensing_is_not_a_header(self):
        """The regex was narrowed once because a looser one flagged the gate's own
        source. That narrowing must survive."""
        self.assertFalse(has_licence_header(
            "This document explains our copyright and licensing policy."))


class TestHeaderCorpus(unittest.TestCase):
    """The stated, testable scope of HEADER_RE. This list IS the specification.

    The pattern used to require the literal word "copyright" before a (c)/©/year, so
    `© 2020 Example Corp` - the commonest European form - matched nothing at all.
    Deleting such a line passed the gate, and the report certified under "you need not
    check these" that no copyright line had been altered.

    An open-ended regex nobody can state the scope of is how that happened and how it
    would happen again. HEADERS is what must match; PROSE is the boundary, and it
    exists because the opposite failure is just as fatal in practice: a looser pattern
    flagged the gate's own documentation, and a gate that fires on prose is a gate
    people learn to click past. Add a case here before changing the pattern.
    """

    HEADERS = [
        # Plain copyright lines, with and without the word.
        " * Copyright (c) 2020 Example Corp",
        " * Copyright (C) 2007 Free Software Foundation, Inc.",
        "# Copyright 2020 Example Corp",
        "// Copyright 2020-2024 Example Corp. All rights reserved.",
        "# Copyright by Example Corp",
        # The three verified live misses that prompted this.
        " * (c) 2020 Example Corp",
        " * (C) 2020 Example Corp, all rights reserved",
        " * © 2020 Example Corp",
        "© Example Corp",
        # SPDX - including exactly what our own stamping tool writes.
        "// SPDX-FileCopyrightText: 2026 Example project contributors",
        "// SPDX-License-Identifier: Example-1.0",
        # Licence grants. "the" is optional: "Licensed under MIT" is ordinary.
        " * Licensed under the Apache License, Version 2.0 (the \"License\");",
        "# Licensed under MIT",
        " * Licenced under the Example License",
        " * All rights reserved.",
        # Licence bodies, matched on a short leading fragment - see HEADER_RE on why
        # a phrase long enough to wrap across lines is useless here.
        " * Permission is hereby granted, free of charge, to any person obtaining",
        " * Redistribution and use in source and binary forms, with or without",
        " * This program is free software: you can redistribute it and/or modify",
        " * it under the terms of the GNU Affero General Public License as published",
        " * GNU Lesser General Public License for more details.",
        # MPL 2.0's standard header, found live by a red-team run: it names no
        # copyright, no year, no (c) and no SPDX tag, so every line of it was
        # invisible. Deleting the whole block passed with the report stating that no
        # licence line had been altered. It is one of the most widely used headers
        # there is, which is the point - the space this pattern covers is open, and
        # only this list makes its edges visible.
        " * This Source Code Form is subject to the terms of the Mozilla Public",
        " * License, v. 2.0. If a copy of the MPL was not distributed with this",
        " * file, You can obtain one at https://mozilla.org/MPL/2.0/.",
        # Same run: a copyright with no year at all, and the circled form.
        "# Copyright Example Corp",
        " * Ⓒ 2020 Example Corp",
        " * Proprietary and confidential.",
    ]

    PROSE = [
        # The original constraint: a looser pattern made the gate flag its own source.
        "This document explains our copyright and licensing policy.",
        "See the licensing section for details.",
        "Ask legal about copyright questions before vendoring anything.",
        "The copyright of each contribution remains with its author.",
        "The copyright holder must be recorded in the register.",
        "Licensing is handled by the compliance team.",
        # A bare (c) is a list marker far more often than a copyright sign.
        "(c) is the third option in the list below.",
        "Options: (a) keep, (b) drop, (c) defer.",
        # Ordinary code and comments in a repository that is ABOUT licensing.
        "// returns the licence for this path, or None",
        "def licence_for(path):",
        "This function checks whether a file has a header.",
        "# TODO: work out which licence applies here",
    ]

    def test_genuine_header_styles_all_match(self):
        for text in self.HEADERS:
            with self.subTest(text=text):
                self.assertTrue(has_licence_header(text))

    def test_prose_and_code_do_not_match(self):
        for text in self.PROSE:
            with self.subTest(text=text):
                self.assertFalse(has_licence_header(text))


class TestHeaderCannotBePushedOutOfView(unittest.TestCase):
    def test_one_prepended_code_line_does_not_hide_a_header(self):
        """Cheaper than the 45-line filler attack and aimed at the same check: put a
        single line of code above an existing header and the leading comment region
        ends before it, so the gate sees no header and demands no notice. Handled by
        asking the BASE whether the file had a header, not just the head."""
        content = "const pre = 0;\n/*\n * Copyright (c) 2020 Example Corp\n */\n"
        self.assertEqual(leading_comment_region(content).strip(), "")
        self.assertFalse(has_licence_header(leading_comment_region(content)))
        # The header is still in the file; only its position changed.
        self.assertTrue(has_licence_header(content))


class TestNoFailureIsSilent(unittest.TestCase):
    """The top-level handler named GateError and UnicodeDecodeError. Anything else
    escaped as a traceback: CI went red, which is correct, but the review comment is
    built from stdout and nothing had been printed before the crash - so the comment
    body was EMPTY, which reads exactly like "ran, found nothing".

    An unanticipated exception is by definition the one the named list does not cover,
    which is why the list was the wrong shape rather than the wrong length. The only
    honest way to test "an exception type nobody has thought of yet" is to inject one,
    which is what the `_main` seam is for."""

    def _run(self, boom, argv=("a", "b")):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = gate.run(list(argv), _main=lambda _argv: boom())
        return code, out.getvalue(), err.getvalue()

    def test_an_unanticipated_exception_still_produces_a_report(self):
        def boom():
            raise ZeroDivisionError("division by zero")
        code, out, _ = self._run(boom)
        self.assertEqual(code, 2)
        self.assertIn("could not run", out)
        self.assertIn("ZeroDivisionError", out, "say what happened, not just that it did")
        self.assertNotIn("Verified automatically", out)

    def test_the_report_goes_to_stderr_under_fix(self):
        """Under --fix stdout is a list of paths the calling action COMMITS. A markdown
        heading is not a filename."""
        def boom():
            raise RuntimeError("nope")
        code, out, err = self._run(boom, argv=("--fix", "a", "b"))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("could not run", err)

    def test_a_clean_exit_is_not_swallowed(self):
        """`Exception`, not `BaseException`. main() returning through sys.exit raises
        SystemExit, and catching that would turn every ordinary run into a crash
        report - the loudest possible way to break the thing being fixed."""
        def boom():
            raise SystemExit(0)
        with self.assertRaises(SystemExit):
            self._run(boom)

    def test_an_ordinary_gate_error_is_unchanged(self):
        def boom():
            raise gate.GateError("unreachable base")
        code, out, _ = self._run(boom)
        self.assertEqual(code, 2)
        self.assertIn("unreachable base", out)
        self.assertNotIn("unexpected", out, "a named failure keeps its own wording")


if __name__ == "__main__":
    unittest.main(verbosity=2)
