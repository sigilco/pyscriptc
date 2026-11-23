/* sys.argv: the process argv materialized once as a list[str] (PsSeq of
PsBuf). The emitted main hands argc/argv to ps_argv_init before running user
code; sys.argv reads come back through ps_argv_get.

The OS gives bytes and a str holds strict UTF-8, so each argument is checked
at init. CPython decodes with surrogateescape and carries the bad byte as a
lone surrogate; the code point str has no surrogate, so (S6, #478, divergence
88) an argument that is not valid UTF-8 is a named UnicodeDecodeError raised
at the first read of sys.argv, not a silent replacement. A program that
never reads sys.argv still runs. */

#include "ps_rt.h"

#include <stdio.h>
#include <string.h>

static PsSeq *ps_argv_seq;
static int32_t ps_argv_bad = -1;
static char ps_argv_what[32];

void ps_argv_init(int32_t argc, char **argv) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    for (int32_t i = 0; i < argc; i++) {
        PsBuf b;
        b.len = (int64_t)strlen(argv[i]);
        if (ps_argv_bad < 0) {
            const char *why = NULL;
            if (ps_utf8_first_error(b.len, argv[i], &why) >= 0) {
                ps_argv_bad = i;
                snprintf(ps_argv_what, sizeof ps_argv_what, "sys.argv[%d]", (int)i);
            }
        }
        b.data = ps_alloc(b.len + 1);
        memcpy(b.data, argv[i], (size_t)b.len + 1);
        ps_seq_push(seq, &b);
    }
    ps_argv_seq = seq;
}

PsSeq *ps_argv_get(void) {
    if (ps_argv_bad >= 0) {
        PsBuf bad;
        ps_seq_get(ps_argv_seq, ps_argv_bad, &bad);
        ps_utf8_require(bad.len, bad.data, ps_argv_what);
    }
    if (ps_argv_seq == NULL) {
        ps_argv_seq = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    }
    return ps_argv_seq;
}
