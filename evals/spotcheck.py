"""Flag suspicious claim statuses by checking identifiers against fixture source.

Not a verifier. Claims are English; this only catches the cheap failure mode where
a claim marked "contradicted" names something that is actually present in the source.
Everything it flags still needs a human to read.
"""
import glob, json, re
from pathlib import Path

SRC = Path(__file__).parent / "fixture_app" / "blog"
corpus = "\n".join(
    p.read_text() for p in SRC.rglob("*")
    if p.is_file() and p.suffix in {".py", ".html"}
)

# identifiers, dotted paths, template names, quoted strings
TOKEN = re.compile(r"`([^`]+)`|\b([a-z_][a-z0-9_]*\.(?:py|html))\b|\b([a-z_][a-z0-9_]{3,})\(")

def tokens(claim):
    out = set()
    for m in TOKEN.finditer(claim):
        t = next(g for g in m.groups() if g)
        t = t.strip("()").split("(")[0]
        if len(t) > 3:
            out.add(t)
    return out

path = sorted(glob.glob(str(Path(__file__).parent / "results" / "*.json")))[-1]
d = json.load(open(path))
print(f"checking {Path(path).name}\n")

suspicious = absent_but_present = 0
for j in d["judgments"]:
    for side in ("a", "b"):
        label = "rag_on" if ((side == "a") == j["rag_on_is_a"]) else "rag_off"
        for c in j[side].get("claims", []):
            found = [t for t in tokens(c["claim"]) if t in corpus]
            if c["status"] == "contradicted" and found:
                suspicious += 1
                print(f"SUSPECT contradicted [{j['fixture']}/{label}] names {found}")
                print(f"        {c['claim'][:150]}\n")
            elif c["status"] == "absent" and found:
                absent_but_present += 1

print(f"\ncontradicted claims naming real source tokens: {suspicious}")
print(f"absent claims naming real source tokens: {absent_but_present}")
