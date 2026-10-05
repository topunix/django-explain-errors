# Eval methodology notes

**The truncation caveat, resolved.** Earlier versions of this section
carried an unresolved caveat, twice, instead of settling it: before the
`app-frame-preserving truncation` fix, `OPENAI_MAX_TRACEBACK_CHARS` kept
only the tail of the raw traceback, which for a Django ORM stack
routinely dropped the application frames and left only library
internals. Part of RAG-off's `points_to_fix_location` disadvantage was
never about missing source access. RAG-off was never shown the frame
naming the failing function at all. After the fix, RAG-off's
`points_to_fix_location` in group A rose from 6 of 30 to the 13 in the README.md Results table,
while RAG-on held roughly steady, around 21 to 26. Roughly half of the
old location gap was the trim bug; the rest is real.

**The no_fabrication arc.** `no_fabrication` wasn't part of the judge
prompt from the start. It was added because the judge kept deciding
fabrication implicitly (by default, whenever the other four questions
tied), which measured nothing. It then failed twice before it worked: a
judge given only the traceback and the known-good facts defaulted to
calling any specific-but-unfamiliar detail invented, since it had nothing
to check it against; a judge later given whole source modules ignored
them anyway, because answering "unverified" is cheaper than reading 300
mostly-irrelevant lines. Neither version was actually measuring
fabrication. Only forcing a per-claim verdict (verified / contradicted /
absent, checked against a small, relevant source excerpt), with
`no_fabrication` *derived* from those verdicts in the parser rather than
asked as a direct yes/no, made the question measure what its name
claimed. The numbers inverted once that landed: RAG-on went from
*appearing* to fabricate more, an artifact of the judge having nothing to
check its source-grounded details against, to measurably fabricating
less, as the Results table in README.md shows.

**A source-extraction bug affecting `str_recursion`, now fixed.**
`_extract_function_source` used to pick a function's source by name alone
(the first `def <name>` it found walking the module, full stop). For
`str_recursion`, whose `RecursionError` alternates between
`Comment.__str__` and `Comment.summary` on every frame, which one lands
as the innermost project frame depends on how deep the call stack already
was when the recursion started (Django's own dispatch and template
rendering depth), not on anything about the fixture itself. Whenever it
resolved to `__str__`, the name-only lookup returned `Author.__str__`
(the first `__str__` in `models.py`, textually, and an unrelated one-line
method) instead of `Comment.__str__`, which is where the actual
recursion lives. `_extract_function_source` now also takes the frame's
line number and matches the definition whose body contains it, so it
returns the method that actually ran regardless of which side of the
recursion the traceback happened to end on. Any past run where this fired
would have shown the judge a materially smaller, wrong-class excerpt for
`str_recursion` (one line instead of the four-line property plus
`__str__` that actually recurse) on both RAG-on and RAG-off sides
alike (this path is shared, not RAG-specific), on whatever fraction of
comparisons hit the unlucky parity. The Results numbers in README.md predate
this fix and were not re-run to quantify the effect; treat any
`str_recursion`-specific finding in them with that in mind. The fix
applies to every fixture, not just this one. Any two classes in
`blog/models.py` or `blog/views.py` that happen to share a method name
were equally exposed.

**Same fixture, a second gap: only half the loop.** Disambiguating by
class fixed which method came back, but not how much of the cycle. When
the frame resolves to `Comment.__str__`, the extracted source used to
stop at `return self.summary`. The judge was asked to verify claims
about a recursion loop while looking at only one side of it.
`_extract_function_source` now also pulls in, one level deep, any other
method on the same class that the matched method's body reaches through
an explicit `self.<name>` attribute access, so a `__str__`-anchored
extraction now includes `summary` too. This is not fully symmetric:
`Comment.summary` reaches back into `__str__` through `str(self)`, a
call, not a `self.__str__` attribute access, so the reverse case (frame
resolves to `summary`) still shows only `summary` alone. Whether that
residual gap is worth closing depends on how often that parity actually
occurs in practice (unmeasured, same as above).
