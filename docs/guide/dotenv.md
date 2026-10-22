---
title: Reading .env files
description: The dotenv subset pyscriptc lowers, the rules it parses, and where it differs from python-dotenv.
---

pyscriptc lowers two names of the `dotenv` module (the `python-dotenv` package): `dotenv.dotenv_values` and `dotenv.load_dotenv`.
Both read a `.env` file through a C pass that parses the document and re-emits it as JSON, so the result follows the same annotation rules as `json.loads`.

```python
import dotenv

values: dict[str, str | None] = dotenv.dotenv_values(".env")
if dotenv.load_dotenv(".env"):
    print(values["DATABASE_URL"])
```

`dotenv_values` needs its result annotation (`PS1001` without one) and takes one positional `str` path.
`load_dotenv` returns `bool` and takes the same path.
The keyword arguments of python-dotenv (`stream`, `verbose`, `interpolate`, `override`, `encoding`) and every other member of the module (`find_dotenv`, `set_key`, `unset_key`, `DotEnv`) are outside the subset and fence.

## The parsing rules

The subset is closed and documented. A line is one of these:

| Form | Binding |
| --- | --- |
| `KEY=value` | the key with the value |
| `KEY=` | the key with the empty value |
| `KEY` | the key with no value (`None`) |
| `KEY= # comment` | the key with the empty value |
| `# comment` | nothing |
| an empty line | nothing |

The keys are bare: a quoted key (`"KEY"=1`) raises `ValueError`.
An `export ` prefix is allowed and ignored.
A UTF-8 byte order mark at the start of the file is stripped.

The values follow the quoting rules:

| Form | Value |
| --- | --- |
| unquoted | the rest of the line, with the ` #` comment cut and the trailing spaces removed |
| `KEY=value#nospace` | `value#nospace`: a `#` right after the `=` is part of the value |
| double quoted | the escapes `\" \' \\ \a \b \f \n \r \t \v` are decoded |
| single quoted | the escapes `\\` and `\'` are decoded, the rest stay literal |

A later binding of the same key overwrites the earlier one.
The parsed document holds only strings, so the result type is `dict[str, str | None]`.

What the subset does not do: no interpolation (`$NAME` stays literal), no multi-line values, no `export` variants beyond the plain prefix, and no quoted keys.

## Where it differs from python-dotenv

Three differences, all listed as divergence 16 in [Divergences from CPython](../reference/divergences.md):

- A malformed line (text after a quoted value, an unterminated quote, a quoted key, a key followed by something other than `=`) raises `ValueError`. python-dotenv skips the line and logs a warning.
- A missing file parses as the empty document, so `dotenv_values` returns `{}` and `load_dotenv` returns `False`. python-dotenv searches for a `.env` file with `find_dotenv()` first.
- `load_dotenv` does not overwrite a variable that is already in the environment, which is python-dotenv's `override=False` default, and it returns `True` whenever the document held a binding, as python-dotenv does.

Non-ASCII keys and values fail in the JSON path, as they do for `json.loads` in this subset.
