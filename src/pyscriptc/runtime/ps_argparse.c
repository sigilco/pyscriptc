/* argparse: the script subset of the command-line parser.

The parser holds its registered options as a list of encoded spec strings.
Each spec is `dest|action|const|default|flags|required|slot`: the fields never hold a `|`
because dest is a Python identifier and action and const come from a closed
set. `flags` is the comma-separated list of option spellings (empty for positional
arguments), `required` is "1" or "0", and `slot` is the index of the namespace field
the value lands in.

A parser may hold subcommand parsers (`add_subparsers`/`add_parser`): `subs` is a
sequence of pointers, the first a header (the dest slot and the required flag),
the rest one record per subcommand. The first positional token that names a
subcommand hands the rest of argv to that subcommand's parser; its name lands in
the dest slot. The help text and the error prefix of every level arrive baked in
one blob of `name RS prog RS help` triples (RS is 0x1e, the top parser first
under the empty name), so the error paths print CPython's bytes: the usage block,
then `prog: error: message`, exit 2. Unrecognized tokens are collected and the
top parser reports them last, as CPython does.

`parse_args` walks the process argv, matches the option spellings and positional
arguments, and writes one value per registered dest into the namespace the compiler
allocated. The namespace holds one slot per dest, in registration order:
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

enum { PS_ARG_STR = 0, PS_ARG_INT = 1, PS_ARG_BOOL = 2, PS_ARG_FLOAT = 3 };

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
   the dest slot (-1 without a dest) and the required flag of the action. */
typedef struct {
    PsArgParser *child;
    char *name;
    int64_t dest_slot;
    int64_t required;
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

#define PS_ARG_PARTS 7
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

/* add_subparsers: record the dest slot and the required flag in the header. */
void ps_argparse_subs(PsArgParser *parser, int64_t dest_slot, int64_t required, int64_t nd, const char *dest,
                      PsSubParsers *out) {
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
        ps_seq_push(parser->subs, &header);
    }
}

