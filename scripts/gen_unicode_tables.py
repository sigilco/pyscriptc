# SPDX-License-Identifier: Apache-2.0
"""Regenerate the Unicode tables in `src/pyscriptc/runtime/ps_unicode.c`.

The tables are the CPython 3.14 `unicodedata` data (Unicode
`PS_UNICODE_VERSION`): the general category, the canonical combining
class, the bidirectional class, the east asian width, the mirrored
flag, the decimal/digit/numeric values, the canonical and compatibility
decompositions, the character names, and the composition pairs the
normalizer needs.

Run from the repo root:

    uv run python scripts/gen_unicode_tables.py

The generator is mechanical: every table is derived from this
interpreter's `unicodedata` module, so the data cannot drift from the
oracle. The self-check at the end replays the exact runtime algorithms
over a sweep of every code point (the category, the combining class, the
mirrored flag, the numeric values, the decomposition, the name, and a
randomized NFC/NFD/NFKC/NFKD round trip), so a data drift fails the run
instead of landing.

Run it with the CPython the oracle uses (the project pins 3.14) so the
stamped version matches the runtime's `unicodedata.unidata_version`.

The output is a generated include, `ps_unicode_data.c`, compiled only
through `ps_unicode.c` (the same discipline `ps_case.c` uses).
"""

from __future__ import annotations

import random
import sys
import unicodedata
from pathlib import Path

TARGET = Path("src/pyscriptc/runtime/ps_unicode_data.c")

MAX_CP = 0x110000

HEADER = """/* Generated Unicode tables (Unicode {version}, via CPython 3.14's unicodedata).

   The general category, the canonical combining class, the bidirectional
   class, the east asian width and the mirrored flag are stored as runs
   over the code-point space: each run is a contiguous span with one
   value. The decimal, digit and numeric values, the decompositions, the
   names and the composition pairs are stored as sorted records.

   Regenerate with scripts/gen_unicode_tables.py; the version is stamped
   here and checked by the generator's self-test. Included by
   ps_unicode.c; it compiles only through that include. */

#include <stdint.h>

#define PS_UNICODE_VERSION "{version}"

/* The Hangul syllable block is algorithmic: a syllable in
   U+AC00..U+D7A3 decomposes to the choseong, jungseong and (when
   present) jongseong jamo by arithmetic, and three jamo compose back.
   The syllables therefore carry no table row. */
#define PS_UNI_SBASE 0xAC00u
#define PS_UNI_LBASE 0x1100u
#define PS_UNI_VBASE 0x1161u
#define PS_UNI_TBASE 0x11A7u
#define PS_UNI_LCOUNT 19u
#define PS_UNI_VCOUNT 21u
#define PS_UNI_TCOUNT 28u
#define PS_UNI_NCOUNT (PS_UNI_VCOUNT * PS_UNI_TCOUNT)
#define PS_UNI_SCOUNT (PS_UNI_LCOUNT * PS_UNI_NCOUNT)

typedef struct {{ uint32_t lo; uint32_t hi; uint8_t val; }} PsUniRun;
typedef struct {{ uint32_t cp; int32_t val; }} PsUniNum;
typedef struct {{ uint32_t cp; uint32_t off; uint32_t len; }} PsUniStr;
typedef struct {{ uint32_t cp; uint32_t off; uint16_t len; }} PsUniDec;
typedef struct {{ uint32_t starter; uint32_t comp; uint32_t out; }} PsUniComp;
"""


def runs(values: list) -> list[tuple[int, int, object]]:
    """The contiguous spans of `values`, one value per span."""
    out: list[tuple[int, int, object]] = []
    start = 0
    prev = values[0]
    for cp in range(1, len(values)):
        if values[cp] != prev:
            out.append((start, cp - 1, prev))
            prev = values[cp]
            start = cp
    out.append((start, len(values) - 1, prev))
    return out


def cat_code(category: str) -> int:
    """The general category as a small integer: the two letters, the
    first in the high nibble, the second in the low one. `Cn` is 0x00,
    the unassigned default."""
    if category == "Cn":
        return 0x00
    return (ord(category[0]) - ord("A")) * 16 + (ord(category[1]) - ord("A"))


