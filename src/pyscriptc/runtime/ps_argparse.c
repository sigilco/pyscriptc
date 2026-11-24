/* argparse: the script subset of the command-line parser.

The parser holds its registered options as a list of encoded spec strings.
Each spec is `dest|action|const|default|flags|required|slot|nargs`: the fields never hold a `|`
because dest is a Python identifier and action and const come from a closed
set. `flags` is the comma-separated list of option spellings (empty for positional
arguments), `required` is "1" or "0", `slot` is the index of the namespace field
the value lands in, and `nargs` is empty (one value), `?`, `*`, `+`, `R` (REMAINDER)
or a count. A list-valued nargs stores a list in the slot (kind 4 + the element kind: the list of
str, int or float).

A parser may hold subcommand parsers (`add_subparsers`/`add_parser`), nested to any
depth: `subs` is a sequence of pointers, the first a header (the dest slot, the required
flag, the metavar and the number of specs registered before the call), the rest one
record per subcommand name (an alias is one more record on the same child). The
subcommand action is a positional of nargs PARSER, ordered among the positional specs by
registration, so the token it consumes and everything after it go to the child parser;
the name as typed lands in the dest slot. The help text and the error prefix of every
level arrive baked in one blob of `name RS prog RS help` triples (RS is 0x1e, the top
parser first under the empty name, a nested level under its space-joined path), so the
error paths print CPython's bytes: the usage block, then `prog: error: message`, exit 2.
Unrecognized tokens are collected and the top parser reports them last, as CPython does.

`parse_args` follows CPython's `_parse_known_args`: it classifies every token as an
argument `A`, an option `O` or the first `--` (`-`), consumes positionals by matching
their nargs patterns against that string, and writes one value per registered dest
into the namespace the compiler allocated. The namespace holds one slot per dest, in registration order:
a string is a PsBuf (16 bytes), a number and a flag are an i64 (8 bytes).
The kind table tells the runtime how wide each slot is.
*/

#include "ps_rt.h"

#include <ctype.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ps_argv.c keeps the process argv. */
PsSeq *ps_argv_get(void);

enum { PS_ARG_STR = 0, PS_ARG_INT = 1, PS_ARG_BOOL = 2, PS_ARG_FLOAT = 3, PS_ARG_LIST = 4 };
/* A list kind is PS_ARG_LIST + the element kind (str, int, float); its slot is 8 bytes. */

/* The parser is `{ PsSeq *specs; PsSeq *subs }`, the same shape as the ArgParser
   class: each list field is a pointer to a sequence. */
typedef struct {
    PsSeq *specs;
    PsSeq *subs;
} PsArgParser;

/* add_subparsers result: the parent plus what the parse needs from the action. */
typedef struct {
    PsArgParser *parent;
    int64_t dest_slot;
    int64_t required;
} PsSubParsers;

/* One entry of `subs`. The first entry is the header (child NULL): it carries
   the dest slot (-1 without a dest), the required flag, the metavar (canon) and
   the dest name (name) of the action, and `pos`, the number of specs registered
   before the add_subparsers call. A child entry carries the name it answers to
   and the canonical name of the child (the level key). */
typedef struct {
    PsArgParser *child;
    char *name;
    char *canon;
    int64_t dest_slot;
    int64_t required;
    int64_t pos;
} PsArgSub;

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

#define PS_ARG_PARTS 8
#define PS_ARG_RS '\x1e'

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

static PsSeq *ps_arg_subs_seq(void) { return ps_seq_new((int64_t)sizeof(PsArgSub *), 0); }

/* ArgumentParser: an empty parser with an empty spec list and no subcommands. */
void ps_argparse_new(PsArgParser *out) {
    out->specs = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    out->subs = ps_arg_subs_seq();
}

/* add_argument: append one encoded spec row. */
void ps_argparse_add(PsArgParser *parser, int64_t sn, const char *spec) {
    PsBuf row;
    row.len = sn;
    row.data = ps_alloc(sn + 1);
    memcpy(row.data, spec, (size_t)sn + 1);
    ps_seq_push(parser->specs, &row);
}

/* add_subparsers: record the dest slot, the required flag, the metavar and the
   position among the specs in the header. */
void ps_argparse_subs(PsArgParser *parser, int64_t dest_slot, int64_t required, int64_t nd, const char *dest,
                      int64_t nm, const char *metavar, PsSubParsers *out) {
    out->parent = parser;
    out->dest_slot = dest_slot;
    out->required = required;
    if (ps_seq_len(parser->subs) == 0) {
        PsArgSub *header = calloc(1, sizeof(PsArgSub));
        if (header == NULL) {
            abort();
        }
        header->dest_slot = dest_slot;
        header->required = required;
        header->name = ps_arg_dup(nd, dest);
        header->canon = ps_arg_dup(nm, metavar);
        header->pos = ps_seq_len(parser->specs);
        ps_seq_push(parser->subs, &header);
    }
}

static void ps_arg_push_sub(PsArgParser *parent, PsArgParser *child, int64_t nn, const char *name, const char *canon) {
    PsArgSub *entry = calloc(1, sizeof(PsArgSub));
    if (entry == NULL) {
        abort();
    }
    entry->child = child;
    entry->name = ps_arg_dup(nn, name);
    entry->canon = ps_arg_dup((int64_t)strlen(canon), canon);
    ps_seq_push(parent->subs, &entry);
}

/* add_parser: a fresh child parser registered under `name` and each alias (the
   aliases arrive space-separated). */
