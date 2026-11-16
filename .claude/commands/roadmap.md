---
description: Implement roadmap items unattended, one PR per item
argument-hint: <item names or queue numbers, comma separated>
---
Implement these roadmap items from docs/roadmap.md, in the order given:
$ARGUMENTS

This run is unattended. Never wait for the user. Never merge. Never push to
main. Never tag or release. Pushing the item's slug-named branch is
pre-authorized, even when the session designates a different working
branch. Process every item even if an earlier one fails.

Resolve queue numbers to item names against the current file. The slug is
the item name in lower case with spaces replaced by hyphens.

Before the first item, run git fetch origin main. Throughout this command,
"main" means origin/main as just fetched, never the local main ref, which
can be stale.

Never call a paid API, run the eval harness against a live provider, or
trigger the Evals or real-embeddings workflows.

For each item:

1. Gate. Skip the item and record why if any of these hold:
   - Every one of its Verify markers is already true on origin/main
     (roadmap is stale).
   - It has a "Resolve before starting" line, or any wording that needs a
     maintainer decision before implementation.
   - It is marked "Gated" or "conditional on" a result that is not yet
     recorded on origin/main.
   - It says it depends on, runs after, or is sequenced after another item
     that is not yet on origin/main. An item is on origin/main when its
     Verify markers are true or its entry is gone from the roadmap.
   - It cannot be started without human labels, human grading, or choosing
     outside material such as third-party repositories.

2. Branch. Run git fetch origin main, then create the branch named after
   the slug from origin/main.

3. Spec. Write docs/tasks/<slug>.md from the roadmap entry and the actual
   code, with these sections: Goal, Scope (In, Out), Files touched,
   Acceptance criteria, Tests to add, Docs to update, Roadmap item
   addressed. Acceptance criteria must be checkable offline, by command or
   by reading a file. Honor every Decided and Rejected line in the roadmap
   entry. List CHANGELOG.md under Docs to update when user-facing behavior
   changes. If the item's Verify marker needs a live eval run, put that run
   under a "Maintainer follow-up" heading, not in the acceptance criteria.
   Commit the spec alone.

4. Implement. Delegate to the implementer agent with the spec path.

5. Review. Delegate to the reviewer agent with the spec path. On
   VERDICT: CHANGES, send blockers and majors back to the implementer, then
   review again. At most 3 rounds.

6. Roadmap. Do not edit docs/roadmap.md. The maintainer removes shipped
   entries after merging.

7. PR. Push the branch and open a PR against main. The body has: summary,
   link to the spec, acceptance criteria with results, test count before
   and after, Deviations, unresolved reviewer findings, and Maintainer
   follow-up. If review did not reach APPROVE in 3 rounds, open it as a
   draft and say so in the title. The PR title, body, and comments contain
   no attribution line and no session URL.

8. CI. Wait for the PR checks, including commit-email. On failure, have the
   implementer fix it and push, at most 3 attempts. After any CI fix,
   delegate to the reviewer once more, limited to the commits pushed since
   its last verdict. An item is green only when checks pass and the
   reviewer has approved the final commit.

Keep your own context small: hold per-item summaries, not file contents.

Finish with one report: a table of item, status (green, draft, skipped),
PR link, and reason, followed by the maintainer follow-ups and the list of
roadmap entries to delete once merged.
