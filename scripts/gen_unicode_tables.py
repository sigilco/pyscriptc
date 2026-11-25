# SPDX-License-Identifier: Apache-2.0
"""Regenerate the Unicode tables in `src/pyscriptc/runtime/ps_unicode_data.c`.

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
over a sweep of every code point (the category, the combining class,
the mirrored flag, the numeric values, the decomposition, the name, and a
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
from fractions import Fraction
from pathlib import Path

TARGET = Path("src/pyscriptc/runtime/ps_unicode_data.c")

MAX_CP = 0x110000

# The Hangul syllable block is algorithmic: a syllable in
# U+AC00..U+D7A3 decomposes to the choseong, jungseong and (when
# present) jongseong jamo by arithmetic, and three jamo compose back.
# The syllables therefore carry no table row.
SBASE = 0xAC00
LBASE = 0x1100
VBASE = 0x1161
TBASE = 0x11A7
LCOUNT = 19
VCOUNT = 21
TCOUNT = 28
NCOUNT = VCOUNT * TCOUNT
SCOUNT = LCOUNT * NCOUNT

# The two name families whose names are the code point in hex: the CJK
# unified ideographs and the CJK compatibility ideographs. They are
# contiguous runs (the unified block has a hole between the extension A
# and the main block), so the runtime builds the name from the prefix
# and the code point instead of storing it.
HEX_NAME_PREFIXES = ("CJK UNIFIED IDEOGRAPH-", "CJK COMPATIBILITY IDEOGRAPH-")

# The compatibility decomposition tags, in the order the runtime numbers
# them. The number is what the generated table stores, so the runtime
# can print the tag CPython prints.
COMPAT_TAGS = (
    "<font>",
    "<noBreak>",
    "<initial>",
    "<medial>",
    "<final>",
    "<isolated>",
    "<circle>",
    "<super>",
    "<sub>",
    "<vertical>",
    "<wide>",
    "<narrow>",
    "<small>",
    "<square>",
    "<fraction>",
    "<compat>",
)

# The formal name aliases `unicodedata.lookup` accepts beyond the stored
# names (the ISO 6429 control names, the correction names, the alternate
# format names, the abbreviations and the figments). Each entry is
# checked against the oracle at generation time, so the set cannot drift
# from CPython's own lookup table.
ALIASES = (
    (0x0000, "NULL"),
    (0x0000, "NUL"),
    (0x0001, "START OF HEADING"),
    (0x0001, "SOH"),
    (0x0002, "START OF TEXT"),
    (0x0002, "STX"),
    (0x0003, "END OF TEXT"),
    (0x0003, "ETX"),
    (0x0004, "END OF TRANSMISSION"),
    (0x0004, "EOT"),
    (0x0005, "ENQUIRY"),
    (0x0005, "ENQ"),
    (0x0006, "ACKNOWLEDGE"),
    (0x0006, "ACK"),
    (0x0007, "ALERT"),
    (0x0007, "BEL"),
    (0x0008, "BACKSPACE"),
    (0x0008, "BS"),
    (0x0009, "CHARACTER TABULATION"),
    (0x0009, "HORIZONTAL TABULATION"),
    (0x0009, "HT"),
    (0x0009, "TAB"),
    (0x000A, "LINE FEED"),
    (0x000A, "NEW LINE"),
    (0x000A, "END OF LINE"),
    (0x000A, "LF"),
    (0x000A, "NL"),
    (0x000A, "EOL"),
    (0x000B, "LINE TABULATION"),
    (0x000B, "VERTICAL TABULATION"),
    (0x000B, "VT"),
    (0x000C, "FORM FEED"),
    (0x000C, "FF"),
    (0x000D, "CARRIAGE RETURN"),
    (0x000D, "CR"),
    (0x000E, "SHIFT OUT"),
    (0x000E, "LOCKING-SHIFT ONE"),
    (0x000E, "SO"),
    (0x000F, "SHIFT IN"),
    (0x000F, "LOCKING-SHIFT ZERO"),
    (0x000F, "SI"),
    (0x0010, "DATA LINK ESCAPE"),
    (0x0010, "DLE"),
    (0x0011, "DEVICE CONTROL ONE"),
    (0x0011, "DC1"),
    (0x0012, "DEVICE CONTROL TWO"),
    (0x0012, "DC2"),
    (0x0013, "DEVICE CONTROL THREE"),
    (0x0013, "DC3"),
    (0x0014, "DEVICE CONTROL FOUR"),
    (0x0014, "DC4"),
    (0x0015, "NEGATIVE ACKNOWLEDGE"),
    (0x0015, "NAK"),
    (0x0016, "SYNCHRONOUS IDLE"),
    (0x0016, "SYN"),
    (0x0017, "END OF TRANSMISSION BLOCK"),
    (0x0017, "ETB"),
    (0x0018, "CANCEL"),
    (0x0018, "CAN"),
    (0x0019, "END OF MEDIUM"),
    (0x0019, "EOM"),
    (0x0019, "EM"),
    (0x001A, "SUBSTITUTE"),
    (0x001A, "SUB"),
    (0x001B, "ESCAPE"),
    (0x001B, "ESC"),
    (0x001C, "INFORMATION SEPARATOR FOUR"),
    (0x001C, "FILE SEPARATOR"),
    (0x001C, "FS"),
    (0x001D, "INFORMATION SEPARATOR THREE"),
    (0x001D, "GROUP SEPARATOR"),
    (0x001D, "GS"),
    (0x001E, "INFORMATION SEPARATOR TWO"),
    (0x001E, "RECORD SEPARATOR"),
    (0x001E, "RS"),
    (0x001F, "INFORMATION SEPARATOR ONE"),
    (0x001F, "UNIT SEPARATOR"),
    (0x001F, "US"),
    (0x0020, "SP"),
    (0x007F, "DELETE"),
    (0x007F, "DEL"),
    (0x0080, "PADDING CHARACTER"),
    (0x0080, "PAD"),
    (0x0081, "HIGH OCTET PRESET"),
    (0x0081, "HOP"),
    (0x0082, "BREAK PERMITTED HERE"),
    (0x0082, "BPH"),
    (0x0083, "NO BREAK HERE"),
    (0x0083, "NBH"),
    (0x0084, "INDEX"),
    (0x0084, "IND"),
    (0x0085, "NEXT LINE"),
    (0x0085, "NEL"),
    (0x0086, "START OF SELECTED AREA"),
    (0x0086, "SSA"),
    (0x0087, "END OF SELECTED AREA"),
    (0x0087, "ESA"),
    (0x0088, "CHARACTER TABULATION SET"),
    (0x0088, "HORIZONTAL TABULATION SET"),
    (0x0088, "HTS"),
    (0x0089, "CHARACTER TABULATION WITH JUSTIFICATION"),
    (0x0089, "HORIZONTAL TABULATION WITH JUSTIFICATION"),
    (0x0089, "HTJ"),
    (0x008A, "LINE TABULATION SET"),
    (0x008A, "VERTICAL TABULATION SET"),
    (0x008A, "VTS"),
    (0x008B, "PARTIAL LINE FORWARD"),
    (0x008B, "PARTIAL LINE DOWN"),
    (0x008B, "PLD"),
    (0x008C, "PARTIAL LINE BACKWARD"),
    (0x008C, "PARTIAL LINE UP"),
    (0x008C, "PLU"),
    (0x008D, "REVERSE LINE FEED"),
    (0x008D, "REVERSE INDEX"),
    (0x008D, "RI"),
    (0x008E, "SINGLE SHIFT TWO"),
    (0x008E, "SINGLE-SHIFT-2"),
    (0x008E, "SS2"),
    (0x008F, "SINGLE SHIFT THREE"),
    (0x008F, "SINGLE-SHIFT-3"),
    (0x008F, "SS3"),
    (0x0090, "DEVICE CONTROL STRING"),
    (0x0090, "DCS"),
    (0x0091, "PRIVATE USE ONE"),
    (0x0091, "PRIVATE USE-1"),
    (0x0091, "PU1"),
    (0x0092, "PRIVATE USE TWO"),
    (0x0092, "PRIVATE USE-2"),
    (0x0092, "PU2"),
    (0x0093, "SET TRANSMIT STATE"),
    (0x0093, "STS"),
    (0x0094, "CANCEL CHARACTER"),
    (0x0094, "CCH"),
    (0x0095, "MESSAGE WAITING"),
    (0x0095, "MW"),
    (0x0096, "START OF GUARDED AREA"),
    (0x0096, "START OF PROTECTED AREA"),
    (0x0096, "SPA"),
    (0x0097, "END OF GUARDED AREA"),
    (0x0097, "END OF PROTECTED AREA"),
    (0x0097, "EPA"),
    (0x0098, "START OF STRING"),
    (0x0098, "SOS"),
    (0x0099, "SINGLE GRAPHIC CHARACTER INTRODUCER"),
    (0x0099, "SGC"),
    (0x009A, "SINGLE CHARACTER INTRODUCER"),
    (0x009A, "SCI"),
    (0x009B, "CONTROL SEQUENCE INTRODUCER"),
    (0x009B, "CSI"),
    (0x009C, "STRING TERMINATOR"),
    (0x009C, "ST"),
    (0x009D, "OPERATING SYSTEM COMMAND"),
    (0x009D, "OSC"),
    (0x009E, "PRIVACY MESSAGE"),
    (0x009E, "PM"),
    (0x009F, "APPLICATION PROGRAM COMMAND"),
    (0x009F, "APC"),
    (0x00A0, "NBSP"),
    (0x00AD, "SHY"),
    (0x01A2, "LATIN CAPITAL LETTER GHA"),
    (0x01A3, "LATIN SMALL LETTER GHA"),
    (0x034F, "CGJ"),
    (0x0616, "ARABIC SMALL HIGH LIGATURE ALEF WITH YEH BARREE"),
    (0x061C, "ALM"),
    (0x0709, "SYRIAC SUBLINEAR COLON SKEWED LEFT"),
    (0x0CDE, "KANNADA LETTER LLLA"),
    (0x0E9D, "LAO LETTER FO FON"),
    (0x0E9F, "LAO LETTER FO FAY"),
    (0x0EA3, "LAO LETTER RO"),
    (0x0EA5, "LAO LETTER LO"),
    (0x0FD0, "TIBETAN MARK BKA- SHOG GI MGO RGYAN"),
    (0x11EC, "HANGUL JONGSEONG YESIEUNG-KIYEOK"),
    (0x11ED, "HANGUL JONGSEONG YESIEUNG-SSANGKIYEOK"),
    (0x11EE, "HANGUL JONGSEONG SSANGYESIEUNG"),
    (0x11EF, "HANGUL JONGSEONG YESIEUNG-KHIEUKH"),
    (0x180B, "FVS1"),
    (0x180C, "FVS2"),
    (0x180D, "FVS3"),
    (0x180E, "MVS"),
    (0x180F, "FVS4"),
    (0x1BBD, "SUNDANESE LETTER ARCHAIC I"),
    (0x200B, "ZWSP"),
    (0x200C, "ZWNJ"),
    (0x200D, "ZWJ"),
    (0x200E, "LRM"),
    (0x200F, "RLM"),
    (0x202A, "LRE"),
    (0x202B, "RLE"),
    (0x202C, "PDF"),
    (0x202D, "LRO"),
    (0x202E, "RLO"),
    (0x202F, "NNBSP"),
    (0x205F, "MMSP"),
    (0x2060, "WJ"),
    (0x2066, "LRI"),
    (0x2067, "RLI"),
    (0x2068, "FSI"),
    (0x2069, "PDI"),
    (0x2118, "WEIERSTRASS ELLIPTIC FUNCTION"),
    (0x2448, "MICR ON US SYMBOL"),
    (0x2449, "MICR DASH SYMBOL"),
    (0x2B7A, "LEFTWARDS TRIANGLE-HEADED ARROW WITH DOUBLE VERTICAL STROKE"),
    (0x2B7C, "RIGHTWARDS TRIANGLE-HEADED ARROW WITH DOUBLE VERTICAL STROKE"),
    (0xA015, "YI SYLLABLE ITERATION MARK"),
    (0xAA6E, "MYANMAR LETTER KHAMTI LLA"),
    (0xFE00, "VS1"),
    (0xFE01, "VS2"),
    (0xFE02, "VS3"),
    (0xFE03, "VS4"),
    (0xFE04, "VS5"),
    (0xFE05, "VS6"),
    (0xFE06, "VS7"),
    (0xFE07, "VS8"),
    (0xFE08, "VS9"),
    (0xFE09, "VS10"),
    (0xFE0A, "VS11"),
    (0xFE0B, "VS12"),
    (0xFE0C, "VS13"),
    (0xFE0D, "VS14"),
    (0xFE0E, "VS15"),
    (0xFE0F, "VS16"),
    (0xFE18, "PRESENTATION FORM FOR VERTICAL RIGHT WHITE LENTICULAR BRACKET"),
    (0xFEFF, "BYTE ORDER MARK"),
    (0xFEFF, "BOM"),
    (0xFEFF, "ZWNBSP"),
    (0x122D4, "CUNEIFORM SIGN NU11 TENU"),
    (0x122D5, "CUNEIFORM SIGN NU11 OVER NU11 BUR OVER BUR"),
    (0x12327, "CUNEIFORM SIGN KALAM"),
    (0x1680B, "BAMUM LETTER PHASE-A MAEMGBIEE"),
    (0x16E56, "MEDEFAIDRIN CAPITAL LETTER H"),
    (0x16E57, "MEDEFAIDRIN CAPITAL LETTER NG"),
    (0x16E76, "MEDEFAIDRIN SMALL LETTER H"),
    (0x16E77, "MEDEFAIDRIN SMALL LETTER NG"),
    (0x1B001, "HENTAIGANA LETTER E-1"),
    (0x1D0C5, "BYZANTINE MUSICAL SYMBOL FTHORA SKLIRON CHROMA VASIS"),
    (0x1E899, "MENDE KIKAKUI SYLLABLE M172 MBO"),
    (0x1E89A, "MENDE KIKAKUI SYLLABLE M174 MBOO"),
    (0xE0100, "VS17"),
    (0xE0101, "VS18"),
    (0xE0102, "VS19"),
    (0xE0103, "VS20"),
    (0xE0104, "VS21"),
    (0xE0105, "VS22"),
    (0xE0106, "VS23"),
    (0xE0107, "VS24"),
    (0xE0108, "VS25"),
    (0xE0109, "VS26"),
    (0xE010A, "VS27"),
    (0xE010B, "VS28"),
    (0xE010C, "VS29"),
    (0xE010D, "VS30"),
    (0xE010E, "VS31"),
    (0xE010F, "VS32"),
    (0xE0110, "VS33"),
    (0xE0111, "VS34"),
    (0xE0112, "VS35"),
    (0xE0113, "VS36"),
    (0xE0114, "VS37"),
    (0xE0115, "VS38"),
    (0xE0116, "VS39"),
    (0xE0117, "VS40"),
    (0xE0118, "VS41"),
    (0xE0119, "VS42"),
    (0xE011A, "VS43"),
    (0xE011B, "VS44"),
    (0xE011C, "VS45"),
    (0xE011D, "VS46"),
    (0xE011E, "VS47"),
    (0xE011F, "VS48"),
    (0xE0120, "VS49"),
    (0xE0121, "VS50"),
    (0xE0122, "VS51"),
    (0xE0123, "VS52"),
    (0xE0124, "VS53"),
    (0xE0125, "VS54"),
    (0xE0126, "VS55"),
    (0xE0127, "VS56"),
    (0xE0128, "VS57"),
    (0xE0129, "VS58"),
    (0xE012A, "VS59"),
    (0xE012B, "VS60"),
    (0xE012C, "VS61"),
    (0xE012D, "VS62"),
    (0xE012E, "VS63"),
    (0xE012F, "VS64"),
    (0xE0130, "VS65"),
    (0xE0131, "VS66"),
    (0xE0132, "VS67"),
    (0xE0133, "VS68"),
    (0xE0134, "VS69"),
    (0xE0135, "VS70"),
    (0xE0136, "VS71"),
    (0xE0137, "VS72"),
    (0xE0138, "VS73"),
    (0xE0139, "VS74"),
    (0xE013A, "VS75"),
    (0xE013B, "VS76"),
    (0xE013C, "VS77"),
    (0xE013D, "VS78"),
    (0xE013E, "VS79"),
    (0xE013F, "VS80"),
    (0xE0140, "VS81"),
    (0xE0141, "VS82"),
    (0xE0142, "VS83"),
    (0xE0143, "VS84"),
    (0xE0144, "VS85"),
    (0xE0145, "VS86"),
    (0xE0146, "VS87"),
    (0xE0147, "VS88"),
    (0xE0148, "VS89"),
    (0xE0149, "VS90"),
    (0xE014A, "VS91"),
    (0xE014B, "VS92"),
    (0xE014C, "VS93"),
    (0xE014D, "VS94"),
    (0xE014E, "VS95"),
    (0xE014F, "VS96"),
    (0xE0150, "VS97"),
    (0xE0151, "VS98"),
    (0xE0152, "VS99"),
    (0xE0153, "VS100"),
    (0xE0154, "VS101"),
    (0xE0155, "VS102"),
    (0xE0156, "VS103"),
    (0xE0157, "VS104"),
    (0xE0158, "VS105"),
    (0xE0159, "VS106"),
    (0xE015A, "VS107"),
    (0xE015B, "VS108"),
    (0xE015C, "VS109"),
    (0xE015D, "VS110"),
    (0xE015E, "VS111"),
    (0xE015F, "VS112"),
    (0xE0160, "VS113"),
    (0xE0161, "VS114"),
    (0xE0162, "VS115"),
    (0xE0163, "VS116"),
    (0xE0164, "VS117"),
    (0xE0165, "VS118"),
    (0xE0166, "VS119"),
    (0xE0167, "VS120"),
    (0xE0168, "VS121"),
    (0xE0169, "VS122"),
    (0xE016A, "VS123"),
    (0xE016B, "VS124"),
    (0xE016C, "VS125"),
    (0xE016D, "VS126"),
    (0xE016E, "VS127"),
    (0xE016F, "VS128"),
    (0xE0170, "VS129"),
    (0xE0171, "VS130"),
    (0xE0172, "VS131"),
    (0xE0173, "VS132"),
    (0xE0174, "VS133"),
    (0xE0175, "VS134"),
    (0xE0176, "VS135"),
    (0xE0177, "VS136"),
    (0xE0178, "VS137"),
    (0xE0179, "VS138"),
    (0xE017A, "VS139"),
    (0xE017B, "VS140"),
    (0xE017C, "VS141"),
    (0xE017D, "VS142"),
    (0xE017E, "VS143"),
    (0xE017F, "VS144"),
    (0xE0180, "VS145"),
    (0xE0181, "VS146"),
    (0xE0182, "VS147"),
    (0xE0183, "VS148"),
    (0xE0184, "VS149"),
    (0xE0185, "VS150"),
    (0xE0186, "VS151"),
    (0xE0187, "VS152"),
    (0xE0188, "VS153"),
    (0xE0189, "VS154"),
    (0xE018A, "VS155"),
    (0xE018B, "VS156"),
    (0xE018C, "VS157"),
    (0xE018D, "VS158"),
    (0xE018E, "VS159"),
    (0xE018F, "VS160"),
    (0xE0190, "VS161"),
    (0xE0191, "VS162"),
    (0xE0192, "VS163"),
    (0xE0193, "VS164"),
    (0xE0194, "VS165"),
    (0xE0195, "VS166"),
    (0xE0196, "VS167"),
    (0xE0197, "VS168"),
    (0xE0198, "VS169"),
    (0xE0199, "VS170"),
    (0xE019A, "VS171"),
    (0xE019B, "VS172"),
    (0xE019C, "VS173"),
    (0xE019D, "VS174"),
    (0xE019E, "VS175"),
    (0xE019F, "VS176"),
    (0xE01A0, "VS177"),
    (0xE01A1, "VS178"),
    (0xE01A2, "VS179"),
    (0xE01A3, "VS180"),
    (0xE01A4, "VS181"),
    (0xE01A5, "VS182"),
    (0xE01A6, "VS183"),
    (0xE01A7, "VS184"),
    (0xE01A8, "VS185"),
    (0xE01A9, "VS186"),
    (0xE01AA, "VS187"),
    (0xE01AB, "VS188"),
    (0xE01AC, "VS189"),
    (0xE01AD, "VS190"),
    (0xE01AE, "VS191"),
    (0xE01AF, "VS192"),
    (0xE01B0, "VS193"),
    (0xE01B1, "VS194"),
    (0xE01B2, "VS195"),
    (0xE01B3, "VS196"),
    (0xE01B4, "VS197"),
    (0xE01B5, "VS198"),
    (0xE01B6, "VS199"),
    (0xE01B7, "VS200"),
    (0xE01B8, "VS201"),
    (0xE01B9, "VS202"),
    (0xE01BA, "VS203"),
    (0xE01BB, "VS204"),
    (0xE01BC, "VS205"),
    (0xE01BD, "VS206"),
    (0xE01BE, "VS207"),
    (0xE01BF, "VS208"),
    (0xE01C0, "VS209"),
    (0xE01C1, "VS210"),
    (0xE01C2, "VS211"),
    (0xE01C3, "VS212"),
    (0xE01C4, "VS213"),
    (0xE01C5, "VS214"),
    (0xE01C6, "VS215"),
    (0xE01C7, "VS216"),
    (0xE01C8, "VS217"),
    (0xE01C9, "VS218"),
    (0xE01CA, "VS219"),
    (0xE01CB, "VS220"),
    (0xE01CC, "VS221"),
    (0xE01CD, "VS222"),
    (0xE01CE, "VS223"),
    (0xE01CF, "VS224"),
    (0xE01D0, "VS225"),
    (0xE01D1, "VS226"),
    (0xE01D2, "VS227"),
    (0xE01D3, "VS228"),
    (0xE01D4, "VS229"),
    (0xE01D5, "VS230"),
    (0xE01D6, "VS231"),
    (0xE01D7, "VS232"),
    (0xE01D8, "VS233"),
    (0xE01D9, "VS234"),
    (0xE01DA, "VS235"),
    (0xE01DB, "VS236"),
    (0xE01DC, "VS237"),
    (0xE01DD, "VS238"),
    (0xE01DE, "VS239"),
    (0xE01DF, "VS240"),
    (0xE01E0, "VS241"),
    (0xE01E1, "VS242"),
    (0xE01E2, "VS243"),
    (0xE01E3, "VS244"),
    (0xE01E4, "VS245"),
    (0xE01E5, "VS246"),
    (0xE01E6, "VS247"),
    (0xE01E7, "VS248"),
    (0xE01E8, "VS249"),
    (0xE01E9, "VS250"),
    (0xE01EA, "VS251"),
    (0xE01EB, "VS252"),
    (0xE01EC, "VS253"),
    (0xE01ED, "VS254"),
    (0xE01EE, "VS255"),
    (0xE01EF, "VS256"),
)

HEADER = """/* Generated Unicode tables (Unicode {version}, via CPython 3.14's unicodedata).

   The general category, the canonical combining class, the bidirectional
   class, the east asian width and the mirrored flag are stored as runs
   over the code-point space: each run is a contiguous span with one
   value. The decimal, digit and numeric values, the decompositions, the
   names and the composition pairs are stored as sorted records.

   The Hangul syllable block and the two CJK ideograph name families are
   algorithmic (the runtime builds them by arithmetic), so they carry no
   table row.

   Regenerate with scripts/gen_unicode_tables.py; the version is stamped
   here and checked by the generator's self-test. Included by
   ps_unicode.c; it compiles only through that include. */

