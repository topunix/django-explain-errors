# Task: README "Production Safety" section

## Context
django-explain-errors is dev-only middleware. When active, it sends exception tracebacks to an LLM provider (OpenAI-compatible endpoint). A misconfigured production `DEBUG = True` would ship source, file paths, and local values off-box. The README currently tells users to append the middleware to `MIDDLEWARE` unconditionally. This task documents the safe registration pattern plus a CI guard.

This ships as part of the README positioning rewrite, but lands on its own branch and commit (one concern per branch).

## Changes

### 1. README.md
Add a `## Production Safety` section immediately after the installation/configuration section (before Usage). Content:

- One short paragraph: the middleware is intended for local development only; when active it sends exception tracebacks to the configured LLM provider, which may include source code, file paths, and local variable values. Keep wording provider-neutral.
- Primary safeguard: register the middleware only when `DEBUG` is on, or only in development settings:

  ```python
  # settings.py
  if DEBUG:
      MIDDLEWARE.append("explain_errors.ExplainErrorsMiddleware")
  ```

  Note in one sentence that this keeps the middleware last in the list, as required.
- Secondary safeguard: run Django's deployment checks in CI against production settings, which fails the build if `DEBUG = True` (`security.W018`):

  ```bash
  python manage.py check --deploy --fail-level WARNING
  ```

Also update the existing installation step that shows the unconditional `MIDDLEWARE = [..., 'explain_errors.ExplainErrorsMiddleware']` example so it uses the conditional pattern. Do not leave two conflicting registration examples.

If `README.rst` is still used as `long_description` in packaging and duplicates README.md content, apply the same change there. If it is stale or unused, stop and ask rather than deciding.

### 2. docs/roadmap.md
Under the README positioning rewrite item, add a sub-item for the Production Safety section with a Verify marker that checks `main` for the `## Production Safety` heading in `README.md`.

## Constraints
- Documentation only. Do not modify `middleware.py` or any Python code. The known `__acall__` / DEBUG-guard ordering issue is a separate task; do not touch it.
- No em dashes anywhere in the README or roadmap. Use commas, parentheses, colons, or periods.
- Match existing README heading levels, code fence style, and tone.
- If any wording, placement, or scope question is a judgment call, stop and ask. Do not decide and present it after the fact.

## Commit
- Single commit, message: `docs: add Production Safety section to README`
- No session URLs, no attribution lines, no co-author trailers in the commit message or PR body.
- Do not push or open a PR until reviewed.

## After merge
Delete `docs/tasks/production-safety-docs.md` in a follow-up commit.