void ps_argparse_add_parser(PsSubParsers *holder, int64_t nn, const char *name, int64_t na, const char *aliases,
                            PsArgParser *out) {
    ps_argparse_new(out);
    char *canon = ps_arg_dup(nn, name);
    ps_arg_push_sub(holder->parent, out, nn, name, canon);
    char *list = ps_arg_dup(na, aliases);
    for (char *tok = strtok(list, " "); tok != NULL; tok = strtok(NULL, " ")) {
        ps_arg_push_sub(holder->parent, out, (int64_t)strlen(tok), tok, canon);
    }
    free(list);
    free(canon);
}

/* The offset of the slot-th namespace slot, given the kind table. */
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

static int64_t ps_arg_kind(PsSeq *kinds, int64_t slot) {
    int64_t kind = 0;
    if (slot >= 0 && slot < ps_seq_len(kinds)) {
        ps_seq_get(kinds, slot, &kind);
    }
    return kind;
}

static void ps_arg_set_str(void *out, int64_t offset, const char *value) {
    PsBuf *slot = (PsBuf *)((char *)out + offset);
    slot->len = (int64_t)strlen(value);
    slot->data = ps_alloc(slot->len + 1);
    memcpy(slot->data, value, (size_t)slot->len + 1);
}

/* Write the text into the slot the kind names. Returns 0 when the text is not a
   number of the kind (the slot then holds 0). */
static int ps_arg_set(void *out, int64_t offset, int64_t kind, const char *value) {
    if (kind == PS_ARG_STR) {
        ps_arg_set_str(out, offset, value);
        return 1;
    }
    char *endptr = NULL;
    if (kind == PS_ARG_FLOAT) {
        double *slot = (double *)((char *)out + offset);
        *slot = strtod(value, &endptr);
        if (endptr == value || *endptr != '\0') {
            *slot = 0.0;
            return 0;
        }
        return 1;
    }
    int64_t *slot = (int64_t *)((char *)out + offset);
    *slot = (int64_t)strtoll(value, &endptr, 10);
    if (endptr == value || *endptr != '\0') {
        *slot = 0;
        return 0;
    }
    return 1;
}

/* The parse state shared by every level. */
typedef struct {
    const char *blob;
    int64_t blob_len;
    PsSeq *kinds;
    PsSeq *argv;
    void *out;
    const char *prog_name;
    char **extras;
    int64_t nextras;
    int64_t cap_extras;
} PsArgCtx;

/* One level's baked text: the prog the error prefix prints and the help. */
typedef struct {
    const char *prog;
    int64_t prog_len;
    const char *help;
    int64_t help_len;
} PsArgLevel;

/* Find the triple for `name` in the blob. An absent level reads as empty. */
static PsArgLevel ps_arg_level_of(PsArgCtx *ctx, const char *name) {
    PsArgLevel level = {"", 0, "", 0};
    int64_t nlen = (int64_t)strlen(name);
    const char *end = ctx->blob + ctx->blob_len;
    const char *p = ctx->blob;
    while (p <= end) {
        const char *f1 = memchr(p, PS_ARG_RS, (size_t)(end - p));
        if (f1 == NULL) {
            break;
        }
        const char *f2 = memchr(f1 + 1, PS_ARG_RS, (size_t)(end - f1 - 1));
        if (f2 == NULL) {
            break;
        }
        const char *f3 = memchr(f2 + 1, PS_ARG_RS, (size_t)(end - f2 - 1));
        const char *next = f3 ? f3 : end;
        if ((int64_t)(f1 - p) == nlen && memcmp(p, name, (size_t)nlen) == 0) {
            level.prog = f1 + 1;
            level.prog_len = (int64_t)(f2 - f1 - 1);
            level.help = f2 + 1;
            level.help_len = (int64_t)(next - f2 - 1);
            return level;
        }
        if (f3 == NULL) {
            break;
        }
        p = f3 + 1;
    }
    return level;
}

/* Print `text` with the program-name sentinel replaced by the process basename. */
static void ps_arg_put(FILE *f, const char *text, int64_t n, const char *prog_name) {
    static const char marker[] = "\x1fPROG\x1f";
    const size_t mlen = sizeof(marker) - 1;
    int64_t i = 0;
    while (i < n) {
        if ((size_t)(n - i) >= mlen && memcmp(text + i, marker, mlen) == 0) {
            fputs(prog_name, f);
            i += (int64_t)mlen;
        } else {
            fputc(text[i], f);
            i += 1;
        }
    }
}

static const char *ps_arg_basename(PsSeq *argv) {
    if (ps_seq_len(argv) == 0) {
        return "";
    }
    PsBuf tok;
    ps_seq_get(argv, 0, &tok);
    const char *name = tok.data;
    for (int64_t p = 0; p < tok.len; p++) {
        if (tok.data[p] == '/' || tok.data[p] == '\\') {
            name = tok.data + p + 1;
        }
    }
    return name;
}

/* -h: this level's help on stdout, exit 0. */
static void ps_arg_help(PsArgCtx *ctx, PsArgLevel level) {
    ps_arg_put(stdout, level.help, level.help_len, ctx->prog_name);
    fflush(stdout);
    exit(0);
}

/* The error path: the usage block, then `prog: error: message`, exit 2. */
static void ps_arg_error(PsArgCtx *ctx, PsArgLevel level, const char *fmt, ...) {
    int64_t usage = level.help_len;
    for (int64_t i = 0; i + 1 < level.help_len; i++) {
        if (level.help[i] == '\n' && level.help[i + 1] == '\n') {
            usage = i + 1;
            break;
        }
    }
    fflush(stdout);
    ps_arg_put(stderr, level.help, usage, ctx->prog_name);
    ps_arg_put(stderr, level.prog, level.prog_len, ctx->prog_name);
    fputs(": error: ", stderr);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    fflush(stderr);
    exit(2);
}

