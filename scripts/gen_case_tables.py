"""Regenerate the case-mapping tables in `src/pyscriptc/runtime/ps_case.c`.

The tables are the CPython 3.14 `str` case mappings (Unicode
`PS_CASE_UNICODE_VERSION`): one sorted record per cased or case-ignorable
code point, carrying the upper/lower/casefold mappings (identity when the
code point maps to itself, PS_CASE_EXP when the mapping is the
multi-character expansion in the shared UTF-8 byte pool) plus the Cased
and Case_Ignorable flags the title word scan and the final-sigma rule
need. The title specials (the titlecase letters, the Georgian Mtavruli
range and the eszett) sit in the pool beside the expansions.

Run from the repo root:

    uv run python scripts/gen_case_tables.py

The script self-checks the generated tables against this interpreter's
`str` methods for a sweep of every code point (mappings, casedness,
ignorability, and a randomized lower/title/swapcase replay of the exact
runtime algorithms), so a data drift fails the run instead of landing.
Run it with the CPython the oracle uses (the project pins 3.14) so the
stamped version matches the runtime's `unicodedata.unidata_version`.
"""

from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

TARGET = Path("src/pyscriptc/runtime/ps_case.c")

PS_CASE_EXP = 0xFFFFFFFE

HEADER = """/* Generated case-mapping tables (Unicode {version}, via CPython 3.14's str).

   One sorted record per cased or case-ignorable code point. The up, lo
   and fold fields hold the mapped code point, cp itself when the mapping
   is identity, PS_CASE_EXP when the mapping is the multi-character
   expansion in ps_case_pool (found through the exp tables). The flags
   field carries PS_CASE_F_CASED and PS_CASE_F_IGNORABLE for the title
   word scan and the final-sigma rule; title differs from upper only for
   the specials in the pool (the titlecase letters, the Georgian Mtavruli
   range and the eszett). Regenerate with scripts/gen_case_tables.py; the
   version is stamped here and checked by the generator's self-test.
   Included by ps_rt.c; it compiles only through that include. */

#define PS_CASE_EXP 0xFFFFFFFEu
#define PS_CASE_F_CASED 0x1u
#define PS_CASE_F_IGNORABLE 0x2u
#define PS_CASE_UNICODE_VERSION "{version}"

typedef struct {{ uint32_t cp; uint32_t up; uint32_t lo; uint32_t fold; uint32_t flags; }} PsCaseRec;
static const PsCaseRec ps_case_map[] = {{
"""


def is_cased(ch: str) -> bool:
    return (
        ch.islower()
        or ch.isupper()
        or ch.istitle()
        or ch.upper() != ch
        or ch.lower() != ch
        or ch.casefold() != ch
        or ch.title() != ch
    )


def is_case_ignorable(ch: str) -> bool:
    # Behavioral oracle: a char is Case_Ignorable exactly when it is
    # skipped by both scans of CPython's final-sigma rule. Clause one
    # probes the forward scan (a non-ignorable non-cased char between the
    # sigma and a cased char forces the final form), clause two the
    # backward scan (a cased non-ignorable char before the sigma forces
    # the final form even at end of string).
    return ("aΣ" + ch + "b").lower() == "aσ" + ch + "b" and (ch + "Σ").lower().endswith("σ")


def build() -> tuple[
    list[tuple[int, int, int, int, int]], dict[int, str], dict[int, str], dict[int, str], dict[int, str]
]:
    recs: list[tuple[int, int, int, int, int]] = []
    exp_up: dict[int, str] = {}
    exp_lo: dict[int, str] = {}
    exp_fold: dict[int, str] = {}
    title_sp: dict[int, str] = {}
    for cp in range(0x110000):
        ch = chr(cp)
        up = ch.upper()
        lo = ch.lower()
        fold = ch.casefold()
        title = ch.title()
        up_id = up == ch
        lo_id = lo == ch
        fold_id = fold == ch
        upv = ord(up) if not up_id and len(up) == 1 else (cp if up_id else PS_CASE_EXP)
        lov = ord(lo) if not lo_id and len(lo) == 1 else (cp if lo_id else PS_CASE_EXP)
        foldv = ord(fold) if not fold_id and len(fold) == 1 else (cp if fold_id else PS_CASE_EXP)
        cased = is_cased(ch)
        ignorable = is_case_ignorable(ch)
        if not cased and not ignorable:
            continue
        if upv == PS_CASE_EXP:
            exp_up[cp] = up
        if lov == PS_CASE_EXP:
            exp_lo[cp] = lo
        if foldv == PS_CASE_EXP:
            exp_fold[cp] = fold
        flags = (1 if cased else 0) | (2 if ignorable else 0)
        recs.append((cp, upv, lov, foldv, flags))
        if title != up:
            title_sp[cp] = title
    return recs, exp_up, exp_lo, exp_fold, title_sp