#include <stdint.h>

#define PS_UNICODE_VERSION "{version}"

#define PS_UNI_SBASE 0xAC00u
#define PS_UNI_LBASE 0x1100u
#define PS_UNI_VBASE 0x1161u
#define PS_UNI_TBASE 0x11A7u
#define PS_UNI_LCOUNT 19u
#define PS_UNI_VCOUNT 21u
#define PS_UNI_TCOUNT 28u
#define PS_UNI_NCOUNT (PS_UNI_VCOUNT * PS_UNI_TCOUNT)
#define PS_UNI_SCOUNT (PS_UNI_LCOUNT * PS_UNI_NCOUNT)

typedef struct {{ uint32_t lo; uint32_t hi; uint16_t val; }} PsUniRun;
typedef struct {{ uint32_t cp; int32_t val; }} PsUniNum;
typedef struct {{ uint32_t cp; int64_t num; int32_t den; }} PsUniVal;
typedef struct {{ uint32_t cp; uint32_t off; uint32_t len; }} PsUniStr;
typedef struct {{ uint32_t cp; uint32_t off; uint16_t len; uint8_t compat; }} PsUniDec;
typedef struct {{ uint32_t cp; uint32_t soff; uint16_t slen; uint16_t pidx; }} PsUniName;
typedef struct {{ uint32_t off; uint16_t len; }} PsUniPrefix;
typedef struct {{ uint32_t cp; uint32_t off; uint16_t len; }} PsUniAlias;
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
    """The general category as a small integer: the upper-case first
    letter (0-25) in the high part, the lower-case second letter (1-26)
    in the low one, packed base 27. `Cn` is 0x00, the unassigned
    default, and the only code whose low part is 0."""
    if category == "Cn":
        return 0x00
    return (ord(category[0]) - ord("A")) * 27 + (ord(category[1]) - ord("a") + 1)


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
    #
    # The numeric value is stored as an exact rational (the numerator and
    # the denominator as integers): the Unicode values include repeating
    # fractions (one seventh, one third) and values above 2^53, so a
    # scaled integer would lose them. The runtime divides the two as
    # doubles, which reproduces the oracle's double exactly.
    decimal: list[tuple[int, int]] = []
    digit: list[tuple[int, int]] = []
    numeric: list[tuple[int, int, int]] = []
    for cp in range(MAX_CP):
        ch = chr(cp)
        try:
            value = unicodedata.numeric(ch)
        except ValueError:
            pass
        else:
            fraction = Fraction(value).limit_denominator(1000000)
            assert fraction.numerator / fraction.denominator == value, (hex(cp), value)
            numeric.append((cp, fraction.numerator, fraction.denominator))
        if ch.isdecimal():
            decimal.append((cp, unicodedata.digit(ch)))
        if ch.isdigit():
            digit.append((cp, unicodedata.digit(ch)))

    # The decompositions: the canonical ones and the compatibility ones,
    # as the code point sequences they expand to. A compatibility
    # decomposition is tagged: the runtime's NFKC/NFKD apply it, NFD/NFC
    # do not.
    decomp_pool = bytearray()
    decomp: list[tuple[int, int, int, int]] = []  # cp, offset, length, compat
    canon_pairs: dict[tuple[int, int], int] = {}
    for cp in range(MAX_CP):
        if SBASE <= cp < SBASE + SCOUNT:
            continue  # Hangul is algorithmic, never a table row
        raw = unicodedata.decomposition(chr(cp))
        if not raw:
            continue
        parts = raw.split()
        compat = 0
        if parts[0].startswith("<"):
            compat = COMPAT_TAGS.index(parts[0]) + 1
            parts = parts[1:]
        seq = [int(x, 16) for x in parts]
        off = len(decomp_pool) // 4
        for value in seq:
            decomp_pool += value.to_bytes(4, "big")
        decomp.append((cp, off, len(seq), compat))
        if not compat and len(seq) == 2:
            canon_pairs[(seq[0], seq[1])] = cp

    # The names. CPython's `name` is the Unicode name, and `lookup`
    # accepts the name and the algorithmic aliases ("NULL", "BYTE ORDER
    # MARK", the Hangul syllable names). The Hangul syllable names are
    # algorithmic (they spell out the chosen jamo names), so they are
    # generated at run time, not stored. The two CJK ideograph families
    # are algorithmic too: the name is the prefix and the code point in
    # hex.
    #
    # The stored names are prefix-compressed: the named code points fall
    # into contiguous runs, and inside a run the names share a long
    # common prefix ("LATIN CAPITAL LETTER A" ... "LATIN CAPITAL LETTER
    # Z"). The pool holds the shared prefix once per run, then the
    # per-code-point suffix, and the index row carries the prefix
    # length, so the runtime rebuilds the name as prefix + suffix.
    named_cps = [
        cp
        for cp in range(MAX_CP)
        if not (SBASE <= cp < SBASE + SCOUNT)
        and unicodedata.name(chr(cp), None)
        and not unicodedata.name(chr(cp)).startswith(HEX_NAME_PREFIXES)
    ]
    name_runs: list[tuple[int, int]] = []
    start = named_cps[0]
    prev = named_cps[0]
    for cp in named_cps[1:]:
        if cp != prev + 1:
            name_runs.append((start, prev))
            start = cp
        prev = cp
    name_runs.append((start, prev))

    name_pool = bytearray()
    names: list[tuple[int, int, int, int]] = []  # cp, suffix offset, suffix len, prefix index
    prefixes: list[tuple[int, int]] = []  # prefix offset, prefix length
    for lo, hi in name_runs:
        run_names = [unicodedata.name(chr(cp)) for cp in range(lo, hi + 1)]
        shared = run_names[0]
        for text in run_names[1:]:
            while not text.startswith(shared):
                shared = shared[:-1]
        prefixes.append((len(name_pool), len(shared)))
        name_pool += shared.encode("ascii")
        prefix_index = len(prefixes) - 1
        for index, text in enumerate(run_names):
            suffix = text[len(shared) :].encode("ascii")
            names.append((lo + index, len(name_pool), len(suffix), prefix_index))
            name_pool += suffix

    # The composition pairs: exactly the (starter, combining) pairs where
    # the pair composes under CPython's own NFC (the composition
    # exclusion list falls out of the oracle).
    comp: list[tuple[int, int, int]] = []
    for (a, b), out in sorted(canon_pairs.items()):
        if unicodedata.normalize("NFC", chr(a) + chr(b)) == chr(out):
            comp.append((a, b, out))

    # The CJK ideograph name families: the code points whose name is the
    # family prefix and the code point in hex. The ranges are read off
    # the oracle, so they cannot drift from CPython's own names.
    cjk_runs: list[tuple[int, int, int]] = []  # lo, hi, family index
    for family, prefix in enumerate(HEX_NAME_PREFIXES):
        members = [cp for cp in range(MAX_CP) if unicodedata.name(chr(cp), None) == f"{prefix}{cp:04X}"]
        start = prev = members[0]
        for cp in members[1:]:
            if cp != prev + 1:
                cjk_runs.append((start, prev, family))
                start = cp
            prev = cp
        cjk_runs.append((start, prev, family))
    cjk_runs.sort()

    # The formal name aliases: the oracle checks every entry, so the set
    # cannot drift from CPython's own lookup table.
    alias_pool = bytearray()
    alias_rows: list[tuple[int, int, int]] = []  # cp, offset, length
    for cp, name in sorted(ALIASES, key=lambda row: row[1]):
        data = name.encode("ascii")
        assert unicodedata.lookup(name) == chr(cp), name
        alias_rows.append((cp, len(alias_pool), len(data)))
        alias_pool += data

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
        "prefixes": prefixes,
        "name_pool": bytes(name_pool),
        "comp": comp,
        "ccc": ccc,
        "cats": cats,
        "aliases": alias_rows,
        "alias_pool": bytes(alias_pool),
        "cjk_runs": cjk_runs,
    }


