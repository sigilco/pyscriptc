/* argparse: the script subset of the command-line parser.

The parser holds its registered options as a list of encoded spec strings.
Each spec is `dest|action|const|default|flags`: the fields never hold a `|`
because dest is a Python identifier and action and const come from a closed
set. `flags` is the comma-separated list of option spellings, so
`--name,-n` names one option with two spellings.

`parse_args` walks the process argv, matches the option spellings and
writes one value per registered dest into the namespace the compiler
allocated. The namespace holds one slot per dest, in registration order:
a string is a PsBuf (16 bytes), a number and a flag are an i64 (8 bytes).
The kind table tells the runtime how wide each slot is.

Options that take a value read the next argv token, or the text after an
`=` in the same token. A flag is set when its spelling appears. An option
that did not appear keeps its default.
*/

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ps_argv.c keeps the process argv. */
PsSeq *ps_argv_get(void);

enum { PS_ARG_STR = 0, PS_ARG_INT = 1, PS_ARG_BOOL = 2, PS_ARG_FLOAT = 3 };

/* The parser is `{ PsSeq *specs }`, the same shape as the ArgParser class:
   the list field is a pointer to the sequence. */
typedef struct {
    PsSeq *specs;
} PsArgParser;

static void ps_arg_fail(const char *what) { ps_raise_cstr(PS_RUNTIME, what); }

static char *ps_arg_dup(int64_t n, const char *s) {
    char *text = malloc((size_t)n + 1);
    if (text == NULL) {
        abort();
    }
    if (n > 0) {
        memcpy(text, s, (size_t)n);
    }
    text[n] = '\0';
    return text;
}

/* Split `spec` on `|` into at most `max` fields. The buffer is caller-owned. */
static int ps_arg_split(char *spec, char **out, int max) {
    int count = 0;
    char *start = spec;
    for (char *p = spec;; p++) {
        if (*p == '|' || *p == '\0') {
            int last = (*p == '\0');
            *p = '\0';
            if (count < max) {
                out[count] = start;
            }
            count += 1;
            if (last) {
                break;
            }
            start = p + 1;
        }
    }
    return count;
}

/* Does `token` spell one of the comma-separated option names? */
static int ps_arg_matches(const char *flags, int64_t n, const char *token, int64_t tlen) {
    const char *p = flags;
    while (p < flags + n) {
        const char *comma = memchr(p, ',', (size_t)(flags + n - p));
        int64_t len = comma ? (int64_t)(comma - p) : (int64_t)(flags + n - p);
        if (len == tlen && memcmp(p, token, (size_t)tlen) == 0) {
            return 1;
        }
        if (!comma) {
            break;
        }
        p = comma + 1;
    }
    return 0;
}

/* ArgumentParser: an empty parser with an empty spec list. */
void ps_argparse_new(PsArgParser *out) {
    out->specs = ps_seq_new((int64_t)sizeof(PsBuf), 0);
}

/* add_argument: append one encoded spec row. */
void ps_argparse_add(PsArgParser *parser, int64_t sn, const char *spec) {
    PsBuf row;
    row.len = sn;
    row.data = ps_alloc(sn + 1);
    memcpy(row.data, spec, (size_t)sn + 1);
    ps_seq_push(parser->specs, &row);
}

/* The offset of the i-th namespace slot, given the kind table. */
static int64_t ps_arg_offset(PsSeq *kinds, int64_t index) {
    int64_t offset = 0;
    int64_t count = ps_seq_len(kinds);
    for (int64_t i = 0; i < count && i < index; i++) {
        int64_t kind = 0;
        ps_seq_get(kinds, i, &kind);
        offset += (kind == PS_ARG_STR) ? 16 : 8;
    }
    return offset;
}

static void ps_arg_set_str(void *out, int64_t offset, const char *value) {
    PsBuf *slot = (PsBuf *)((char *)out + offset);
    slot->len = (int64_t)strlen(value);
    slot->data = ps_alloc(slot->len + 1);
    memcpy(slot->data, value, (size_t)slot->len + 1);
}