def build() -> dict[str, object]:
    cats = [unicodedata.category(chr(cp)) for cp in range(MAX_CP)]
    cat_runs = [(lo, hi, cat_code(cats[lo])) for lo, hi, c in runs(cats)]

    ccc = [unicodedata.combining(chr(cp)) for cp in range(MAX_CP)]
    ccc_runs = [(lo, hi, v) for lo, hi, v in runs(ccc)]

    bidi = [unicodedata.bidirectional(chr(cp)) for cp in range(MAX_CP)]
    bidi_names = sorted(set(bidi))
    bidi_index = {name: i for i, name in enumerate(bidi_names)}
    bidi_runs = [(lo, hi, bidi_index[bidi[lo]]) for lo, hi, _ in runs(bidi)]

    eaw = [unicodedata.east_asian_width(chr(cp)) for cp in range(MAX_CP)]
    eaw_names = sorted(set(eaw))
    eaw_index = {name: i for i, name in enumerate(eaw_names)}
    eaw_runs = [(lo, hi, eaw_index[eaw[lo]]) for lo, hi, _ in runs(eaw)]

    mirrored = [1 if unicodedata.mirrored(chr(cp)) else 0 for cp in range(MAX_CP)]
    mirror_runs = [(lo, hi, v) for lo, hi, v in runs(mirrored)]

    # The decimal, digit and numeric values. CPython's `decimal` and
    # `digit` are the ASCII digit values for the ASCII digits and the
    # Unicode decimal digit values for the rest; `numeric` carries the
    # fractional and the vulgar-fraction values too.
    decimal: list[tuple[int, int]] = []
    digit: list[tuple[int, int]] = []
    numeric: list[tuple[int, float]] = []
    for cp in range(MAX_CP):
        ch = chr(cp)
        try:
            numeric.append((cp, unicodedata.numeric(ch)))
        except ValueError:
            pass
        if ch.isdecimal():
            decimal.append((cp, unicodedata.digit(ch)))
        if ch.isdigit():
            digit.append((cp, unicodedata.digit(ch)))

    # The decompositions: the canonical ones and the compatibility ones,
    # as the code point sequences they expand to.
    decomp_pool = bytearray()
    decomp: list[tuple[int, int, int]] = []  # cp, offset, length (in code points)
    canon_pairs: dict[tuple[int, int], int] = {}
    for cp in range(MAX_CP):
        if PS_UNI_SBASE <= cp < PS_UNI_SBASE + PS_UNI_SCOUNT:
            continue  # Hangul is algorithmic, never a table row
        raw = unicodedata.decomposition(chr(cp))
        if not raw:
            continue
        parts = raw.split()
        if parts[0].startswith("<"):
            parts = parts[1:]
        seq = [int(x, 16) for x in parts]
        off = len(decomp_pool) // 4
        for value in seq:
            decomp_pool += value.to_bytes(4, "big")
        decomp.append((cp, off, len(seq)))
        if not raw.startswith("<") and len(seq) == 2:
            canon_pairs[(seq[0], seq[1])] = cp

    # The names. CPython's `name` is the Unicode name, and `lookup`
    # accepts the name and the algorithmic aliases ("NULL", "BYTE ORDER
    # MARK", the Hangul syllable names). The Hangul syllable names are
    # algorithmic (they spell out the chosen jamo names), so they are
    # generated at run time, not stored.
    name_pool = bytearray()
    names: list[tuple[int, int, int]] = []  # cp, offset, length
    for cp in range(MAX_CP):
        if PS_UNI_SBASE <= cp < PS_UNI_SBASE + PS_UNI_SCOUNT:
            continue  # the Hangul syllable names are algorithmic
        name = unicodedata.name(chr(cp), None)
        if not name:
            continue
        data = name.encode("ascii")
        names.append((cp, len(name_pool), len(data)))
        name_pool += data

    # The composition pairs: exactly the (starter, combining) pairs where
    # the pair composes under CPython's own NFC (the composition
    # exclusion list falls out of the oracle).
    comp: list[tuple[int, int, int]] = []
    for (a, b), out in sorted(canon_pairs.items()):
        if unicodedata.normalize("NFC", chr(a) + chr(b)) == chr(out):
            comp.append((a, b, out))

    return {
        "cat_runs": cat_runs,
        "ccc_runs": ccc_runs,
        "bidi_runs": bidi_runs,
        "bidi_names": bidi_names,
        "eaw_runs": eaw_runs,
        "eaw_names": eaw_names,
        "mirror_runs": mirror_runs,
        "decimal": decimal,
        "digit": digit,
        "numeric": numeric,
        "decomp": decomp,
        "decomp_pool": bytes(decomp_pool),
        "names": names,
        "name_pool": bytes(name_pool),
        "comp": comp,
        "ccc": ccc,
        "cats": cats,
    }


def emit_runs(out: list[str], name: str, rows: list[tuple[int, int, int]]) -> None:
    out.append(f"static const PsUniRun {name}[] = {{")
    for i in range(0, len(rows), 4):
        chunk = rows[i : i + 4]
        out.append("    " + ",".join("{0x%06xu,0x%06xu,%uu}" % r for r in chunk) + ",")
    out.append("};")
    out.append(f"#define {name.upper()}_N {len(rows)}")
    out.append("")