/* The label CPython prints for an argument: `-n/--name` for an option, the dest
   for a positional. The caller frees it. */
static char *ps_arg_label(const char *flags, const char *dest) {
    char *label = ps_arg_dup((int64_t)strlen(flags && *flags ? flags : dest), flags && *flags ? flags : dest);
    for (char *c = label; flags && *flags && *c; c++) {
        if (*c == ',') {
            *c = '/';
        }
    }
    return label;
}

/* ---- the spec rows ---------------------------------------------------- */

enum { PS_NARGS_ONE = 0, PS_NARGS_OPT, PS_NARGS_STAR, PS_NARGS_PLUS, PS_NARGS_REM, PS_NARGS_COUNT };

/* One decoded spec row. The text is owned by the row. */
typedef struct {
    char *text;
    char *dest;
    char *action;
    char *cnst;
    char *dflt;
    char *flags;
    int64_t slot;
    int64_t kind;
    int64_t offset;
    int nargs;
    int64_t count;
    int positional;
    int required;
    int takes_value;
} PsArgRow;

static void ps_arg_row_load(PsArgCtx *ctx, PsSeq *specs, int64_t i, PsArgRow *row) {
    PsBuf buf;
    ps_seq_get(specs, i, &buf);
    row->text = ps_arg_dup(buf.len, buf.data);
    char *parts[PS_ARG_PARTS] = {0};
    ps_arg_split(row->text, parts, PS_ARG_PARTS);
    for (int k = 0; k < PS_ARG_PARTS; k++) {
        if (parts[k] == NULL) {
            parts[k] = (char *)"";
        }
    }
    row->dest = parts[0];
    row->action = parts[1];
    row->cnst = parts[2];
    row->dflt = parts[3];
    row->flags = parts[4];
    row->required = strcmp(parts[5], "1") == 0;
    row->slot = parts[6][0] ? atoll(parts[6]) : i;
    row->kind = ps_arg_kind(ctx->kinds, row->slot);
    row->offset = ps_arg_offset(ctx->kinds, row->slot);
    row->positional = row->flags[0] == '\0';
    row->nargs = PS_NARGS_ONE;
    row->count = 0;
    const char *nargs = parts[7];
    if (strcmp(nargs, "?") == 0) {
        row->nargs = PS_NARGS_OPT;
    } else if (strcmp(nargs, "*") == 0) {
        row->nargs = PS_NARGS_STAR;
    } else if (strcmp(nargs, "+") == 0) {
        row->nargs = PS_NARGS_PLUS;
    } else if (strcmp(nargs, "R") == 0) {
        row->nargs = PS_NARGS_REM;
    } else if (nargs[0] != '\0') {
        row->nargs = PS_NARGS_COUNT;
        row->count = atoll(nargs);
    }
    row->takes_value = row->kind != PS_ARG_BOOL && strcmp(row->action, "store_const") != 0;
}

static void ps_arg_row_free(PsArgRow *row) { free(row->text); }

/* A fresh empty list of the element kind, stored in the slot. */
static void ps_arg_set_empty_list(void *out, int64_t offset, int64_t kind) {
    int64_t base = kind - PS_ARG_LIST;
    PsSeq *seq = ps_seq_new(base == PS_ARG_STR ? (int64_t)sizeof(PsBuf) : 8, 0);
    *(PsSeq **)((char *)out + offset) = seq;
}

/* Convert `value` to the element kind and push it. Returns 0 on a bad number. */
static int ps_arg_push_value(PsSeq *seq, int64_t base, const char *value) {
    if (base == PS_ARG_STR) {
        PsBuf item;
        ps_arg_set_str(&item, 0, value);
        ps_seq_push(seq, &item);
        return 1;
    }
    char *endptr = NULL;
    if (base == PS_ARG_FLOAT) {
        double number = strtod(value, &endptr);
        if (endptr == value || *endptr != '\0') {
            return 0;
        }
        ps_seq_push(seq, &number);
        return 1;
    }
    int64_t number = (int64_t)strtoll(value, &endptr, 10);
    if (endptr == value || *endptr != '\0') {
        return 0;
    }
    ps_seq_push(seq, &number);
    return 1;
}

/* Write every default of this parser and its subcommands, so an option or a
   subcommand that does not appear keeps one. */
static void ps_arg_defaults(PsArgCtx *ctx, PsArgParser *parser) {
    int64_t nspecs = ps_seq_len(parser->specs);
    for (int64_t i = 0; i < nspecs; i++) {
        PsArgRow row;
        ps_arg_row_load(ctx, parser->specs, i, &row);
        if (row.kind >= PS_ARG_LIST) {
            ps_arg_set_empty_list(ctx->out, row.offset, row.kind);
        } else if (row.kind == PS_ARG_BOOL) {
            *(int64_t *)((char *)ctx->out + row.offset) = strcmp(row.dflt[0] ? row.dflt : "0", "1") == 0 ? 1 : 0;
        } else if (row.dflt[0] != '\0') {
            ps_arg_set(ctx->out, row.offset, row.kind, row.dflt);
        } else if (row.kind == PS_ARG_STR) {
            ps_arg_set_str(ctx->out, row.offset, "");
        } else if (row.kind == PS_ARG_FLOAT) {
            *(double *)((char *)ctx->out + row.offset) = 0.0;
        } else {
            *(int64_t *)((char *)ctx->out + row.offset) = 0;
        }
        ps_arg_row_free(&row);
    }
    int64_t nsubs = ps_seq_len(parser->subs);
    PsArgParser *last = NULL;
    for (int64_t i = 0; i < nsubs; i++) {
        PsArgSub *sub = NULL;
        ps_seq_get(parser->subs, i, &sub);
        if (sub->child == NULL) {
            if (sub->dest_slot >= 0) {
                ps_arg_set_str(ctx->out, ps_arg_offset(ctx->kinds, sub->dest_slot), "");
            }
        } else if (sub->child != last) {
            ps_arg_defaults(ctx, sub->child);
            last = sub->child;
        }
    }
}