static void ps_arg_set_int(void *out, int64_t offset, const char *value) {
    int64_t *slot = (int64_t *)((char *)out + offset);
    *slot = (int64_t)strtoll(value, NULL, 10);
}

static void ps_arg_set_float(void *out, int64_t offset, const char *value) {
    double *slot = (double *)((char *)out + offset);
    *slot = strtod(value, NULL);
}

/* Write the text into the slot the kind names. */
static void ps_arg_set(void *out, int64_t offset, int64_t kind, const char *value) {
    if (kind == PS_ARG_STR) {
        ps_arg_set_str(out, offset, value);
    } else if (kind == PS_ARG_FLOAT) {
        ps_arg_set_float(out, offset, value);
    } else {
        ps_arg_set_int(out, offset, value);
    }
}

/* parse_args: fill the namespace. The namespace holds one slot per
   registered dest, in registration order. */
void ps_argparse_parse(PsArgParser *parser, PsSeq *kinds, void *out) {
    PsSeq *specs = parser->specs;
    PsSeq *kind_seq = kinds;
    int64_t nspecs = ps_seq_len(specs);
    PsSeq *argv = ps_argv_get();
    int64_t nargv = ps_seq_len(argv);

    /* The defaults land first, so an option that does not appear keeps them. */
    for (int64_t i = 0; i < nspecs; i++) {
        PsBuf row;
        ps_seq_get(specs, i, &row);
        char *text = ps_arg_dup(row.len, row.data);
        char *parts[5];
        ps_arg_split(text, parts, 5);
        int64_t kind = 0;
        if (i < ps_seq_len(kind_seq)) {
            ps_seq_get(kind_seq, i, &kind);
        }
        int64_t offset = ps_arg_offset(kind_seq, i);
        if (kind == PS_ARG_BOOL) {
            int64_t *slot = (int64_t *)((char *)out + offset);
            *slot = strcmp(parts[3] ? parts[3] : "0", "1") == 0 ? 1 : 0;
        } else {
            ps_arg_set(out, offset, kind, parts[3] ? parts[3] : "0");
        }
        free(text);
    }

    for (int64_t a = 1; a < nargv; a++) {
        PsBuf token;
        ps_seq_get(argv, a, &token);
        char *word = ps_arg_dup(token.len, token.data);
        char *eq = strchr(word, '=');
        int64_t head_len = eq ? (int64_t)(eq - word) : (int64_t)strlen(word);
        const char *inline_value = eq ? eq + 1 : NULL;

        int matched = 0;
        for (int64_t i = 0; i < nspecs; i++) {
            PsBuf row;
            ps_seq_get(specs, i, &row);
            char *text = ps_arg_dup(row.len, row.data);
            char *parts[5];
            ps_arg_split(text, parts, 5);
            int64_t flags_len = (int64_t)strlen(parts[4]);
            if (!ps_arg_matches(parts[4], flags_len, word, head_len)) {
                free(text);
                continue;
            }
            matched = 1;
            int64_t kind = 0;
            if (i < ps_seq_len(kind_seq)) {
                ps_seq_get(kind_seq, i, &kind);
            }
            int64_t offset = ps_arg_offset(kind_seq, i);
            const char *action = parts[1];
            if (kind == PS_ARG_BOOL) {
                int64_t *slot = (int64_t *)((char *)out + offset);
                *slot = strcmp(action, "store_false") == 0 ? 0 : 1;
            } else if (strcmp(action, "store_const") == 0) {
                ps_arg_set(out, offset, kind, parts[2] ? parts[2] : "");
            } else {
                const char *value = inline_value;
                char *owned = NULL;
                if (value == NULL) {
                    if (a + 1 >= nargv) {
                        free(text);
                        free(word);
                        ps_arg_fail("option needs a value");
                    }
                    a += 1;
                    PsBuf next;
                    ps_seq_get(argv, a, &next);
                    owned = ps_arg_dup(next.len, next.data);
                    value = owned;
                }
                ps_arg_set(out, offset, kind, value);
                free(owned);
            }
            free(text);
            break;
        }
        if (!matched) {
            fprintf(stderr, "unrecognized argument: %s\n", word);
            free(word);
            exit(2);
        }
        free(word);
    }
}
