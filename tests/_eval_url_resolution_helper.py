"""Subprocess helper for test_eval_fixtures.EvalFixtureUrlResolutionTest.

Importing the fixture app's URLconf imports blog.views, which imports
blog.models -- and defining a Django model requires an app registry that
has 'blog' in INSTALLED_APPS. The test suite process has already populated
its app registry from test_settings (INSTALLED_APPS = ["explain_errors"])
by the time any test runs, and Django populates its registry once per
process and ignores later attempts with different settings. So this has to
run somewhere Django hasn't been set up yet.
"""
import json
import os
import sys


def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fixture_app_dir = os.path.join(repo_root, "evals", "fixture_app")
    sys.path.insert(0, repo_root)
    sys.path.insert(0, fixture_app_dir)
    os.environ["DJANGO_SETTINGS_MODULE"] = "settings"

    import django

    django.setup()

    from django.urls import Resolver404, resolve

    from evals.fixtures import FIXTURES

    failures = []
    for fixture in FIXTURES:
        path = fixture.url.split("?", 1)[0]
        try:
            resolve(path)
        except Resolver404 as exc:
            failures.append(f"{fixture.name}: {path!r} did not resolve ({exc})")

    print(json.dumps({"failures": failures}))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