def emit_runs(out: list[str], name: str, rows: list[tuple[int, int, int]]) -> None:
    out.append(f"static const PsUniRun {name}[] = {{")
    for i in range(0, len(rows), 4):
        chunk = rows[i : i + 4]
        out.append("    " + ",".join("{0x%06xu,0x%06xu,%uu}" % r for r in chunk) + ",")
    out.append("};")
    out.append(f"#define {name.upper()}_N {len(rows)}")
    out.append("")


def emit_nums(out: list[str], name: str, rows: list[tuple[int, int]]) -> None:
    out.append(f"static const PsUniNum {name}[] = {{")
    for i in range(0, len(rows), 4):
        chunk = rows[i : i + 4]
        out.append("    " + ",".join("{0x%06xu,%d}" % r for r in chunk) + ",")
    out.append("};")
    out.append(f"#define {name.upper()}_N {len(rows)}")
    out.append("")


def emit_vals(out: list[str], name: str, rows: list[tuple[int, int, int]]) -> None:
    out.append(f"static const PsUniVal {name}[] = {{")
    for i in range(0, len(rows), 2):
        chunk = rows[i : i + 2]
        cells = ["{0x%06xu,%dLL,%d}" % r for r in chunk]
        out.append("    " + ",".join(cells) + ",")
    out.append("};")
    out.append(f"#define {name.upper()}_N {len(rows)}")
    out.append("")


