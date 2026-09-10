# Task: explain-errors-language

Add `EXPLAIN_ERRORS_LANGUAGE`, letting a developer read explanations in a language other
than English.

Full rationale is in `docs/roadmap.md` item 1. Read it before starting.

Branch from an updated `origin/main`. Do not commit unless asked.

## 1. The setting

`EXPLAIN_ERRORS_LANGUAGE`, default `None`, meaning English. Accepts a language name or code
as a plain string.

Do **not** inherit from Django's `LANGUAGE_CODE`. That is the language the site's audience
reads, not the language the developer reads, and the two diverge routinely. A Django project
serving Japanese users may well be maintained by someone who wants English explanations, or
the reverse.

Do **not** use `gettext` or translation catalogs. Django's i18n machinery translates a fixed,
known set of strings. Generated explanations are unbounded text, so catalogs are the wrong
tool. This is one clause added to the prompt.

## 2. Prompt changes

When the setting is `None`, the prompt must be byte-identical to what ships today. Adding an
"answer in English" clause when no language is configured would be a silent change to every
existing user's output.

When set, add a clause instructing the model to answer in the target language, and to keep
the following in English so they stay greppable and matchable against documentation:

- Exception type names (`ValueError`, `ImproperlyConfigured`)
- Django and Python identifiers, attribute names, and settings names
- Code, file paths, and tracebacks

Only the prose explaining the error gets translated.

## 3. Word budget scaling

`EXPLANATION_WORD_BUDGET` is currently one constant. Japanese, Korean, Arabic, Hindi, and
Thai tokenize far worse than English, so the same word budget consumes substantially more
tokens.

`DEFAULT_MAX_TOKENS` is 1000 against a 200-word budget, which is generous headroom and likely
absorbs most of the variance already. **Check whether scaling is needed before building it.**
Estimate tokens-per-word for a representative explanation in two or three of the languages
above, and report the numbers.

- If 1000 comfortably covers them, add nothing. Note the finding in your report so the
  roadmap can record that scaling was investigated and found unnecessary.
- If it does not, scale the ceiling per language, not the word budget. The word budget is
  what the prompt asks for and should stay constant across languages; the token ceiling is
  the safety net and is what needs to move.

Whatever you do here, the existing drift tests in `tests/test_truncation.py` must still hold.
If scaling makes `DEFAULT_MAX_TOKENS` per-language, the test asserting the ceiling covers the
budget has to assert it for every language, not just the default.

## 4. Tests

New file `tests/test_language.py`. `SimpleTestCase`, all API calls mocked, no network.

1. With `EXPLAIN_ERRORS_LANGUAGE` unset, the prompt is byte-identical to the current
   English-only prompt. This is the important one: it guards existing users against a
   silent output change.
2. With the setting configured, the language clause appears in the system prompt.
3. With the setting configured, the identifier-preservation instruction appears.
4. If you implement per-language token scaling, assert the ceiling covers the word budget for
   each configured language.

## 5. Constraints

- Invariant 10: with RAG disabled the user-message prompt must remain byte-identical to the
  traceback-only prompt. The language clause belongs in the system message, same as the word
  budget clause, so this should hold. Confirm it rather than assuming.
- All existing tests pass. Never delete or weaken one to make a change pass.
- Invariant 2: no behavior when `DEBUG=False`.
- Run: `DJANGO_SETTINGS_MODULE=test_settings python -m django test tests -v 2`

## 6. Documentation

README only, no positioning changes:

- A Configuration table row for `EXPLAIN_ERRORS_LANGUAGE`.
- A short section showing usage, and stating that identifiers and code stay in English.
- The known limitation: small local models behind `OPENAI_BASE_URL` degrade sharply outside
  English, so output quality does not transfer uniformly across providers. Say this plainly
  rather than burying it.

`setup.py`'s `description` is stale (see the roadmap loose end) and is the PyPI summary, so
it must change in a release commit rather than here. Do not touch it.

## 7. Report before finishing

- The tokens-per-word measurements, and whether scaling was needed.
- Confirmation that invariant 10 still holds.
- New test count.

## 8. On completion

Delete this file. Delete item 1 from `docs/roadmap.md` and renumber the queue. If scaling
turned out to be unnecessary, say so in your report so the roadmap's mention of per-language
budgets can be corrected separately.
