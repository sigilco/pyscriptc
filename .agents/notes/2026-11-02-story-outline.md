# Story outline: compiling a Flask backend, and what that taught the compiler

Blog outline, first person, not a draft. Nothing here is published. No draft prose below, only beats, one snippet, one rejected alternative, and the ordering.

## The one-sentence frame

I ran our compiler over a real Flask-plus-LLM backend, and the tool told me the truth in two numbers: 41 percent of the definitions pass the per-definition check, and 0 percent of the code runs native.

## Beats, in order

1. **The constraint that forced the design.** A native binary has no interpreter inside. That single sentence is the whole design: it means annotations are not hints, they are the lowering input; it means reflection has no substrate to run on; it means every "we'll figure it out at run time" habit of Python hits a wall with a named diagnostic. I did not add this constraint to be pure. I added it because every alternative on the market quietly reintroduces the interpreter (Nuitka links libpython, Cython and mypyc output modules for CPython to import, PyPy is an interpreter), and none of them ships a single file you can cross-compile and hand to someone without a Python install.
2. **The setup, honestly.** I picked a typical Flask plus OpenAI SDK LLM backend (anonymized): a real open-source app from GitHub, Flask, OpenAI, an agent framework (camel-ai/OASIS), PDF parsing, the works. First run: `pyscriptc coverage` on the tree. The compiler stopped with 1,285 fences and named every line. Not a crash. A table.
3. **The snippet.** The plane map moment (below). This is where the reader should feel the tool being more honest than the marketing on any compiler page.
4. **The lesson: per-definition numbers lie.** 41 percent of the definitions pass in isolation. But flask sits at the top of every module, the top level is all-or-nothing on the island plan, and the demotion propagates through calls. The plane map routes 903 of 903 functions to embedded CPython. The `--json` output even names the function that pulled each demotion (`demotedBy`). A number that looks like "41 percent of your app is compilable" and a number that says "0 percent of your app runs native" are both correct, and only the second one is useful.
5. **The taxonomy I came out with.** Three buckets, and the tool sorts every fence into one: (a) what the island tier already makes work (flask, openai, PyMuPDF, every dependency here, at marshalling cost), (b) what the typed-first rule says you can fix (221 missing-annotation fences, each with the exact line and fix), (c) what is structural (pydantic reads annotations at run time and a binary without an interpreter has none; C extensions have no Python source to lower; agent frameworks dispatch through getattr and the dispatch is the program). The honest product statement is the buckets, not a percentage.
6. **What this is for, stated in the negative.** Not for the Flask app as native code. For the typed core you can carve out of it: the graph builder, the retry logic, the config validation, the data pipeline. The app stays on CPython; the parts that earn a binary get one.
7. **Close on the first-run gap.** The outsider who did the same exercise first installed 0.4.1 from PyPI while master was 0.7.0, and hit a `build` command that demands a `[project.scripts]` entry this app does not have. The tooling told the truth about the code and told nothing about itself. We filed the issues. The compiler being honest is only half the job; the install channel and the first build error have to be honest too.

## The one code snippet

Not a feature demo. The moment the plane map contradicted the summary:

```console
$ pyscriptc assess backend/

Summary:
  Native:    41.3% (386 of 934 definitions)

$ pyscriptc assess backend/ --json   # the plane map inside says:
  "moduleTopLevel": "island",
  "functions": { "create_app": "island", "report_agent": "island", ... }
```

Cut the JSON down to three lines in the post; the point is that both outputs are true and one of them is the one to read.
Alternative snippet if the plane-map one reads too inside-baseball: the `path to the island` output, which prints the exact pyproject lines (`flask = "island"`) with the import site that forced each one. That one shows the tool ending with an actionable answer instead of a refusal.

## The one rejected alternative

mypyc. It is the closest input contract: typed Python in, checked before lowering. I considered carving the app's services into mypyc extension modules instead of fighting the subset. Rejected because the artifact is wrong for the goal: mypyc output is a `.so` that a CPython process imports, so the deployment still ships an interpreter, still manages a venv, and still inherits CPython's startup. My constraint was "one file, no interpreter at run time", and mypyc does not try to meet it; it optimizes a different point on the trade (stay inside CPython, restrict the typed modules). Say plainly in the post that for a reader whose goal is "my hot module runs faster inside my existing process", mypyc is the right tool and this post's tool is not.

## Voice notes

- First person, past tense for the experiment, present tense for the conclusions.
- Banned: leverage, robust, seamless, unlock, empower, delve. Also skip "journey".
- Numbers over adjectives: 1,285 fences, 41.3 percent, 903 of 903, 221 annotations, 644 import fences. Every number was measured on master, commit 9d6c831, CPython 3.14.9.
- Name the failures first (PyPI staleness, the build entry-point error) before the wins. Credibility comes from the tool telling on itself.
- Do not claim speedups. The only benchmark claim allowed in the post is our own measured one if cited with the harness, or none.
- Length target: 1,200 to 1,600 words. One diagram if any: the two planes (native / island) and one arrow showing a demotion propagating from a flask import through a call chain.