def emit_names(out: list[str], name: str, rows: list[tuple[int, int, int, int]]) -> None:
    out.append(f"static const PsUniName {name}[] = {{")
    for i in range(0, len(rows), 4):
        chunk = rows[i : i + 4]
        out.append("    " + ",".join("{0x%06xu,%uu,%uu,%uu}" % r for r in chunk) + ",")
    out.append("};")
    out.append(f"#define {name.upper()}_N {len(rows)}")
    out.append("")


def emit_prefixes(out: list[str], name: str, rows: list[tuple[int, int]]) -> None:
    out.append(f"static const PsUniPrefix {name}[] = {{")
    for i in range(0, len(rows), 4):
        chunk = rows[i : i + 4]
        out.append("    " + ",".join("{%uu,%uu}" % r for r in chunk) + ",")
    out.append("};")
    out.append(f"#define {name.upper()}_N {len(rows)}")
    out.append("")


def emit_aliases(out: list[str], name: str, rows: list[tuple[int, int, int]]) -> None:
    out.append(f"static const PsUniAlias {name}[] = {{")
    for i in range(0, len(rows), 4):
        chunk = rows[i : i + 4]
        out.append("    " + ",".join("{0x%06xu,%uu,%uu}" % r for r in chunk) + ",")
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


def emit_decs(out: list[str], name: str, rows: list[tuple[int, int, int, int]]) -> None:
    out.append(f"static const PsUniDec {name}[] = {{")
    for i in range(0, len(rows), 4):
        chunk = rows[i : i + 4]
        out.append("    " + ",".join("{0x%06xu,%uu,%uu,%uu}" % r for r in chunk) + ",")
    out.append("};")
    out.append(f"#define {name.upper()}_N {len(rows)}")
    out.append("")