static void ps_arg_extra(PsArgCtx *ctx, const char *word) {
    if (ctx->nextras == ctx->cap_extras) {
        ctx->cap_extras = ctx->cap_extras ? ctx->cap_extras * 2 : 4;
        ctx->extras = realloc(ctx->extras, (size_t)ctx->cap_extras * sizeof(char *));
        if (ctx->extras == NULL) {
            abort();
        }
    }
    ctx->extras[ctx->nextras++] = ps_arg_dup((int64_t)strlen(word), word);
}

/* The `{a,b}` label of a subcommand action without a dest. */
static char *ps_arg_choices_label(PsArgParser *parser, const char *sep, const char *quote) {
    size_t cap = 8;
    int64_t nsubs = ps_seq_len(parser->subs);
    for (int64_t i = 0; i < nsubs; i++) {
        PsArgSub *sub = NULL;
        ps_seq_get(parser->subs, i, &sub);
        if (sub->child != NULL) {
            cap += strlen(sub->name) + strlen(sep) + 2 * strlen(quote);
        }
    }
    char *text = malloc(cap);
    if (text == NULL) {
        abort();
    }
    text[0] = '\0';
    int first = 1;
    for (int64_t i = 0; i < nsubs; i++) {
        PsArgSub *sub = NULL;
        ps_seq_get(parser->subs, i, &sub);
        if (sub->child == NULL) {
            continue;
        }
        if (!first) {
            strcat(text, sep);
        }
        strcat(text, quote);
        strcat(text, sub->name);
        strcat(text, quote);
        first = 0;
    }
    return text;
}

/* The label of the subcommand action: its metavar, else its dest, else `{a,b}`.
   The caller frees it. */
static char *ps_arg_subs_label(PsArgParser *parser, PsArgSub *header) {
    if (header->canon != NULL && header->canon[0] != '\0') {
        return ps_arg_dup((int64_t)strlen(header->canon), header->canon);
    }
    if (header->name != NULL && header->name[0] != '\0') {
        return ps_arg_dup((int64_t)strlen(header->name), header->name);
    }
    char *inner = ps_arg_choices_label(parser, ",", "");
    char *label = malloc(strlen(inner) + 3);
    if (label == NULL) {
        abort();
    }
    sprintf(label, "{%s}", inner);
    free(inner);
    return label;
}

/* ---- the nargs patterns ------------------------------------------------ */

/* One element of a nargs pattern: a character set repeated min..max times (max
   -1 is unbounded). The sets are over the argument-string alphabet `A` (argument),
   `-` (the first `--`) and `O` (option). The matcher is a greedy backtracking
   walk, which is what the regex engine does for CPython's patterns. */
#define PS_SET_A 1
#define PS_SET_D 2
#define PS_SET_O 4

typedef struct {
    unsigned char set;
    int min;
    int max;
} PsArgElem;

static unsigned char ps_arg_bit(char c) { return c == 'A' ? PS_SET_A : (c == '-' ? PS_SET_D : PS_SET_O); }

static int64_t ps_arg_match_rec(const PsArgElem *e, int64_t ne, int64_t ei, const char *s, int64_t len, int64_t pos,
                                int64_t *starts) {
    starts[ei] = pos;
    if (ei == ne) {
        return pos;
    }
    int64_t maxc = 0;
    while (pos + maxc < len && (e[ei].max < 0 || maxc < e[ei].max) && (ps_arg_bit(s[pos + maxc]) & e[ei].set)) {
        maxc++;
    }
    for (int64_t c = maxc; c >= e[ei].min; c--) {
        int64_t r = ps_arg_match_rec(e, ne, ei + 1, s, len, pos + c, starts);
        if (r >= 0) {
            return r;
        }
    }
    return -1;
}

static PsArgElem *ps_arg_put_elem(PsArgElem *out, unsigned char set, int min, int max) {
    out->set = set;
    out->min = min;
    out->max = max;
    return out + 1;
}

/* The pattern of a positional row (`ps_arg_subs_elems` for the subcommand action).
   Returns the number of elements written. */
