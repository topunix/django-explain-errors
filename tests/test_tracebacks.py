import os
import sysconfig

import django
from django.test import SimpleTestCase

from explain_errors.tracebacks import _is_library_frame, format_traceback


def _raise_nested():
    def inner():
        raise ValueError("boom")

    inner()


def _bad_json():
    import json

    json.loads("{not valid json")


class FrameClassificationTest(SimpleTestCase):
    def test_site_packages_is_library(self):
        self.assertTrue(_is_library_frame("/usr/lib/python3.11/site-packages/requests/api.py"))

    def test_dist_packages_is_library(self):
        self.assertTrue(_is_library_frame("/usr/lib/python3/dist-packages/foo/bar.py"))

    def test_stdlib_is_library(self):
        stdlib = sysconfig.get_paths()["stdlib"]
        filename = os.path.join(stdlib, "json", "__init__.py")
        self.assertTrue(_is_library_frame(filename))

    def test_django_package_is_library(self):
        django_dir = os.path.dirname(django.__file__)
        filename = os.path.join(django_dir, "db", "models", "base.py")
        self.assertTrue(_is_library_frame(filename))

    def test_plain_project_path_is_application(self):
        self.assertFalse(_is_library_frame("/home/user/myproject/myapp/views.py"))

    def test_site_packages_inside_project_is_still_library(self):
        filename = "/proj/.venv/lib/python3.11/site-packages/somepkg/x.py"
        self.assertTrue(_is_library_frame(filename))


class FormatTracebackTest(SimpleTestCase):
    def test_application_frames_are_kept(self):
        try:
            _raise_nested()
        except ValueError as exc:
            result = format_traceback(exc, max_chars=10_000)

        self.assertIn("_raise_nested", result)
        self.assertIn("inner", result)
        self.assertIn(__file__, result)

    def test_exception_header_always_present(self):
        try:
            _raise_nested()
        except ValueError as exc:
            result = format_traceback(exc, max_chars=10_000)

        self.assertIn("ValueError: boom", result)

    def test_budget_drops_library_frames_but_keeps_application_frames(self):
        try:
            _bad_json()
        except Exception as exc:
            full = format_traceback(exc, max_chars=10_000)
            trimmed = format_traceback(exc, max_chars=600)

        self.assertIn(__file__, trimmed)
        self.assertIn("_bad_json", trimmed)
        self.assertIn("library frames omitted", trimmed)
        self.assertLess(len(trimmed), len(full))

    def test_over_budget_falls_back_to_tail_slice(self):
        try:
            raise ValueError("x" * 5000)
        except ValueError as exc:
            result = format_traceback(exc, max_chars=200)

        self.assertTrue(result.startswith("...(truncated)...\n"))
        self.assertLessEqual(len(result), 200 + len("...(truncated)...\n"))

    def test_none_traceback_falls_back_to_tail_slice(self):
        exc = ValueError("standalone, never raised")
        self.assertIsNone(exc.__traceback__)

        try:
            raise RuntimeError("ambient exception for format_exc()")
        except RuntimeError:
            result = format_traceback(exc, max_chars=50)

        self.assertIn("ambient exception for format_exc()", result)

    def test_header_line_present_on_every_path(self):
        # Over-budget fallback path: max_chars is too small even for the
        # application frame alone, but the header ("ValueError: boom") is
        # short enough that the tail-slice still reaches back to it.
        try:
            _raise_nested()
        except ValueError as exc:
            fallback_result = format_traceback(exc, max_chars=50)
        self.assertTrue(fallback_result.startswith("...(truncated)...\n"))
        self.assertIn("ValueError: boom", fallback_result)

        # None-traceback fallback path.
        try:
            raise RuntimeError("ambient")
        except RuntimeError:
            none_tb_result = format_traceback(ValueError("detached"), max_chars=50)
        self.assertIn("RuntimeError", none_tb_result)

        # Normal path, budget large enough for everything.
        try:
            _raise_nested()
        except ValueError as exc:
            normal_result = format_traceback(exc, max_chars=10_000)
        self.assertIn("ValueError: boom", normal_result)
