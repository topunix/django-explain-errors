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


class SanitizeExtendedPatternsTest(SimpleTestCase):

    def assertRedacted(self, text, secret):
        result = sanitize_traceback(text)
        self.assertIn("[REDACTED]", result)
        self.assertNotIn(secret, result)

    def test_secret_key_with_trailing_name_redacted(self):
        self.assertRedacted('SECRET_KEY = "django-insecure-abc123"', "abc123")

    def test_aws_secret_access_key_redacted(self):
        self.assertRedacted('AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI"', "wJalrXUtnFEMI")

    def test_camel_case_and_hyphenated_names_redacted(self):
        self.assertRedacted('apiKeyId = "abcd1234"', "abcd1234")
        self.assertRedacted("x-auth-token: abcd1234", "abcd1234")

    def test_quoted_dict_key_redacted(self):
        self.assertRedacted("'PASSWORD': 'hunter2',", "hunter2")

    def test_quoted_value_with_spaces_redacted_whole(self):
        result = sanitize_traceback('password = "my pass phrase"')
        self.assertEqual(result, "[REDACTED]")

    def test_single_quoted_value_with_spaces_redacted_whole(self):
        self.assertEqual(sanitize_traceback("secret = 'two words'"), "[REDACTED]")

    def test_unclosed_quote_redacts_to_end_of_line(self):
        result = sanitize_traceback('password = "never closed\nnext line')
        self.assertNotIn("never closed", result)
        self.assertIn("next line", result)

    def test_url_credentials_redacted_keeping_scheme_and_host(self):
        result = sanitize_traceback("connect to postgres://alice:s3cret@db.example.com:5432/app")
        self.assertEqual(
            result, "connect to postgres://[REDACTED]@db.example.com:5432/app"
        )

    def test_url_credentials_without_dotted_host_redacted(self):
        result = sanitize_traceback('DATABASE_URL = "postgres://u:pw@host/db"')
        self.assertNotIn("u:pw", result)
        self.assertIn("@host/db", result)

    def test_url_without_credentials_unchanged(self):
        for text in (
            "see https://docs.djangoproject.com/en/5.2/",
            "GET https://example.com:8000/path/",
        ):
            with self.subTest(text=text):
                self.assertEqual(sanitize_traceback(text), text)

    def test_key_shapes_redacted_regardless_of_variable_name(self):
        shapes = [
            "sk-abcdefghijklmnopqrstuvwxyz012345",
            "sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345",
            "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
            "sk_live_abcdefghijklmnop1234",
            "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "github_pat_abcdefghijklmnopqrstuvwxyz0123",
            "xoxb-1234567890-abcdefghij",
            "AKIAABCDEFGHIJKLMNOP",
            "AIza" + "a" * 35,
        ]
        for shape in shapes:
            with self.subTest(shape=shape):
                self.assertRedacted(f'key = "{shape}"', shape)
                self.assertRedacted(f"called with {shape} here", shape)


class SanitizeNoOverRedactionTest(SimpleTestCase):

    def assertUnchanged(self, text):
        self.assertEqual(sanitize_traceback(text), text)

    def test_bare_key_name_left_alone(self):
        self.assertUnchanged('key = "abc"')
        self.assertUnchanged("for key, value in items:")

    def test_dict_keys_left_alone(self):
        self.assertUnchanged("KeyError: 'key'")
        self.assertUnchanged('data["key"] = value')
        self.assertUnchanged("{'key': 1, 'other': 2}")

    def test_cache_key_call_left_alone(self):
        self.assertUnchanged("cache_key = make_key(x)")

    def test_names_that_only_start_with_a_keyword_left_alone(self):
        self.assertUnchanged("author = Author.objects.get(pk=1)")
        self.assertUnchanged("authors = []")
        self.assertUnchanged("tokenizer = Tokenizer()")
        self.assertUnchanged("tokens = text.split()")

    def test_hyphenated_words_containing_sk_left_alone(self):
        self.assertUnchanged("risk-assessment-framework-document-name")
        self.assertUnchanged("task-queue-worker-configuration-long")


class SanitizeSafeSettingNamesTest(SimpleTestCase):

    SAFE_LINES = {
        "AUTH_USER_MODEL": 'AUTH_USER_MODEL = "accounts.User"',
        "AUTHENTICATION_BACKENDS": 'AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]',
        "AUTH_PASSWORD_VALIDATORS": "AUTH_PASSWORD_VALIDATORS = [",
        "PASSWORD_HASHERS": 'PASSWORD_HASHERS = ["django.contrib.auth.hashers.Argon2PasswordHasher"]',
        "PASSWORD_RESET_TIMEOUT": "PASSWORD_RESET_TIMEOUT = 3600",
    }

    def test_allowlisted_settings_left_alone(self):
        for name, line in self.SAFE_LINES.items():
            with self.subTest(name=name):
                self.assertEqual(sanitize_traceback(line), line)

    def test_allowlisted_names_left_alone_in_other_forms(self):
        for text in (
            "settings.PASSWORD_HASHERS = []",
            "'AUTH_USER_MODEL': 'accounts.User',",
            "AttributeError: 'Settings' has no AUTH_PASSWORD_VALIDATORS",
        ):
            with self.subTest(text=text):
                self.assertEqual(sanitize_traceback(text), text)

    def test_secrets_next_to_allowlisted_names_still_redacted(self):
        result = sanitize_traceback('PASSWORD_HASHERS = []; password = "hunter2"')
        self.assertIn("PASSWORD_HASHERS = []", result)
        self.assertNotIn("hunter2", result)

    def test_allowlist_is_exact_names_only(self):
        for text in ('AUTH_USER_MODEL_X = "abc123"', 'MY_PASSWORD_HASHERS = "abc123"'):
            with self.subTest(text=text):
                self.assertNotIn("abc123", sanitize_traceback(text))

    def test_secret_key_and_quoted_password_still_redacted(self):
        self.assertEqual(sanitize_traceback('SECRET_KEY = "abc123"'), "[REDACTED]")
        result = sanitize_traceback("'PASSWORD': 'x',")
        self.assertIn("[REDACTED]", result)
        self.assertNotIn("'x'", result)

    def test_token_count_style_names_still_redacted(self):
        self.assertIn("[REDACTED]", sanitize_traceback("token_count = 5"))


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
