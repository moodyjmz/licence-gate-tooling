#!/usr/bin/env python3
"""The suite is allowed to fail. It is not allowed to quietly get smaller.

THE FAILURE THIS EXISTS FOR, which ran for a release. Three test modules import
PyYAML to read the action definitions. Nothing installed it. Before the version
matrix the runner's stock `python3` happened to carry it; `actions/setup-python`
installs a clean interpreter and does not, so the matrix did not break those tests -
it removed the accident that had been keeping them green.

Fifty-nine tests then stopped running, and the way `unittest discover` reports that
is the dangerous part: a module it cannot import becomes a synthetic `_FailedTest`
INSIDE the run, so the summary read

    Ran 224 tests ... FAILED (errors=3)

221 real tests plus three stubs standing in for the fifty-nine that had vanished.
The total went UP as the suite shrank, and a reader skims three failures rather than
fifty-nine absences. Nothing stated an expected count to contradict them.

So the invariant is not a number - a floor needs maintaining and gets raised by
whoever is annoyed by it. It is: EVERY test module in this directory loads. A module
that does not is a module contributing zero tests, whatever the summary line says,
and it is named here with the reason.

This is the same rule the gate applies to itself. `git_io` exists because an empty
answer from git is never "nothing to report"; an empty test module is never "nothing
to test".
"""

import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class TestEveryTestModuleLoads(unittest.TestCase):

    def test_no_test_module_failed_to_import(self):
        """Discovery is run again, for its ERRORS rather than its tests.

        Loading does not execute anything: the modules are already imported by the
        run in progress, so this reads `loader.errors` and nothing else. A failure
        here names the module and quotes the ImportError, which is the sentence
        somebody can act on - "no module named yaml" tells you to install it, and
        three anonymous errors at the bottom of a 255-line summary do not.
        """
        loader = unittest.TestLoader()
        loader.discover(HERE, pattern="test_*.py")
        if loader.errors:
            self.fail(
                "{} test module(s) could not be imported and contributed NO tests to "
                "this run. The summary line counts one stub for each, so the total "
                "goes up while the coverage goes down.\n\n{}".format(
                    len(loader.errors), "\n\n".join(loader.errors)))

    def test_the_modules_that_read_the_action_definitions_are_among_them(self):
        """Named explicitly, because these are the ones that went dark.

        The check above is general and would catch it, but it says "some module did
        not load". These three cover the controls written in YAML - the layer a pull
        request reaches first - and a general message does not convey that losing
        them is different from losing a unit test.
        """
        import importlib
        for name in ("test_action_plumbing", "test_gate_action",
                     "test_patch_only_actions"):
            with self.subTest(module=name):
                try:
                    importlib.import_module(name)
                except ImportError as exc:
                    self.fail(
                        f"{name} did not import ({exc}). It executes the `run:` bodies "
                        f"out of the action definitions, which is the only thing that "
                        f"tests them at all; without it those controls are unexercised "
                        f"and the suite still reports a pass for everything else.")


if __name__ == "__main__":
    unittest.main()