static int64_t ps_arg_row_elems(const PsArgRow *row, PsArgElem *out) {
    PsArgElem *p = out;
    switch (row->nargs) {
    case PS_NARGS_ONE:
        p = ps_arg_put_elem(p, PS_SET_D, 0, -1);
        p = ps_arg_put_elem(p, PS_SET_A, 1, 1);
        p = ps_arg_put_elem(p, PS_SET_D, 0, -1);
        break;
    case PS_NARGS_OPT:
        p = ps_arg_put_elem(p, PS_SET_D, 0, -1);
        p = ps_arg_put_elem(p, PS_SET_A, 0, 1);
        p = ps_arg_put_elem(p, PS_SET_D, 0, -1);
        break;
    case PS_NARGS_STAR:
        p = ps_arg_put_elem(p, PS_SET_D, 0, -1);
        p = ps_arg_put_elem(p, PS_SET_A | PS_SET_D, 0, -1);
        break;
    case PS_NARGS_PLUS:
        p = ps_arg_put_elem(p, PS_SET_D, 0, -1);
        p = ps_arg_put_elem(p, PS_SET_A, 1, 1);
        p = ps_arg_put_elem(p, PS_SET_A | PS_SET_D, 0, -1);
        break;
    case PS_NARGS_REM:
        p = ps_arg_put_elem(p, PS_SET_A | PS_SET_D | PS_SET_O, 0, -1);
        break;
    default:
        for (int64_t k = 0; k < row->count; k++) {
            p = ps_arg_put_elem(p, PS_SET_D, 0, -1);
            p = ps_arg_put_elem(p, PS_SET_A, 1, 1);
        }
        p = ps_arg_put_elem(p, PS_SET_D, 0, -1);
        break;
    }
    return (int64_t)(p - out);
}

/* `(-*A[-AO]*)`: the subcommand action takes one name and everything after it. */
static int64_t ps_arg_subs_elems(PsArgElem *out) {
    PsArgElem *p = out;
    p = ps_arg_put_elem(p, PS_SET_D, 0, -1);
    p = ps_arg_put_elem(p, PS_SET_A, 1, 1);
    p = ps_arg_put_elem(p, PS_SET_A | PS_SET_D | PS_SET_O, 0, -1);
    return (int64_t)(p - out);
}

/* ---- one parser level -------------------------------------------------- */

/* A positional action still to fill: a spec row, or the subcommand action. */
typedef struct {
    int is_subs;
    int64_t idx;
} PsArgPos;

typedef struct {
    PsArgCtx *ctx;
    PsArgParser *parser;
    PsArgLevel level;
    const char *path;
    char **words;
    int64_t nw;
    char *pat;         /* A, O or - per word */
    int64_t *opt;      /* per word: the row of an option, -1 unknown, -2 help */
    const char **inl;  /* per word: the `=value` of an option, else NULL */
    PsArgRow *rows;
    int64_t nrows;
    int *seen;
    int seen_subs;
    PsArgSub *header;
    PsArgPos *rem;     /* the positionals not yet consumed */
    int64_t nrem;
} PsArgLv;

static int ps_arg_negative_number(const char *w) {
    if (w[0] != '-') {
        return 0;
    }
    const char *p = w + 1;
    int digits = 0;
    while (isdigit((unsigned char)*p)) {
        p++;
        digits++;
    }
    if (*p == '\0') {
        return digits > 0;
    }
    if (*p != '.') {
        return 0;
    }
    p++;
    int frac = 0;
    while (isdigit((unsigned char)*p)) {
        p++;
        frac++;
    }
    return *p == '\0' && frac > 0;
}

static int64_t ps_arg_find_flag(PsArgLv *lv, const char *word, int64_t n) {
    for (int64_t i = 0; i < lv->nrows; i++) {
        if (lv->rows[i].flags[0] != '\0' &&
            ps_arg_matches(lv->rows[i].flags, (int64_t)strlen(lv->rows[i].flags), word, n)) {
            return i;
        }
    }
    return -1;
}

/* `_parse_optional`: is this word an option of the level, an unknown option, or an argument? */
static void ps_arg_classify(PsArgLv *lv, int64_t i) {
    const char *w = lv->words[i];
    lv->opt[i] = -1;
    lv->inl[i] = NULL;
    if (w[0] != '-') {
        lv->pat[i] = 'A';
        return;
    }
    int64_t n = (int64_t)strlen(w);
    int64_t row = ps_arg_find_flag(lv, w, n);
    if (row >= 0) {
        lv->pat[i] = 'O';
        lv->opt[i] = row;
        return;
    }
    if (strcmp(w, "-h") == 0 || strcmp(w, "--help") == 0) {
        lv->pat[i] = 'O';
        lv->opt[i] = -2;
        return;
    }
    if (n == 1) {
        lv->pat[i] = 'A';
        return;
    }
    const char *eq = strchr(w, '=');
    if (eq != NULL) {
        row = ps_arg_find_flag(lv, w, (int64_t)(eq - w));
        if (row >= 0) {
            lv->pat[i] = 'O';
            lv->opt[i] = row;
            lv->inl[i] = eq + 1;
            return;
        }
    }
    if (ps_arg_negative_number(w) || strchr(w, ' ') != NULL) {
        lv->pat[i] = 'A';
        return;
    }
    lv->pat[i] = 'O';
}

static void ps_arg_set_checked(PsArgLv *lv, const PsArgRow *row, const char *label, const char *value) {
    if (!ps_arg_set(lv->ctx->out, row->offset, row->kind, value)) {
        ps_arg_error(lv->ctx, lv->level, "argument %s: invalid %s value: '%s'", label,
                     row->kind == PS_ARG_FLOAT ? "float" : "int", value);
    }
}

static void ps_arg_set_list(PsArgLv *lv, const PsArgRow *row, const char *label, char **args, int64_t n) {
    int64_t base = row->kind - PS_ARG_LIST;
    PsSeq *seq = ps_seq_new(base == PS_ARG_STR ? (int64_t)sizeof(PsBuf) : 8, 0);
    for (int64_t i = 0; i < n; i++) {
        if (!ps_arg_push_value(seq, base, args[i])) {
            ps_arg_error(lv->ctx, lv->level, "argument %s: invalid %s value: '%s'", label,
                         base == PS_ARG_FLOAT ? "float" : "int", args[i]);
        }
    }
    *(PsSeq **)((char *)lv->ctx->out + row->offset) = seq;
}