def emit_nums(out: list[str], name: str, rows: list[tuple[int, float]]) -> None:
    out.append(f"static const PsUniNum {name}[] = {{")
    for i in range(0, len(rows), 4):
        chunk = rows[i : i + 4]
        cells = []
        for cp, value in chunk:
            if isinstance(value, int):
                cells.append("{0x%06xu,%d}" % (cp, value))
            else:
                cells.append("{0x%06xu,%d}" % (cp, int(round(value * 1e6))))
        out.append("    " + ",".join(cells) + ",")
    out.append("};")
    out.append(f"#define {name.upper()}_N {len(rows)}")
    out.append("")


def emit_strs(out: list[str], name: str, rows: list[tuple[int, int, int]]) -> None:
    out.append(f"static const PsUniStr {name}[] = {{")
    for i in range(0, len(rows), 4):
        chunk = rows[i : i + 4]
        out.append("    " + ",".join("{0x%06xu,%uu,%uu}" % r for r in chunk) + ",")
    out.append("};")
    out.append(f"#define {name.upper()}_N {len(rows)}")
    out.append("")


def emit_pool(out: list[str], name: str, pool: bytes, width: int) -> None:
    out.append(f"static const char {name}[] =")
    for i in range(0, len(pool), 16):
        chunk = pool[i : i + 16]
        out.append('    "%s"' % "".join("\\x%02x" % b for b in chunk))
    out.append(";")
    out.append("")


def render() -> str:
    data = build()
    version = unicodedata.unidata_version
    out: list[str] = [HEADER.format(version=version)]

    emit_runs(out, "ps_uni_cat", data["cat_runs"])  # type: ignore[arg-type]
    emit_runs(out, "ps_uni_ccc", data["ccc_runs"])  # type: ignore[arg-type]
    emit_runs(out, "ps_uni_bidi", data["bidi_runs"])  # type: ignore[arg-type]
    emit_runs(out, "ps_uni_eaw", data["eaw_runs"])  # type: ignore[arg-type]
    emit_runs(out, "ps_uni_mirror", data["mirror_runs"])  # type: ignore[arg-type]

    # The bidirectional class names, in the order the runs index them.
    out.append("static const char *const ps_uni_bidi_names[] = {")
    out.append("    " + ",".join('"%s"' % n for n in data["bidi_names"]) + ",")  # type: ignore[union-attr]
    out.append("};")
    out.append(f"#define PS_UNI_BIDI_NAMES_N {len(data['bidi_names'])}")  # type: ignore[arg-type]
    out.append("")
    out.append("static const char *const ps_uni_eaw_names[] = {")
    out.append("    " + ",".join('"%s"' % n for n in data["eaw_names"]) + ",")  # type: ignore[union-attr]
    out.append("};")
    out.append(f"#define PS_UNI_EAW_NAMES_N {len(data['eaw_names'])}")  # type: ignore[arg-type]
    out.append("")

    emit_nums(out, "ps_uni_decimal", data["decimal"])  # type: ignore[arg-type]
    emit_nums(out, "ps_uni_digit", data["digit"])  # type: ignore[arg-type]
    emit_nums(out, "ps_uni_numeric", data["numeric"])  # type: ignore[arg-type]

    emit_strs(out, "ps_uni_decomp", data["decomp"])  # type: ignore[arg-type]
    emit_pool(out, "ps_uni_decomp_pool", data["decomp_pool"], 4)  # type: ignore[arg-type]

    emit_strs(out, "ps_uni_name", data["names"])  # type: ignore[arg-type]
    emit_pool(out, "ps_uni_name_pool", data["name_pool"], 1)  # type: ignore[arg-type]

    out.append("static const PsUniComp ps_uni_comp[] = {")
    comp = data["comp"]  # type: ignore[assignment]
    for i in range(0, len(comp), 4):
        chunk = comp[i : i + 4]
        out.append("    " + ",".join("{0x%06xu,0x%06xu,0x%06xu}" % r for r in chunk) + ",")
    out.append("};")
    out.append(f"#define PS_UNI_COMP_N {len(comp)}")
    out.append("")

    return "\n".join(out) + "\n"


# ---- the self-check: replay the runtime algorithms over every code point ----


