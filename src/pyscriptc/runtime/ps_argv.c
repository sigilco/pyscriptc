/* sys.argv: the process argv materialized once as a list[str] (PsSeq of
PsBuf). The emitted main hands argc/argv to ps_argv_init before running user
code; sys.argv reads come back through ps_argv_get. */

#include "ps_rt.h"

#include <string.h>

static PsSeq *ps_argv_seq;

void ps_argv_init(int32_t argc, char **argv) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    for (int32_t i = 0; i < argc; i++) {
        PsBuf b;
        b.len = (int64_t)strlen(argv[i]);
        b.data = ps_alloc(b.len + 1);
        memcpy(b.data, argv[i], (size_t)b.len + 1);
        ps_seq_push(seq, &b);
    }
    ps_argv_seq = seq;
}

PsSeq *ps_argv_get(void) {
    if (ps_argv_seq == NULL) {
        ps_argv_seq = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    }
    return ps_argv_seq;
}