/* add_parser: a fresh child parser registered under `name`. */
void ps_argparse_add_parser(PsSubParsers *holder, int64_t nn, const char *name, PsArgParser *out) {
    ps_argparse_new(out);
    PsArgSub *entry = calloc(1, sizeof(PsArgSub));
    if (entry == NULL) {
        abort();
    }
    entry->child = out;
    entry->name = ps_arg_dup(nn, name);
    ps_seq_push(holder->parent->subs, &entry);
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

static int ps_arg_option_like(const char *word) { return word[0] == '-' && word[1] != '\0' && !isdigit((unsigned char)word[1]); }

/* Write every default of this parser and its subcommands, so an option or a
   subcommand that does not appear keeps one. */
static void ps_arg_defaults(PsArgCtx *ctx, PsArgParser *parser) {
    int64_t nspecs = ps_seq_len(parser->specs);
    for (int64_t i = 0; i < nspecs; i++) {
        PsBuf row;
        ps_seq_get(parser->specs, i, &row);
        char *text = ps_arg_dup(row.len, row.data);
        char *parts[PS_ARG_PARTS] = {0};
        ps_arg_split(text, parts, PS_ARG_PARTS);
        int64_t slot = parts[6] ? atoll(parts[6]) : i;
        int64_t kind = ps_arg_kind(ctx->kinds, slot);
        int64_t offset = ps_arg_offset(ctx->kinds, slot);
        if (kind == PS_ARG_BOOL) {
            *(int64_t *)((char *)ctx->out + offset) = strcmp(parts[3] ? parts[3] : "0", "1") == 0 ? 1 : 0;
        } else if (parts[3] && strlen(parts[3]) > 0) {
            ps_arg_set(ctx->out, offset, kind, parts[3]);
        } else if (kind == PS_ARG_STR) {
            ps_arg_set_str(ctx->out, offset, "");
        } else if (kind == PS_ARG_FLOAT) {
            *(double *)((char *)ctx->out + offset) = 0.0;
        } else {
            *(int64_t *)((char *)ctx->out + offset) = 0;
        }
        free(text);
    }
    int64_t nsubs = ps_seq_len(parser->subs);
    for (int64_t i = 0; i < nsubs; i++) {
        PsArgSub *sub = NULL;
        ps_seq_get(parser->subs, i, &sub);
        if (sub->child == NULL) {
            if (sub->dest_slot >= 0) {
                ps_arg_set_str(ctx->out, ps_arg_offset(ctx->kinds, sub->dest_slot), "");
            }
        } else {
            ps_arg_defaults(ctx, sub->child);
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

/* The label of the subcommand action: its dest, else `{a,b}`. The caller frees it. */
static char *ps_arg_subs_label(PsArgParser *parser, PsArgSub *header) {
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

/* One parser level over argv[start..]. A subcommand token hands the rest of argv
   to the child level and the walk ends here (the required checks still run). */
static void ps_arg_level(PsArgCtx *ctx, PsArgParser *parser, const char *name, int64_t start) {
    PsArgLevel level = ps_arg_level_of(ctx, name);
    PsSeq *specs = parser->specs;
    int64_t nspecs = ps_seq_len(specs);
    int64_t nsubs = ps_seq_len(parser->subs);
    int64_t nargv = ps_seq_len(ctx->argv);
    PsArgSub *header = NULL;
    if (nsubs > 0) {
        ps_seq_get(parser->subs, 0, &header);
    }
    int dispatched = 0;
    int *seen = (int *)calloc((size_t)(nspecs > 0 ? nspecs : 1), sizeof(int));
    int end_of_options = 0;

    for (int64_t a = start; a < nargv; a++) {
        PsBuf token;
        ps_seq_get(ctx->argv, a, &token);
        char *word = ps_arg_dup(token.len, token.data);

        if (!end_of_options && strcmp(word, "--") == 0) {
            end_of_options = 1;
            free(word);
            continue;
        }
        if (!end_of_options && (strcmp(word, "-h") == 0 || strcmp(word, "--help") == 0)) {
            ps_arg_help(ctx, level);
        }

        if (!end_of_options && ps_arg_option_like(word)) {
            char *eq = strchr(word, '=');
            int64_t head_len = eq ? (int64_t)(eq - word) : (int64_t)strlen(word);
            const char *inline_value = eq ? eq + 1 : NULL;
            int matched = 0;
            for (int64_t i = 0; i < nspecs && !matched; i++) {
                PsBuf row;
                ps_seq_get(specs, i, &row);
                char *text = ps_arg_dup(row.len, row.data);
                char *parts[PS_ARG_PARTS] = {0};
                ps_arg_split(text, parts, PS_ARG_PARTS);
                if (parts[4] == NULL || strlen(parts[4]) == 0 ||
                    !ps_arg_matches(parts[4], (int64_t)strlen(parts[4]), word, head_len)) {
                    free(text);
                    continue;
                }
                matched = 1;
                seen[i] = 1;
                int64_t slot = parts[6] ? atoll(parts[6]) : i;
                int64_t kind = ps_arg_kind(ctx->kinds, slot);
                int64_t offset = ps_arg_offset(ctx->kinds, slot);
                const char *action = parts[1];
                if (kind == PS_ARG_BOOL) {
                    *(int64_t *)((char *)ctx->out + offset) = strcmp(action, "store_false") == 0 ? 0 : 1;
                } else if (strcmp(action, "store_const") == 0) {
                    ps_arg_set(ctx->out, offset, kind, parts[2] ? parts[2] : "");
                } else {
                    const char *value = inline_value;
                    char *owned = NULL;
                    if (value == NULL) {
                        char *label = ps_arg_label(parts[4], parts[0]);
                        PsBuf next;
                        int ok = 0;
                        if (a + 1 < nargv) {
                            ps_seq_get(ctx->argv, a + 1, &next);
                            owned = ps_arg_dup(next.len, next.data);
                            ok = !(strcmp(owned, "--") == 0 || ps_arg_option_like(owned));
                        }
                        if (!ok) {
                            ps_arg_error(ctx, level, "argument %s: expected one argument", label);
                        }
                        a += 1;
                        value = owned;
                        free(label);
                    }
                    if (!ps_arg_set(ctx->out, offset, kind, value)) {
                        char *label = ps_arg_label(parts[4], parts[0]);
                        ps_arg_error(ctx, level, "argument %s: invalid %s value: '%s'", label,
                                     kind == PS_ARG_FLOAT ? "float" : "int", value);
                    }
                    free(owned);
                }
                free(text);
            }
            if (!matched) {
                ps_arg_extra(ctx, word);
            }
        } else {
            int matched = 0;
            for (int64_t i = 0; i < nspecs && !matched; i++) {
                PsBuf row;
                ps_seq_get(specs, i, &row);
                char *text = ps_arg_dup(row.len, row.data);
                char *parts[PS_ARG_PARTS] = {0};
                ps_arg_split(text, parts, PS_ARG_PARTS);
                if ((parts[4] == NULL || strlen(parts[4]) == 0) && !seen[i]) {
                    matched = 1;
                    seen[i] = 1;
                    int64_t slot = parts[6] ? atoll(parts[6]) : i;
                    int64_t kind = ps_arg_kind(ctx->kinds, slot);
                    if (!ps_arg_set(ctx->out, ps_arg_offset(ctx->kinds, slot), kind, word)) {
                        ps_arg_error(ctx, level, "argument %s: invalid %s value: '%s'", parts[0],
                                     kind == PS_ARG_FLOAT ? "float" : "int", word);
                    }
                }
                free(text);
            }
            if (!matched && nsubs > 0 && !dispatched) {
                PsArgSub *found = NULL;
                for (int64_t i = 1; i < nsubs; i++) {
                    PsArgSub *sub = NULL;
                    ps_seq_get(parser->subs, i, &sub);
                    if (strcmp(sub->name, word) == 0) {
                        found = sub;
                        break;
                    }
                }
                if (found == NULL) {
                    char *label = ps_arg_subs_label(parser, header);
                    char *choices = ps_arg_choices_label(parser, ", ", "'");
                    ps_arg_error(ctx, level, "argument %s: invalid choice: '%s' (choose from %s)", label, word,
                                 choices);
                }
                matched = 1;
                dispatched = 1;
                if (header->dest_slot >= 0) {
                    ps_arg_set_str(ctx->out, ps_arg_offset(ctx->kinds, header->dest_slot), found->name);
                }
                ps_arg_level(ctx, found->child, found->name, a + 1);
                a = nargv;
            }
            if (!matched) {
                ps_arg_extra(ctx, word);
            }
        }
        free(word);
    }

    /* The required arguments and the required subcommand, reported together. */
    size_t cap = 64;
    for (int64_t i = 0; i < nspecs; i++) {
        PsBuf row;
        ps_seq_get(specs, i, &row);
        cap += (size_t)row.len + 4;
    }
    char *missing = malloc(cap + 256);
    if (missing == NULL) {
        abort();
    }
    missing[0] = '\0';
    int nmissing = 0;
    for (int64_t i = 0; i < nspecs; i++) {
        PsBuf row;
        ps_seq_get(specs, i, &row);
        char *text = ps_arg_dup(row.len, row.data);
        char *parts[PS_ARG_PARTS] = {0};
        ps_arg_split(text, parts, PS_ARG_PARTS);
        int req = (parts[5] && strcmp(parts[5], "1") == 0);
        if (req && !seen[i]) {
            char *label = ps_arg_label(parts[4], parts[0]);
            if (nmissing > 0) {
                strcat(missing, ", ");
            }
            strcat(missing, label);
            nmissing += 1;
            free(label);
        }
        free(text);
    }
    if (nsubs > 0 && header->required && !dispatched) {
        char *label = ps_arg_subs_label(parser, header);
        missing = realloc(missing, strlen(missing) + strlen(label) + 4);
        if (nmissing > 0) {
            strcat(missing, ", ");
        }
        strcat(missing, label);
        nmissing += 1;
        free(label);
    }
    if (nmissing > 0) {
        ps_arg_error(ctx, level, "the following arguments are required: %s", missing);
    }
    free(missing);
    free(seen);
}

static void ps_argparse_parse_from(PsArgParser *parser, PsSeq *kinds, int64_t blob_len, const char *blob, PsSeq *argv,
                                   void *out) {
    PsArgCtx ctx = {blob, blob_len, kinds, argv, out, ps_arg_basename(argv), NULL, 0, 0};
    ps_arg_defaults(&ctx, parser);
    ps_arg_level(&ctx, parser, "", 1);
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