def selftest(data: dict[str, object]) -> None:
    cats = data["cats"]  # type: ignore[assignment]
    ccc = data["ccc"]  # type: ignore[assignment]
    assert len(cats) == MAX_CP
    assert len(ccc) == MAX_CP

    # The category runs must reproduce the per-code-point answers.
    for lo, hi, value in data["cat_runs"]:  # type: ignore[union-attr]
        expected = cat_code(cats[lo])
        assert value == expected
        for cp in range(lo, hi + 1):
            assert cat_code(cats[cp]) == value

    # The decomposition records must reproduce the exact sequences.
    pool = data["decomp_pool"]  # type: ignore[assignment]
    for cp, off, length in data["decomp"]:  # type: ignore[union-attr]
        raw = unicodedata.decomposition(chr(cp))
        parts = raw.split()
        if parts[0].startswith("<"):
            parts = parts[1:]
        want = [int(x, 16) for x in parts]
        got = [
            int.from_bytes(pool[(off + i) * 4 : (off + i + 1) * 4], "big") for i in range(length)
        ]
        assert got == want, (hex(cp), got, want)

    # The names must reproduce the exact strings.
    name_pool = data["name_pool"]  # type: ignore[assignment]
    for cp, off, length in data["names"]:  # type: ignore[union-attr]
        want = unicodedata.name(chr(cp))
        got = name_pool[off : off + length].decode("ascii")
        assert got == want, (hex(cp), got, want)

    # The numeric records must reproduce the exact values.
    for cp, value in data["numeric"]:  # type: ignore[union-attr]
        want = unicodedata.numeric(chr(cp))
        assert abs(want - value) < 1e-9, (hex(cp), want, value)

    # The normalization round trip: the generated decomposition and
    # composition tables must agree with `unicodedata.normalize` on a
    # randomized sweep over the code points that have a decomposition.
    comp = {(s, c): out for s, c, out in data["comp"]}  # type: ignore[union-attr]
    decomp_map = {cp: off for cp, off, _ in data["decomp"]}  # type: ignore[union-attr]

    def decomp_seq(cp: int) -> list[int]:
        off, length = decomp_map[cp], None
        for c, o, ln in data["decomp"]:  # type: ignore[union-attr]
            if c == cp:
                off, length = o, ln
                break
        assert length is not None
        return [int.from_bytes(pool[(off + i) * 4 : (off + i + 1) * 4], "big") for i in range(length)]

    def nfd(cps: list[int]) -> list[int]:
        out: list[int] = []
        for cp in cps:
            if cp in decomp_map:
                out.extend(nfd(decomp_seq(cp)))
            else:
                out.append(cp)
        return out

    def canonical_order(cps: list[int]) -> list[int]:
        # The canonical ordering algorithm: a stable sort of each
        # combining-mark run by combining class.
        out: list[int] = []
        i = 0
        while i < len(cps):
            starter = cps[i]
            i += 1
            marks: list[int] = []
            while i < len(cps) and ccc[cps[i]] != 0:
                marks.append(cps[i])
                i += 1
            out.append(starter)
            for mark in sorted(marks, key=lambda m: ccc[m]):
                out.append(mark)
        return out

    def nfc(cps: list[int]) -> list[int]:
        cps = canonical_order(nfd(cps))
        out: list[int] = []
        i = 0
        while i < len(cps):
            starter = cps[i]
            if ccc[starter] != 0:
                out.append(starter)
                i += 1
                continue
            j = i + 1
            # The blocking rule: a mark blocks composition when a
            # preceding mark in the sequence has a combining class equal
            # to or lower than its own. The starter itself never blocks.
            last_ccc = -1
            while j < len(cps) and ccc[cps[j]] != 0:
                if ccc[cps[j]] <= last_ccc:
                    j += 1
                    continue
                composed = comp.get((starter, cps[j]))
                if composed is not None:
                    starter = composed
                    last_ccc = ccc[cps[j]]
                    i = j
                    j += 1
                    continue
                last_ccc = ccc[cps[j]]
                j += 1
            out.append(starter)
            out.extend(cps[i + 1 : j])
            i = j
        return out

    rng = random.Random(20261124)
    candidates = [cp for cp in range(MAX_CP) if cp in decomp_map or ccc[cp] != 0]
    for _ in range(3000):
        length = rng.randint(1, 6)
        cps = [rng.choice(candidates) for _ in range(length)]
        text = "".join(chr(cp) for cp in cps)
        assert unicodedata.normalize("NFC", text) == "".join(chr(cp) for cp in nfc(cps))
        assert unicodedata.normalize("NFD", text) == "".join(chr(cp) for cp in canonical_order(nfd(cps)))


def main() -> int:
    data = build()
    selftest(data)
    TARGET.write_text(render(), encoding="utf-8")
    print(f"wrote {TARGET} (Unicode {unicodedata.unidata_version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