def render() -> str:
    recs, exp_up, exp_lo, exp_fold, title_sp = build()
    version = unicodedata.unidata_version
    out: list[str] = [HEADER.format(version=version)]
    row: list[str] = []
    for cp, upv, lov, foldv, flags in recs:
        row.append("{%#06xu,%#010xu,%#010xu,%#010xu,%#04xu}" % (cp, upv, lov, foldv, flags))
        if len(row) == 4:
            out.append("    " + ",".join(row) + ",")
            row = []
    if row:
        out.append("    " + ",".join(row) + ",")
    out.append("};")
    out.append("#define PS_CASE_MAP_N %d" % len(recs))
    out.append("")
    out.append("typedef struct { uint32_t cp; uint32_t off; uint32_t len; } PsCaseExp;")

    pool = bytearray()

    def emit_exp(name: str, table: dict[int, str]) -> None:
        out.append("static const PsCaseExp %s[] = {" % name)
        rows: list[str] = []
        for cp in sorted(table):
            data = table[cp].encode("utf-8")
            off = len(pool)
            pool.extend(data)
            rows.append("{%#06xu,%du,%du}" % (cp, off, len(data)))
        for i in range(0, len(rows), 4):
            out.append("    " + ",".join(rows[i : i + 4]) + ",")
        out.append("};")
        out.append("#define %s_N %d" % (name.upper(), len(table)))
        out.append("")

    emit_exp("ps_case_exp_up", exp_up)
    emit_exp("ps_case_exp_lo", exp_lo)
    emit_exp("ps_case_exp_fold", exp_fold)
    emit_exp("ps_case_exp_title", title_sp)

    out.append("static const char ps_case_pool[] =")
    for i in range(0, len(pool), 16):
        chunk = pool[i : i + 16]
        out.append('    "%s"' % "".join("\\x%02x" % b for b in chunk))
    out.append(";")
    return "\n".join(out) + "\n"


def _mapped(rec: tuple[int, int, int, int, int] | None, which: int, cp: int, exps: dict[int, str]) -> str:
    """The mapped string for one record field: the single code point, the
    identity (the field holds cp), or the expansion from the pool table."""
    if rec is None:
        return chr(cp)
    value = rec[which]
    if value == cp:
        return chr(cp)
    if value < PS_CASE_EXP:
        return chr(value)
    return exps.get(cp, chr(cp))


def self_check() -> None:
    recs, exp_up, exp_lo, exp_fold, title_sp = build()
    by_cp = {rec[0]: rec for rec in recs}
    for cp in range(0x110000):
        ch = chr(cp)
        rec = by_cp.get(cp)
        assert _mapped(rec, 1, cp, exp_up) == ch.upper(), hex(cp)
        assert _mapped(rec, 2, cp, exp_lo) == ch.lower(), hex(cp)
        if rec is None or rec[3] == cp:
            fold = ch
        elif rec[3] < PS_CASE_EXP:
            fold = chr(rec[3])
        else:
            fold = exp_fold.get(cp, ch)
        assert fold == ch.casefold(), hex(cp)
        want_title = title_sp.get(cp, ch.upper())
        assert ch.title() == want_title, hex(cp)
        assert is_cased(ch) == bool(rec is not None and rec[4] & 1), hex(cp)
        assert is_case_ignorable(ch) == bool(rec is not None and rec[4] & 2), hex(cp)
    _replay_check(by_cp, exp_up, exp_lo, exp_fold, title_sp)