def emit_pool(out: list[str], name: str, pool: bytes) -> None:
    # The pool bytes are ASCII (the Unicode names) or big-endian code
    # points (the decompositions). A printable ASCII byte other than the
    # quote and the backslash is emitted literally, which keeps the
    # generated file readable and a third of the size; the rest goes out
    # as a hex escape. A hex escape is always terminated by the next
    # non-hex character, so the emitter breaks the line after every
    # escape to keep the boundary unambiguous.
    out.append(f"static const char {name}[] =")
    line: list[str] = []
    escaped = False
    for byte in pool:
        if 0x20 <= byte < 0x7F and byte not in (0x22, 0x5C):
            # A hex escape followed by a hex digit would extend the
            # escape (C reads "\x61f" as one character), so a literal
            # character after an escape closes the string literal and
            # opens a new one.
            if escaped:
                out.append('    "%s"' % "".join(line))
                line = []
                escaped = False
            line.append(chr(byte))
        else:
            line.append("\\x%02x" % byte)
            escaped = True
    out.append('    "%s"' % "".join(line))
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
    emit_vals(out, "ps_uni_numeric", data["numeric"])  # type: ignore[arg-type]

    emit_decs(out, "ps_uni_decomp", data["decomp"])  # type: ignore[arg-type]
    emit_pool(out, "ps_uni_decomp_pool", data["decomp_pool"])  # type: ignore[arg-type]

    emit_names(out, "ps_uni_name", data["names"])  # type: ignore[arg-type]
    emit_prefixes(out, "ps_uni_name_prefix", data["prefixes"])  # type: ignore[arg-type]
    emit_pool(out, "ps_uni_name_pool", data["name_pool"])  # type: ignore[arg-type]

    out.append("static const PsUniComp ps_uni_comp[] = {")
    comp = data["comp"]  # type: ignore[assignment]
    for i in range(0, len(comp), 4):
        chunk = comp[i : i + 4]
        out.append("    " + ",".join("{0x%06xu,0x%06xu,0x%06xu}" % r for r in chunk) + ",")
    out.append("};")
    out.append(f"#define PS_UNI_COMP_N {len(comp)}")
    out.append("")

    emit_aliases(out, "ps_uni_alias", data["aliases"])  # type: ignore[arg-type]
    emit_pool(out, "ps_uni_alias_pool", data["alias_pool"])  # type: ignore[arg-type]

    # The CJK ideograph name ranges: the code points whose name is the
    # family prefix and the code point in hex.
    out.append("typedef struct { uint32_t lo; uint32_t hi; uint8_t family; } PsUniCjk;")
    out.append("static const PsUniCjk ps_uni_cjk[] = {")
    for lo, hi, family in data["cjk_runs"]:  # type: ignore[union-attr]
        out.append(f"    {{0x{lo:06x}u,0x{hi:06x}u,{family}u}},")
    out.append("};")
    out.append(f"#define PS_UNI_CJK_N {len(data['cjk_runs'])}")  # type: ignore[arg-type]
    out.append("")
    out.append("static const char *const ps_uni_cjk_prefixes[] = {")
    out.append("    " + ",".join('"%s"' % p for p in HEX_NAME_PREFIXES) + ",")
    out.append("};")
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
    for cp, off, length, compat in data["decomp"]:  # type: ignore[union-attr]
        raw = unicodedata.decomposition(chr(cp))
        parts = raw.split()
        want_compat = 0
        if parts[0].startswith("<"):
            want_compat = COMPAT_TAGS.index(parts[0]) + 1
            parts = parts[1:]
        want = [int(x, 16) for x in parts]
        got = [int.from_bytes(pool[(off + i) * 4 : (off + i + 1) * 4], "big") for i in range(length)]
        assert got == want, (hex(cp), got, want)
        assert compat == want_compat, (hex(cp), compat, want_compat)

    # The names must reproduce the exact strings: the prefix at the
    # prefix offset plus the suffix at the prefix offset plus the prefix
    # length, which is where the runtime reads them from.
    name_pool = data["name_pool"]  # type: ignore[assignment]
    for cp, soff, slen, pidx in data["names"]:  # type: ignore[union-attr]
        want = unicodedata.name(chr(cp))
        poff, plen = data["prefixes"][pidx]  # type: ignore[index]
        prefix = name_pool[poff : poff + plen].decode("ascii")
        suffix = name_pool[soff : soff + slen].decode("ascii")
        assert prefix + suffix == want, (hex(cp), prefix + suffix, want)

    # The numeric records must reproduce the exact values.
    for cp, num, den in data["numeric"]:  # type: ignore[union-attr]
        want = unicodedata.numeric(chr(cp))
        got = num / den
        assert got == want, (hex(cp), got, want)

    # The normalization round trip: the generated decomposition and
    # composition tables must agree with `unicodedata.normalize` on a
    # randomized sweep over the code points that have a decomposition.
    comp = {(s, c): out for s, c, out in data["comp"]}  # type: ignore[union-attr]
    decomp_map = {cp: (off, ln) for cp, off, ln, _ in data["decomp"]}  # type: ignore[union-attr]

    def hangul_decomp(cp: int) -> list[int] | None:
        """The algorithmic Hangul syllable decomposition, or None."""
        if not (SBASE <= cp < SBASE + SCOUNT):
            return None
        index = cp - SBASE
        out = [LBASE + index // NCOUNT, VBASE + (index % NCOUNT) // TCOUNT]
        tail = index % TCOUNT
        if tail:
            out.append(TBASE + tail)
        return out

    def canon_decomp(cp: int) -> list[int] | None:
        """The canonical decomposition, or None when the code point has
        none or only a compatibility one."""
        hangul = hangul_decomp(cp)
        if hangul is not None:
            return hangul
        raw = unicodedata.decomposition(chr(cp))
        if not raw or raw.startswith("<"):
            return None
        return [int(x, 16) for x in raw.split()]

    def comp_decomp(cp: int) -> list[int] | None:
        """The full decomposition (compatibility included), or None."""
        raw = unicodedata.decomposition(chr(cp))
        if not raw:
            return None
        parts = raw.split()
        if parts[0].startswith("<"):
            parts = parts[1:]
        return [int(x, 16) for x in parts]

    def full_decomp(cp: int, table: dict[int, list[int]]) -> list[int]:
        seq = table.get(cp)
        if seq is None:
            return [cp]
        out: list[int] = []
        for value in seq:
            out.extend(full_decomp(value, table))
        return out

    # The runtime tables, in the shape the generated C encodes them.
    decomp_canon = {cp: seq for cp, seq in ((c, canon_decomp(c)) for c in range(MAX_CP)) if seq}
    decomp_full = {cp: seq for cp, seq in ((c, comp_decomp(c)) for c in range(MAX_CP)) if seq}

    def compose(a: int, b: int) -> int | None:
        # The pair (LV, T) composes to an LVT syllable; the pair (L, V)
        # composes to an LV syllable. The rest come from the table.
        if TBASE < b < TBASE + TCOUNT and SBASE <= a < SBASE + SCOUNT and (a - SBASE) % TCOUNT == 0:
            return a + (b - TBASE)
        if LBASE <= a < LBASE + LCOUNT and VBASE <= b < VBASE + VCOUNT:
            return SBASE + ((a - LBASE) * VCOUNT + (b - VBASE)) * TCOUNT
        return comp.get((a, b))

    def nfc(cps: list[int]) -> list[int]:
        d: list[int] = []
        for cp in cps:
            d.extend(full_decomp(cp, decomp_canon))
        seq = canonical_order(d)
        if not seq:
            return []
        # The reference composition walk: the current starter stays in
        # place, a composable mark replaces it in place, and a class-0
        # code point becomes the next starter.
        res = [seq[0]]
        sp = 0
        starter = seq[0]
        wpos = 1
        last = unicodedata.combining(chr(starter))
        if last != 0:
            last = 256
        for ch in seq[1:]:
            cls = unicodedata.combining(chr(ch))
            composite = compose(starter, ch)
            if composite is not None and (last < cls or last == 0):
                res[sp] = composite
                starter = composite
            else:
                if cls == 0:
                    sp = wpos
                    starter = ch
                last = cls
                res.append(ch)
                wpos += 1
        return res

    def canonical_order(cps: list[int]) -> list[int]:
        # The canonical ordering algorithm: a stable sort of each
        # combining-mark run by combining class.
        res: list[int] = []
        for ch in cps:
            cls = ccc[ch]
            if cls == 0:
                res.append(ch)
            else:
                j = len(res) - 1
                while j >= 0 and ccc[res[j]] > cls:
                    j -= 1
                res.insert(j + 1, ch)
        return res

    rng = random.Random(20261124)
    candidates = [cp for cp in range(MAX_CP) if cp in decomp_map or ccc[cp] != 0]
    for _ in range(3000):
        length = rng.randint(1, 6)
        cps = [rng.choice(candidates) for _ in range(length)]
        text = "".join(chr(cp) for cp in cps)
        assert unicodedata.normalize("NFC", text) == "".join(chr(cp) for cp in nfc(cps))
        assert unicodedata.normalize("NFD", text) == "".join(
            chr(cp) for cp in canonical_order([x for cp in cps for x in full_decomp(cp, decomp_canon)])
        )
        assert unicodedata.normalize("NFKD", text) == "".join(
            chr(cp) for cp in canonical_order([x for cp in cps for x in full_decomp(cp, decomp_full)])
        )


def main() -> int:
    data = build()
    selftest(data)
    TARGET.write_text(render(), encoding="utf-8")
    print(f"wrote {TARGET} (Unicode {unicodedata.unidata_version})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