/* take_action for a registered row (an option or a positional) with its values. */
static void ps_arg_take_row(PsArgLv *lv, const PsArgRow *row, const char *label, char **args, int64_t n) {
    if (!row->takes_value) {
        if (row->kind == PS_ARG_BOOL) {
            *(int64_t *)((char *)lv->ctx->out + row->offset) = strcmp(row->action, "store_false") == 0 ? 0 : 1;
        } else {
            ps_arg_set(lv->ctx->out, row->offset, row->kind, row->cnst);
        }
        return;
    }
    if (row->kind >= PS_ARG_LIST) {
        ps_arg_set_list(lv, row, label, args, n);
    } else if (n == 0) {
        /* nargs='?' with no value: the const of an option, the default of a positional. */
        const char *fallback = row->positional ? row->dflt : row->cnst;
        if (fallback[0] != '\0' || row->kind == PS_ARG_STR) {
            ps_arg_set_checked(lv, row, label, fallback);
        }
    } else {
        ps_arg_set_checked(lv, row, label, args[0]);
    }
}

static void ps_arg_level(PsArgCtx *ctx, PsArgParser *parser, const char *path, char **words, int64_t nw);

/* The subcommand action: its first value names the child, the rest is the child's argv. */
static void ps_arg_take_subs(PsArgLv *lv, char **args, int64_t n) {
    lv->seen_subs = 1;
    PsArgParser *parser = lv->parser;
    PsArgSub *found = NULL;
    int64_t nsubs = ps_seq_len(parser->subs);
    for (int64_t i = 1; i < nsubs; i++) {
        PsArgSub *sub = NULL;
        ps_seq_get(parser->subs, i, &sub);
        if (strcmp(sub->name, args[0]) == 0) {
            found = sub;
            break;
        }
    }
    if (found == NULL) {
        char *label = ps_arg_subs_label(parser, lv->header);
        char *choices = ps_arg_choices_label(parser, ", ", "'");
        ps_arg_error(lv->ctx, lv->level, "argument %s: invalid choice: '%s' (choose from %s)", label, args[0],
                     choices);
    }
    if (lv->header->dest_slot >= 0) {
        ps_arg_set_str(lv->ctx->out, ps_arg_offset(lv->ctx->kinds, lv->header->dest_slot), found->name);
    }
    size_t plen = strlen(lv->path) + strlen(found->canon) + 2;
    char *child_path = malloc(plen);
    if (child_path == NULL) {
        abort();
    }
    if (lv->path[0] == '\0') {
        strcpy(child_path, found->canon);
    } else {
        sprintf(child_path, "%s %s", lv->path, found->canon);
    }
    ps_arg_level(lv->ctx, found->child, child_path, args + 1, n - 1);
}

/* consume_optional: the option at `start` and the values its nargs takes. */
static int64_t ps_arg_consume_optional(PsArgLv *lv, int64_t start) {
    int64_t r = lv->opt[start];
    const char *word = lv->words[start];
    if (r == -2) {
        ps_arg_help(lv->ctx, lv->level);
    }
    if (r < 0) {
        ps_arg_extra(lv->ctx, word);
        return start + 1;
    }
    PsArgRow *row = &lv->rows[r];
    lv->seen[r] = 1;
    char *label = ps_arg_label(row->flags, row->dest);
    const char *inline_value = lv->inl[start];
    if (inline_value != NULL) {
        if (!row->takes_value) {
            ps_arg_error(lv->ctx, lv->level, "argument %s: ignored explicit argument '%s'", label, inline_value);
        }
        if (row->nargs == PS_NARGS_COUNT && row->count != 1) {
            ps_arg_error(lv->ctx, lv->level, "argument %s: expected %lld arguments", label, (long long)row->count);
        }
        char *args[1] = {(char *)inline_value};
        ps_arg_take_row(lv, row, label, args, 1);
        free(label);
        return start + 1;
    }
    const char *rest = lv->pat + start + 1;
    int64_t avail = lv->nw - start - 1;
    int64_t n = 0;
    if (row->takes_value) {
        switch (row->nargs) {
        case PS_NARGS_ONE:
            if (avail < 1 || rest[0] != 'A') {
                ps_arg_error(lv->ctx, lv->level, "argument %s: expected one argument", label);
            }
            n = 1;
            break;
        case PS_NARGS_OPT:
            n = (avail >= 1 && rest[0] == 'A') ? 1 : 0;
            break;
        case PS_NARGS_STAR:
        case PS_NARGS_PLUS:
            while (n < avail && rest[n] == 'A') {
                n++;
            }
            if (row->nargs == PS_NARGS_PLUS && n == 0) {
                ps_arg_error(lv->ctx, lv->level, "argument %s: expected at least one argument", label);
            }
            break;
        case PS_NARGS_REM:
            while (n < avail && (rest[n] == 'A' || rest[n] == 'O')) {
                n++;
            }
            break;
        default:
            while (n < avail && n < row->count && (rest[n] == 'A' || rest[n] == 'O')) {
                n++;
            }
            if (n < row->count) {
                ps_arg_error(lv->ctx, lv->level, row->count == 1 ? "argument %s: expected %lld argument" : "argument %s: expected %lld arguments",
                             label, (long long)row->count);
            }
            break;
        }
    }
    ps_arg_take_row(lv, row, label, lv->words + start + 1, n);
    free(label);
    return start + 1 + n;
}

