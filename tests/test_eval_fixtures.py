"""Plumbing tests for the eval harness's fixture registry (evals/fixtures.py).

Fully mocked / subprocess-isolated, same as the rest of the eval harness's
plumbing tests. Nothing here hits a real API; nothing here is part of the
harness itself.
"""
import json
import os
import subprocess
import sys
import unittest

from evals.fixtures import FIXTURES, Fixture

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FixtureRegistryTest(unittest.TestCase):

    def test_fifteen_fixtures_registered(self):
        self.assertEqual(len(FIXTURES), 15)

    def test_every_fixture_has_all_required_fields(self):
        for fixture in FIXTURES:
            self.assertTrue(fixture.name, fixture)
            self.assertTrue(fixture.url, fixture)
            self.assertIn(fixture.group, ("A", "B"), fixture)
            self.assertTrue(fixture.expected_exception, fixture)
            self.assertTrue(fixture.expected_cause, fixture)
            self.assertTrue(fixture.expected_fix_location, fixture)
            self.assertIn(fixture.method, ("GET", "POST"), fixture)

    def test_names_are_unique(self):
        names = [fixture.name for fixture in FIXTURES]
        self.assertEqual(len(names), len(set(names)))

    def test_group_split_matches_the_spec(self):
        group_a = [f for f in FIXTURES if f.group == "A"]
        group_b = [f for f in FIXTURES if f.group == "B"]
        self.assertEqual(len(group_a), 10)
        self.assertEqual(len(group_b), 5)

    def test_post_fixtures_carry_data(self):
        for fixture in FIXTURES:
            if fixture.method == "POST":
                self.assertIsInstance(fixture.data, dict, fixture)

    def test_invalid_group_is_rejected(self):
        with self.assertRaises(ValueError):
            Fixture(
                name="x",
                url="/x/",
                group="C",
                expected_exception="ValueError",
                expected_cause="c",
                expected_fix_location="x.py:f",
            )

    def test_invalid_method_is_rejected(self):
        with self.assertRaises(ValueError):
            Fixture(
                name="x",
                url="/x/",
                group="A",
                method="PUT",
                expected_exception="ValueError",
                expected_cause="c",
                expected_fix_location="x.py:f",
            )


class EvalFixtureUrlResolutionTest(unittest.TestCase):
    """Runs in a subprocess -- see tests/_eval_url_resolution_helper.py for
    why: resolving the fixture app's URLconf imports blog.models, which
    needs a Django app registry with 'blog' in INSTALLED_APPS, and this
    test suite's registry is already populated from test_settings.
    """

    def test_every_fixture_url_resolves_in_the_fixture_apps_urlconf(self):
        proc = subprocess.run(
            [sys.executable, "-m", "tests._eval_url_resolution_helper"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["failures"], [])
