from django.test import SimpleTestCase, override_settings

from explain_errors.sanitize import sanitize_traceback


class SanitizeDefaultPatternsTest(SimpleTestCase):

    def test_secret_assignment_redacted(self):
        tb = 'File "x.py", line 1\npassword="hunter2" was used'
        result = sanitize_traceback(tb)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("hunter2", result)

    def test_api_key_assignment_redacted(self):
        tb = "api_key: sk-abc123XYZ in config"
        result = sanitize_traceback(tb)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("sk-abc123XYZ", result)

    def test_bearer_token_redacted(self):
        tb = "Authorization: Bearer abc.def.ghi123"
        result = sanitize_traceback(tb)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("abc.def.ghi123", result)

    def test_email_redacted(self):
        tb = "raised for user someone@example.com during checkout"
        result = sanitize_traceback(tb)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("someone@example.com", result)

    def test_non_sensitive_text_unchanged(self):
        tb = 'Traceback (most recent call last):\n  File "views.py", line 10, in view\nValueError: bad input'
        self.assertEqual(sanitize_traceback(tb), tb)


class SanitizeSettingsTest(SimpleTestCase):

    @override_settings(EXPLAIN_ERRORS_REDACT_PATTERNS=[r"CUSTOM-\d+"])
    def test_custom_pattern_applied(self):
        tb = "order id CUSTOM-4821 failed"
        result = sanitize_traceback(tb)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("CUSTOM-4821", result)

    @override_settings(EXPLAIN_ERRORS_REDACT_DISABLE_DEFAULTS=True)
    def test_disable_defaults_skips_default_patterns(self):
        tb = "password=hunter2"
        result = sanitize_traceback(tb)
        self.assertEqual(result, tb)

    @override_settings(
        EXPLAIN_ERRORS_REDACT_DISABLE_DEFAULTS=True,
        EXPLAIN_ERRORS_REDACT_PATTERNS=[r"CUSTOM-\d+"],
    )
    def test_disable_defaults_still_applies_custom_patterns(self):
        tb = "password=hunter2 order CUSTOM-4821"
        result = sanitize_traceback(tb)
        self.assertIn("password=hunter2", result)
        self.assertNotIn("CUSTOM-4821", result)

    @override_settings(EXPLAIN_ERRORS_REDACT_REPLACEMENT="***")
    def test_custom_replacement_token(self):
        tb = "password=hunter2"
        result = sanitize_traceback(tb)
        self.assertIn("***", result)
        self.assertNotIn("[REDACTED]", result)

    @override_settings(EXPLAIN_ERRORS_REDACT_PATTERNS=["(unclosed"])
    def test_invalid_custom_regex_is_skipped_without_raising(self):
        tb = "password=hunter2"
        result = sanitize_traceback(tb)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("hunter2", result)