/* consume_positionals: match as many of the remaining positionals as possible
   against the pattern from `start`, and take each one's values. */
static int64_t ps_arg_consume_positionals(PsArgLv *lv, int64_t start) {
    const char *s = lv->pat + start;
    int64_t len = lv->nw - start;
    int64_t k = 0;
    int64_t *counts = calloc((size_t)(lv->nrem + 1), sizeof(int64_t));
    if (counts == NULL) {
        abort();
    }
    for (int64_t take = lv->nrem; take >= 1 && k == 0; take--) {
        int64_t cap = 0;
        for (int64_t g = 0; g < take; g++) {
            cap += lv->rem[g].is_subs ? 3 : 2 * lv->rows[lv->rem[g].idx].count + 3;
        }
        PsArgElem *elems = malloc((size_t)cap * sizeof(PsArgElem));
        int64_t *starts = malloc((size_t)(cap + 1) * sizeof(int64_t));
        int64_t *group = malloc((size_t)(take + 1) * sizeof(int64_t));
        if (elems == NULL || starts == NULL || group == NULL) {
            abort();
        }
        int64_t ne = 0;
        for (int64_t g = 0; g < take; g++) {
            group[g] = ne;
            ne += lv->rem[g].is_subs ? ps_arg_subs_elems(elems + ne)
                                     : ps_arg_row_elems(&lv->rows[lv->rem[g].idx], elems + ne);
        }
        group[take] = ne;
        int64_t end = ps_arg_match_rec(elems, ne, 0, s, len, 0, starts);
        if (end >= 0) {
            for (int64_t g = 0; g < take; g++) {
                counts[g] = starts[group[g + 1]] - starts[group[g]];
            }
            int64_t keep = take;
            if (end < len && s[end] == 'O') {
                while (keep > 0 && counts[keep - 1] == 0) {
                    keep--;
                }
            }
            k = keep;
            if (k == 0) {
                /* Every group matched empty before an option: nothing is consumed. */
                free(elems);
                free(starts);
                free(group);
                break;
            }
        }
        free(elems);
        free(starts);
        free(group);
    }
    int64_t index = start;
    PsArgPos *taken = lv->rem;
    for (int64_t g = 0; g < k; g++) {
        int64_t cnt = counts[g];
        char **args = malloc((size_t)(cnt + 1) * sizeof(char *));
        if (args == NULL) {
            abort();
        }
        int64_t n = 0;
        int dropped = 0;
        for (int64_t j = 0; j < cnt; j++) {
            int is_dd = lv->pat[index + j] == '-';
            /* Strip the first `--`, except from a REMAINDER argument; the
               subcommand action only loses a `--` that starts its values. */
            int remainder = !taken[g].is_subs && lv->rows[taken[g].idx].nargs == PS_NARGS_REM;
            int leading = !taken[g].is_subs || j == 0;
            if (is_dd && !dropped && !remainder && leading) {
                dropped = 1;
                continue;
            }
            args[n++] = lv->words[index + j];
        }
        index += cnt;
        if (taken[g].is_subs) {
            ps_arg_take_subs(lv, args, n);
        } else {
            PsArgRow *row = &lv->rows[taken[g].idx];
            lv->seen[taken[g].idx] = 1;
            ps_arg_take_row(lv, row, row->dest, args, n);
        }
        free(args);
    }
    lv->rem += k;
    lv->nrem -= k;
    free(counts);
    return index;
}