def _replay_check(
    by_cp: dict[int, tuple[int, int, int, int, int]],
    exp_up: dict[int, str],
    exp_lo: dict[int, str],
    exp_fold: dict[int, str],
    title_sp: dict[int, str],
) -> None:
    """Replay the runtime algorithms over random adversarial strings and
    compare byte for byte with this interpreter: the full lower (with the
    final-sigma rule), title and swapcase."""
    import random

    alphabet = [
        0xDF,
        0x1C4,
        0x1C5,
        0x1C6,
        0x130,
        0x131,
        0x2B0,
        0x138,
        0x10D0,
        0x1C90,
        0x30,
        0x41,
        0x61,
        0x20,
        0x27,
        0xAD,
        0x301,
        0x345,
        0x1F130,
        0x3A3,
        0x3C2,
        0x3C3,
        0x387,
        0x2019,
        0xB5,
        0xFF,
        0xB7,
        0x308,
        0x897,
        0x10D4E,
        0x2E,
    ]

    def flags_of(cp: int) -> int:
        rec = by_cp.get(cp)
        return rec[4] if rec is not None else 0

    def cased(cp: int) -> bool:
        return bool(flags_of(cp) & 1)

    def ignorable(cp: int) -> bool:
        return bool(flags_of(cp) & 2)

    def lower1(cp: int) -> str:
        return _mapped(by_cp.get(cp), 2, cp, exp_lo)

    def upper1(cp: int) -> str:
        return _mapped(by_cp.get(cp), 1, cp, exp_up)

    def title1(cp: int) -> str:
        return title_sp.get(cp, upper1(cp))

    def sigma_final(cps: list[int], i: int) -> bool:
        j = i - 1
        while j >= 0 and ignorable(cps[j]):
            j -= 1
        if j < 0 or not cased(cps[j]):
            return False
        k = i + 1
        while k < len(cps) and ignorable(cps[k]):
            k += 1
        return k >= len(cps) or not cased(cps[k])

    def lower_full(cps: list[int]) -> str:
        return "".join("ς" if c == 0x3A3 and sigma_final(cps, i) else lower1(c) for i, c in enumerate(cps))

    def title_full(cps: list[int]) -> str:
        out: list[str] = []
        prev_cased = False
        for i, c in enumerate(cps):
            if prev_cased:
                out.append("ς" if c == 0x3A3 and sigma_final(cps, i) else lower1(c))
            else:
                out.append(title1(c))
            prev_cased = cased(c)
        return "".join(out)

    def swap_full(cps: list[int]) -> str:
        # CPython's swapcase: ISLOWER chars take the full upper mapping,
        # ISUPPER chars the context-aware lower (the final-sigma rule
        # applies), everything else is identity. The tables classify a
        # code point by which mapping directions differ from identity:
        # only up differs means a lower char, only lo differs means an
        # upper char, both differ is an Lt (title == identity, swap to
        # itself) or a digraph Lu (title != identity, swaps down).
        def swap_lower(cps: list[int], at: int, c: int) -> str:
            if c == 0x3A3 and sigma_final(cps, at):
                return "ς"
            return lower1(c)

        out: list[str] = []
        for at, c in enumerate(cps):
            rec = by_cp.get(c)
            up_diff = rec is not None and rec[1] != c
            lo_diff = rec is not None and rec[2] != c
            if up_diff and not lo_diff:
                out.append(upper1(c))
            elif lo_diff and not up_diff:
                out.append(swap_lower(cps, at, c))
            elif up_diff and lo_diff:
                if title_sp.get(c) == chr(c):
                    out.append(chr(c))
                else:
                    out.append(swap_lower(cps, at, c))
            else:
                out.append(chr(c))
        return "".join(out)

    rng = random.Random(0x5EED)
    for _ in range(20000):
        n = rng.randint(0, 14)
        cps = [rng.choice(alphabet) for _ in range(n)]
        s = "".join(chr(c) for c in cps)
        assert lower_full(cps) == s.lower(), s.encode("unicode_escape")
        assert title_full(cps) == s.title(), s.encode("unicode_escape")
        assert swap_full(cps) == s.swapcase(), s.encode("unicode_escape")


def main() -> int:
    self_check()
    TARGET.write_text(render(), encoding="utf-8")
    print(f"wrote {TARGET} (Unicode {unicodedata.unidata_version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