/* One parser level over `words`. */
static void ps_arg_level(PsArgCtx *ctx, PsArgParser *parser, const char *path, char **words, int64_t nw) {
    PsArgLv lv;
    memset(&lv, 0, sizeof(lv));
    lv.ctx = ctx;
    lv.parser = parser;
    lv.level = ps_arg_level_of(ctx, path);
    lv.path = path;
    lv.words = words;
    lv.nw = nw;
    lv.nrows = ps_seq_len(parser->specs);
    int64_t nsubs = ps_seq_len(parser->subs);
    if (nsubs > 0) {
        ps_seq_get(parser->subs, 0, &lv.header);
    }
    lv.rows = calloc((size_t)(lv.nrows + 1), sizeof(PsArgRow));
    lv.seen = calloc((size_t)(lv.nrows + 1), sizeof(int));
    lv.pat = malloc((size_t)nw + 1);
    lv.opt = malloc((size_t)(nw + 1) * sizeof(int64_t));
    lv.inl = malloc((size_t)(nw + 1) * sizeof(char *));
    lv.rem = malloc((size_t)(lv.nrows + 2) * sizeof(PsArgPos));
    if (lv.rows == NULL || lv.seen == NULL || lv.pat == NULL || lv.opt == NULL || lv.inl == NULL || lv.rem == NULL) {
        abort();
    }
    for (int64_t i = 0; i < lv.nrows; i++) {
        ps_arg_row_load(ctx, parser->specs, i, &lv.rows[i]);
    }
    /* The positionals in registration order; the subcommand action sits where
       add_subparsers was called. */
    for (int64_t i = 0; i <= lv.nrows; i++) {
        if (nsubs > 0 && lv.header->pos == i) {
            lv.rem[lv.nrem].is_subs = 1;
            lv.rem[lv.nrem].idx = 0;
            lv.nrem++;
        }
        if (i < lv.nrows && lv.rows[i].positional) {
            lv.rem[lv.nrem].is_subs = 0;
            lv.rem[lv.nrem].idx = i;
            lv.nrem++;
        }
    }
    int after_dd = 0;
    for (int64_t i = 0; i < nw; i++) {
        if (after_dd) {
            lv.pat[i] = 'A';
            lv.opt[i] = -1;
            lv.inl[i] = NULL;
        } else if (strcmp(words[i], "--") == 0) {
            lv.pat[i] = '-';
            lv.opt[i] = -1;
            lv.inl[i] = NULL;
            after_dd = 1;
        } else {
            ps_arg_classify(&lv, i);
        }
    }
    lv.pat[nw] = '\0';

    int64_t max_option = -1;
    for (int64_t i = 0; i < nw; i++) {
        if (lv.pat[i] == 'O') {
            max_option = i;
        }
    }
    int64_t start = 0;
    while (start <= max_option) {
        int64_t next_option = start;
        while (next_option <= max_option && lv.pat[next_option] != 'O') {
            next_option++;
        }
        if (start != next_option) {
            int64_t end = ps_arg_consume_positionals(&lv, start);
            if (end > start) {
                start = end;
                continue;
            }
            start = end;
        }
        if (lv.pat[start] != 'O') {
            for (int64_t i = start; i < next_option; i++) {
                ps_arg_extra(ctx, words[i]);
            }
            start = next_option;
        }
        start = ps_arg_consume_optional(&lv, start);
    }
    int64_t stop = ps_arg_consume_positionals(&lv, start);
    for (int64_t i = stop; i < nw; i++) {
        ps_arg_extra(ctx, words[i]);
    }

    /* The required arguments and the required subcommand, in registration order. */
    char *missing = calloc(1, 1);
    if (missing == NULL) {
        abort();
    }
    int nmissing = 0;
    for (int64_t i = 0; i <= lv.nrows; i++) {
        char *labels[2] = {NULL, NULL};
        if (nsubs > 0 && lv.header->pos == i && lv.header->required && !lv.seen_subs) {
            labels[0] = ps_arg_subs_label(parser, lv.header);
        }
        if (i < lv.nrows && lv.rows[i].required && !lv.seen[i]) {
            labels[1] = ps_arg_label(lv.rows[i].flags, lv.rows[i].dest);
        }
        for (int k = 0; k < 2; k++) {
            if (labels[k] == NULL) {
                continue;
            }
            missing = realloc(missing, strlen(missing) + strlen(labels[k]) + 3);
            if (missing == NULL) {
                abort();
            }
            if (nmissing > 0) {
                strcat(missing, ", ");
            }
            strcat(missing, labels[k]);
            nmissing++;
            free(labels[k]);
        }
    }
    if (nmissing > 0) {
        ps_arg_error(ctx, lv.level, "the following arguments are required: %s", missing);
    }
    free(missing);
    for (int64_t i = 0; i < lv.nrows; i++) {
        ps_arg_row_free(&lv.rows[i]);
    }
    free(lv.rows);
    free(lv.seen);
    free(lv.pat);
    free(lv.opt);
    free(lv.inl);
    /* `rem` was advanced as positionals were consumed; its base is not needed again. */
}

static void ps_argparse_parse_from(PsArgParser *parser, PsSeq *kinds, int64_t blob_len, const char *blob, PsSeq *argv,
                                   void *out) {
    PsArgCtx ctx = {blob, blob_len, kinds, argv, out, ps_arg_basename(argv), NULL, 0, 0};
    ps_arg_defaults(&ctx, parser);
    int64_t nargv = ps_seq_len(argv);
    char **words = calloc((size_t)nargv + 1, sizeof(char *));
    if (words == NULL) {
        abort();
    }
    for (int64_t i = 1; i < nargv; i++) {
        PsBuf token;
        ps_seq_get(argv, i, &token);
        words[i - 1] = ps_arg_dup(token.len, token.data);
    }
    ps_arg_level(&ctx, parser, "", words, nargv > 0 ? nargv - 1 : 0);
    if (ctx.nextras > 0) {
        size_t total = 1;
        for (int64_t i = 0; i < ctx.nextras; i++) {
            total += strlen(ctx.extras[i]) + 1;
        }
        char *joined = malloc(total);
        if (joined == NULL) {
            abort();
        }
        joined[0] = '\0';
        for (int64_t i = 0; i < ctx.nextras; i++) {
            if (i > 0) {
                strcat(joined, " ");
            }
            strcat(joined, ctx.extras[i]);
        }
        ps_arg_error(&ctx, ps_arg_level_of(&ctx, ""), "unrecognized arguments: %s", joined);
    }
}

void ps_argparse_parse(PsArgParser *parser, PsSeq *kinds, int64_t blob_len, const char *blob, void *out) {
    ps_argparse_parse_from(parser, kinds, blob_len, blob, ps_argv_get(), out);
}

/* parse_args(list): the given strings replace the process arguments; the
   program name still comes from argv[0]. */
void ps_argparse_parse_list(PsArgParser *parser, PsSeq *kinds, int64_t blob_len, const char *blob, PsSeq *args,
                            void *out) {
    PsSeq *process = ps_argv_get();
    PsSeq *argv = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    if (ps_seq_len(process) > 0) {
        PsBuf prog;
        ps_seq_get(process, 0, &prog);
        ps_seq_push(argv, &prog);
    } else {
        PsBuf prog = {0, (char *)""};
        ps_seq_push(argv, &prog);
    }
    for (int64_t i = 0; i < ps_seq_len(args); i++) {
        PsBuf item;
        ps_seq_get(args, i, &item);
        ps_seq_push(argv, &item);
    }
    ps_argparse_parse_from(parser, kinds, blob_len, blob, argv, out);
}
