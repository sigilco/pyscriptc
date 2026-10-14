/* Backtracking subset of re. Patterns that can loop without consuming
(for example "()*") stop with RuntimeError once the fuel is spent.
ps_re_findall is the one-group form. Two to four groups go through
ps_re_findall_groups, which returns pointers to rows of PsBuf. A dynamic
pattern that actually has two or more groups still raises ValueError here. */

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

enum {
    OP_CHAR = 1,
    OP_ANY,
    OP_CLASS,
    OP_BOL,
    OP_EOL,
    OP_WB,
    OP_NWB,
    OP_BACK,
    OP_SPLIT,
    OP_JUMP,
    OP_SAVE,
    OP_MATCH
};

typedef struct Op {
    int op;
    int a;
    int b;
    int c;
} Op;

typedef struct Bits {
    unsigned char b[32];
    int negate;
} Bits;

typedef struct Comp {
    const char *p;
    int64_t n;
    int64_t i;
    Op *ops;
    int64_t len;
    int64_t cap;
    Bits *bits;
    int nbits;
    int groups;
    const char *err;
} Comp;

typedef struct Frame {
    int pc;
    int64_t sp;
    int64_t cs[32];
    int64_t ce[32];
} Frame;

typedef struct Eng {
    const char *text;
    int64_t tlen;
    Op *ops;
    Bits *bits;
    int64_t fuel;
    int64_t cs[32];
    int64_t ce[32];
    int64_t end;
    Frame *stack;
    int64_t top;
    int64_t scap;
    Comp *pool;
} Eng;

static void re_err(Comp *c, const char *msg) {
    if (c->err == NULL) {
        c->err = msg;
    }
}

static int emit(Comp *c, Op op) {
    if (c->err) {
        return 0;
    }
    if (c->len == c->cap) {
        int64_t cap = c->cap == 0 ? 32 : c->cap * 2;
        Op *ops = realloc(c->ops, (size_t)cap * sizeof(Op));
        if (ops == NULL) {
            abort();
        }
        c->ops = ops;
        c->cap = cap;
    }
    c->ops[c->len] = op;
    return (int)c->len++;
}

static void bit_set(Bits *bits, int ch) { bits->b[ch >> 3] |= (unsigned char)(1u << (ch & 7)); }

static int bit_has(const Bits *bits, int ch) { return (bits->b[ch >> 3] >> (ch & 7)) & 1; }

static int is_word(int ch) {
    return (ch >= 'A' && ch <= 'Z') || (ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '_';
}

static int starts_atom(Comp *c) {
    if (c->i >= c->n) {
        return 0;
    }
    char ch = c->p[c->i];
    if (ch == '|' || ch == ')') {
        return 0;
    }
    if (ch == '*' || ch == '+' || ch == '?') {
        re_err(c, "nothing to repeat");
        return 0;
    }
    return 1;
}

static void compile_alt(Comp *c);

static int skip_class(const char *p, int64_t n, int64_t i) {
    if (i < n && p[i] == '^') {
        i++;
    }
    if (i < n && p[i] == ']') {
        i++;
    }
    while (i < n && p[i] != ']') {
        if (p[i] == '\\' && i + 1 < n) {
            i += 2;
            continue;
        }
        i++;
    }
    if (i < n && p[i] == ']') {
        i++;
    }
    return (int)i;
}

static int has_top_alt(Comp *c) {
    int64_t i = c->i;
    int depth = 0;
    while (i < c->n) {
        char ch = c->p[i];
        if (ch == '\\') {
            i += 2;
            continue;
        }
        if (ch == '[') {
            i = skip_class(c->p, c->n, i + 1);
            continue;
        }
        if (ch == '(') {
            depth++;
        } else if (ch == ')') {
            if (depth == 0) {
                break;
            }
            depth--;
        } else if (ch == '|' && depth == 0) {
            return 1;
        }
        i++;
    }
    return 0;
}

static void add_class_escape(Comp *c, Bits *bits, char e) {
    if (e == 'd') {
        for (int ch = '0'; ch <= '9'; ch++) {
            bit_set(bits, ch);
        }
        return;
    }
    if (e == 'D') {
        for (int ch = 0; ch < 256; ch++) {
            if (ch < '0' || ch > '9') {
                bit_set(bits, ch);
            }
        }
        return;
    }
    if (e == 'w') {
        for (int ch = 0; ch < 256; ch++) {
            if (is_word(ch)) {
                bit_set(bits, ch);
            }
        }
        return;
    }
    if (e == 'W') {
        for (int ch = 0; ch < 256; ch++) {
            if (!is_word(ch)) {
                bit_set(bits, ch);
            }
        }
        return;
    }
    if (e == 's') {
        bit_set(bits, ' ');
        bit_set(bits, '\t');
        bit_set(bits, '\n');
        bit_set(bits, '\r');
        bit_set(bits, '\f');
        bit_set(bits, '\v');
        return;
    }
    if (e == 'S') {
        for (int ch = 0; ch < 256; ch++) {
            if (ch != ' ' && ch != '\t' && ch != '\n' && ch != '\r' && ch != '\f' && ch != '\v') {
                bit_set(bits, ch);
            }
        }
        return;
    }
    if (e == 'n') {
        bit_set(bits, '\n');
        return;
    }
    if (e == 't') {
        bit_set(bits, '\t');
        return;
    }
    if (e == 'r') {
        bit_set(bits, '\r');
        return;
    }
    if (e == 'b') {
        bit_set(bits, '\b');
        return;
    }
    if ((e >= 'A' && e <= 'Z') || (e >= 'a' && e <= 'z') || (e >= '0' && e <= '9')) {
        re_err(c, "bad escape");
        return;
    }
    bit_set(bits, (unsigned char)e);
}

static void compile_class(Comp *c) {
    c->i++;
    Bits bits;
    memset(&bits, 0, sizeof bits);
    if (c->i < c->n && c->p[c->i] == '^') {
        bits.negate = 1;
        c->i++;
    }
    int first = 1;
    if (c->i >= c->n) {
        re_err(c, "unterminated character set");
        return;
    }
    while (c->i < c->n && !(c->p[c->i] == ']' && !first)) {
        first = 0;
        unsigned char ch;
        if (c->p[c->i] == '\\') {
            if (c->i + 1 >= c->n) {
                re_err(c, "bad escape");
                return;
            }
            char e = c->p[c->i + 1];
            c->i += 2;
            if (e == 'd' || e == 'D' || e == 'w' || e == 'W' || e == 's' || e == 'S') {
                add_class_escape(c, &bits, e);
                continue;
            }
            add_class_escape(c, &bits, e);
            ch = (unsigned char)e;
            if (e == 'n') {
                ch = '\n';
            } else if (e == 't') {
                ch = '\t';
            } else if (e == 'r') {
                ch = '\r';
            } else if (e == 'b') {
                ch = '\b';
            }
        } else {
            ch = (unsigned char)c->p[c->i++];
        }
        if (c->i + 1 < c->n && c->p[c->i] == '-' && c->p[c->i + 1] != ']') {
            c->i++;
            unsigned char end;
            if (c->p[c->i] == '\\') {
                re_err(c, "unsupported class range");
                return;
            }
            end = (unsigned char)c->p[c->i++];
            if (end < ch) {
                re_err(c, "bad character range");
                return;
            }
            for (int k = ch; k <= end; k++) {
                bit_set(&bits, k);
            }
        } else if (!(c->p[c->i - 1] == '\\' && (ch == 'd' || ch == 'w' || ch == 's'))) {
            /* Single characters were already set for ordinary chars. Escapes that
            expanded a set returned early. A plain character still needs a bit. */
            if (c->i >= 1 && c->p[c->i - 1] != '\\') {
                bit_set(&bits, ch);
            }
        }
    }
    if (c->i >= c->n || c->p[c->i] != ']') {
        re_err(c, "unterminated character set");
        return;
    }
    c->i++;
    if (c->nbits == 0) {
        c->bits = NULL;
    }
    c->bits = realloc(c->bits, (size_t)(c->nbits + 1) * sizeof(Bits));
    if (c->bits == NULL) {
        abort();
    }
    c->bits[c->nbits] = bits;
    Op op = {OP_CLASS, c->nbits, 0, 0};
    c->nbits++;
    emit(c, op);
}

static void compile_escape(Comp *c) {
    if (c->i + 1 >= c->n) {
        re_err(c, "bad escape");
        return;
    }
    char e = c->p[++c->i];
    c->i++;
    if (e >= '1' && e <= '9') {
        int g = e - '0';
        Op op = {OP_BACK, g, 0, 0};
        emit(c, op);
        return;
    }
    if (e == 'd' || e == 'D' || e == 'w' || e == 'W' || e == 's' || e == 'S') {
        Bits bits;
        memset(&bits, 0, sizeof bits);
        add_class_escape(c, &bits, e);
        c->bits = realloc(c->bits, (size_t)(c->nbits + 1) * sizeof(Bits));
        if (c->bits == NULL) {
            abort();
        }
        c->bits[c->nbits] = bits;
        Op op = {OP_CLASS, c->nbits, 0, 0};
        c->nbits++;
        emit(c, op);
        return;
    }
    if (e == 'n' || e == 't' || e == 'r') {
        char ch = e == 'n' ? '\n' : e == 't' ? '\t' : '\r';
        Op op = {OP_CHAR, 0, 0, (unsigned char)ch};
        emit(c, op);
        return;
    }
    if (e == 'b') {
        Op op = {OP_WB, 0, 0, 0};
        emit(c, op);
        return;
    }
    if (e == 'B') {
        Op op = {OP_NWB, 0, 0, 0};
        emit(c, op);
        return;
    }
    if ((e >= 'A' && e <= 'Z') || (e >= 'a' && e <= 'z') || e == '0') {
        re_err(c, "bad escape");
        return;
    }
    Op op = {OP_CHAR, 0, 0, (unsigned char)e};
    emit(c, op);
}

static void compile_atom(Comp *c) {
    if (c->i >= c->n) {
        re_err(c, "unexpected end of pattern");
        return;
    }
    char ch = c->p[c->i];
    if (ch == '(') {
        c->i++;
        int cap = 1;
        if (c->i + 1 < c->n && c->p[c->i] == '?' && c->p[c->i + 1] == ':') {
            cap = 0;
            c->i += 2;
        } else if (c->i < c->n && c->p[c->i] == '?') {
            re_err(c, "unsupported regular expression");
            return;
        }
        int g = 0;
        if (cap) {
            c->groups++;
            if (c->groups >= 32) {
                re_err(c, "too many capturing groups");
                return;
            }
            g = c->groups;
            Op save = {OP_SAVE, g, 0, 0};
            emit(c, save);
        }
        compile_alt(c);
        if (c->i >= c->n || c->p[c->i] != ')') {
            re_err(c, "missing )");
            return;
        }
        c->i++;
        if (cap) {
            Op save = {OP_SAVE, g, 1, 0};
            emit(c, save);
        }
        return;
    }
    if (ch == '[') {
        compile_class(c);
        return;
    }
    if (ch == '^') {
        c->i++;
        Op op = {OP_BOL, 0, 0, 0};
        emit(c, op);
        return;
    }
    if (ch == '$') {
        c->i++;
        Op op = {OP_EOL, 0, 0, 0};
        emit(c, op);
        return;
    }
    if (ch == '.') {
        c->i++;
        Op op = {OP_ANY, 0, 0, 0};
        emit(c, op);
        return;
    }
    if (ch == '\\') {
        compile_escape(c);
        return;
    }
    c->i++;
    Op op = {OP_CHAR, 0, 0, (unsigned char)ch};
    emit(c, op);
}

static void relocate(Op *ops, int count, int delta) {
    for (int i = 0; i < count; i++) {
        if (ops[i].op == OP_SPLIT || ops[i].op == OP_JUMP) {
            ops[i].a += delta;
            if (ops[i].op == OP_SPLIT) {
                ops[i].b += delta;
            }
        }
    }
}

static void append_ops(Comp *c, Op *ops, int count, int base_was) {
    int delta = (int)c->len - base_was;
    for (int i = 0; i < count; i++) {
        Op op = ops[i];
        if (op.op == OP_SPLIT || op.op == OP_JUMP) {
            op.a += delta;
            if (op.op == OP_SPLIT) {
                op.b += delta;
            }
        }
        emit(c, op);
    }
}

static int parse_quant(Comp *c, int *min, int *max, int *greedy) {
    if (c->i >= c->n) {
        return 0;
    }
    char ch = c->p[c->i];
    if (ch == '*') {
        *min = 0;
        *max = -1;
        c->i++;
    } else if (ch == '+') {
        *min = 1;
        *max = -1;
        c->i++;
    } else if (ch == '?') {
        *min = 0;
        *max = 1;
        c->i++;
    } else if (ch == '{') {
        int64_t save = c->i;
        c->i++;
        int saw_num = 0;
        int lo = 0;
        while (c->i < c->n && c->p[c->i] >= '0' && c->p[c->i] <= '9') {
            saw_num = 1;
            lo = lo * 10 + (c->p[c->i] - '0');
            if (lo > 100000) {
                re_err(c, "repeat is too large");
                return 0;
            }
            c->i++;
        }
        int hi = lo;
        int unbounded = 0;
        if (c->i < c->n && c->p[c->i] == ',') {
            c->i++;
            if (c->i < c->n && c->p[c->i] >= '0' && c->p[c->i] <= '9') {
                hi = 0;
                while (c->i < c->n && c->p[c->i] >= '0' && c->p[c->i] <= '9') {
                    hi = hi * 10 + (c->p[c->i] - '0');
                    if (hi > 100000) {
                        re_err(c, "repeat is too large");
                        return 0;
                    }
                    c->i++;
                }
            } else {
                unbounded = 1;
                hi = -1;
            }
        } else if (!saw_num) {
            c->i = save;
            return 0;
        }
        if (c->i >= c->n || c->p[c->i] != '}') {
            c->i = save;
            return 0;
        }
        c->i++;
        if (!unbounded && hi < lo) {
            re_err(c, "min repeat greater than max");
            return 0;
        }
        *min = saw_num ? lo : 0;
        *max = unbounded ? -1 : hi;
    } else {
        return 0;
    }
    *greedy = 1;
    if (c->i < c->n && c->p[c->i] == '?') {
        *greedy = 0;
        c->i++;
    }
    return 1;
}

static void emit_copy(Comp *c, Op *atom, int count) {
    append_ops(c, atom, count, 0);
}

static void emit_star(Comp *c, Op *atom, int count, int greedy) {
    int split = emit(c, (Op){OP_SPLIT, 0, 0, 0});
    int take = (int)c->len;
    emit_copy(c, atom, count);
    emit(c, (Op){OP_JUMP, split, 0, 0});
    int done = (int)c->len;
    if (greedy) {
        c->ops[split].a = take;
        c->ops[split].b = done;
    } else {
        c->ops[split].a = done;
        c->ops[split].b = take;
    }
}

static void emit_opt(Comp *c, Op *atom, int count, int greedy) {
    int split = emit(c, (Op){OP_SPLIT, 0, 0, 0});
    int take = (int)c->len;
    emit_copy(c, atom, count);
    int done = (int)c->len;
    if (greedy) {
        c->ops[split].a = take;
        c->ops[split].b = done;
    } else {
        c->ops[split].a = done;
        c->ops[split].b = take;
    }
}

static void compile_repeat(Comp *c) {
    int op_at = (int)c->len;
    compile_atom(c);
    if (c->err) {
        return;
    }
    int count = (int)c->len - op_at;
    int min = 0;
    int max = 0;
    int greedy = 1;
    if (!parse_quant(c, &min, &max, &greedy)) {
        return;
    }
    if (min > 64 || (max >= 0 && max - min > 64)) {
        re_err(c, "repeat is too large");
        return;
    }
    Op *atom = malloc((size_t)count * sizeof(Op));
    if (atom == NULL) {
        abort();
    }
    memcpy(atom, c->ops + op_at, (size_t)count * sizeof(Op));
    relocate(atom, count, -op_at);
    c->len = op_at;
    for (int k = 0; k < min; k++) {
        emit_copy(c, atom, count);
    }
    if (max < 0) {
        emit_star(c, atom, count, greedy);
    } else {
        for (int k = min; k < max; k++) {
            emit_opt(c, atom, count, greedy);
        }
    }
    free(atom);
}

static void compile_concat(Comp *c) {
    while (starts_atom(c) && c->err == NULL) {
        compile_repeat(c);
    }
}

static void compile_alt(Comp *c) {
    if (!has_top_alt(c)) {
        compile_concat(c);
        return;
    }
    int split = emit(c, (Op){OP_SPLIT, 0, 0, 0});
    compile_concat(c);
    int jump = emit(c, (Op){OP_JUMP, 0, 0, 0});
    if (c->i >= c->n || c->p[c->i] != '|') {
        re_err(c, "expected alternation");
        return;
    }
    c->i++;
    int right = (int)c->len;
    compile_alt(c);
    int end = (int)c->len;
    c->ops[split].a = split + 1;
    c->ops[split].b = right;
    c->ops[jump].a = end;
}

static void compile_pattern(Comp *c) {
    compile_alt(c);
    if (c->err) {
        return;
    }
    if (c->i != c->n) {
        re_err(c, "unbalanced pattern");
        return;
    }
    emit(c, (Op){OP_MATCH, 0, 0, 0});
}

static void eng_fail(Eng *e, int code, const char *msg) {
    free(e->stack);
    e->stack = NULL;
    if (e->pool) {
        free(e->pool->ops);
        free(e->pool->bits);
        e->pool->ops = NULL;
        e->pool->bits = NULL;
    }
    ps_raise_cstr(code, msg);
}

static void eng_push(Eng *e, int pc, int64_t sp) {
    if (e->top == e->scap) {
        int64_t cap = e->scap == 0 ? 64 : e->scap * 2;
        if (cap > 1000000) {
            eng_fail(e, PS_RUNTIME, "regular expression fuel exhausted");
        }
        Frame *stack = realloc(e->stack, (size_t)cap * sizeof(Frame));
        if (stack == NULL) {
            abort();
        }
        e->stack = stack;
        e->scap = cap;
    }
    Frame *fr = &e->stack[e->top++];
    fr->pc = pc;
    fr->sp = sp;
    memcpy(fr->cs, e->cs, sizeof fr->cs);
    memcpy(fr->ce, e->ce, sizeof fr->ce);
}

static int eng_pop(Eng *e, int *pc, int64_t *sp) {
    if (e->top == 0) {
        return 0;
    }
    Frame *fr = &e->stack[--e->top];
    *pc = fr->pc;
    *sp = fr->sp;
    memcpy(e->cs, fr->cs, sizeof e->cs);
    memcpy(e->ce, fr->ce, sizeof e->ce);
    return 1;
}

static int word_at(Eng *e, int64_t sp) {
    if (sp < 0 || sp >= e->tlen) {
        return 0;
    }
    return is_word((unsigned char)e->text[sp]);
}

static int eng_run(Eng *e, int pc, int64_t sp) {
    while (1) {
        if (--e->fuel < 0) {
            eng_fail(e, PS_RUNTIME, "regular expression fuel exhausted");
        }
        Op op = e->ops[pc];
        switch (op.op) {
        case OP_CHAR:
            if (sp < e->tlen && (unsigned char)e->text[sp] == (unsigned char)op.c) {
                pc++;
                sp++;
                break;
            }
            if (!eng_pop(e, &pc, &sp)) {
                return 0;
            }
            break;
        case OP_ANY:
            if (sp < e->tlen && e->text[sp] != '\n') {
                pc++;
                sp++;
                break;
            }
            if (!eng_pop(e, &pc, &sp)) {
                return 0;
            }
            break;
        case OP_CLASS: {
            int ok = 0;
            if (sp < e->tlen) {
                int has = bit_has(&e->bits[op.a], (unsigned char)e->text[sp]);
                ok = e->bits[op.a].negate ? !has : has;
            }
            if (ok) {
                pc++;
                sp++;
                break;
            }
            if (!eng_pop(e, &pc, &sp)) {
                return 0;
            }
            break;
        }
        case OP_BOL:
            if (sp == 0) {
                pc++;
                break;
            }
            if (!eng_pop(e, &pc, &sp)) {
                return 0;
            }
            break;
        case OP_EOL:
            if (sp == e->tlen || (sp == e->tlen - 1 && e->text[sp] == '\n')) {
                pc++;
                break;
            }
            if (!eng_pop(e, &pc, &sp)) {
                return 0;
            }
            break;
        case OP_WB:
        case OP_NWB: {
            int bound = word_at(e, sp - 1) != word_at(e, sp);
            int ok = op.op == OP_WB ? bound : !bound;
            if (ok) {
                pc++;
                break;
            }
            if (!eng_pop(e, &pc, &sp)) {
                return 0;
            }
            break;
        }
        case OP_BACK: {
            int g = op.a;
            if (e->cs[g] < 0 || e->ce[g] < e->cs[g]) {
                if (!eng_pop(e, &pc, &sp)) {
                    return 0;
                }
                break;
            }
            int64_t n = e->ce[g] - e->cs[g];
            if (sp + n <= e->tlen && (n == 0 || memcmp(e->text + sp, e->text + e->cs[g], (size_t)n) == 0)) {
                sp += n;
                pc++;
                break;
            }
            if (!eng_pop(e, &pc, &sp)) {
                return 0;
            }
            break;
        }
        case OP_SPLIT:
            eng_push(e, op.b, sp);
            pc = op.a;
            break;
        case OP_JUMP:
            pc = op.a;
            break;
        case OP_SAVE:
            if (op.b == 0) {
                e->cs[op.a] = sp;
            } else {
                e->ce[op.a] = sp;
            }
            pc++;
            break;
        case OP_MATCH:
            e->end = sp;
            return 1;
        default:
            eng_fail(e, PS_VALUE, "bad regular expression program");
        }
    }
}

static void eng_clear(Eng *e) {
    for (int i = 0; i < 32; i++) {
        e->cs[i] = -1;
        e->ce[i] = -1;
    }
    e->top = 0;
}

static int eng_search(Eng *e, int64_t pos, int64_t *start, int64_t *end) {
    for (int64_t i = pos; i <= e->tlen; i++) {
        eng_clear(e);
        if (eng_run(e, 0, i)) {
            *start = i;
            *end = e->end;
            e->cs[0] = i;
            e->ce[0] = e->end;
            return 1;
        }
    }
    return 0;
}

static void compile_or_raise(Comp *c) {
    compile_pattern(c);
    if (c->err) {
        const char *msg = c->err;
        free(c->ops);
        free(c->bits);
        c->ops = NULL;
        c->bits = NULL;
        ps_raise_cstr(PS_VALUE, msg);
    }
}

static void eng_init(Eng *e, Comp *c, const char *text, int64_t tlen) {
    memset(e, 0, sizeof *e);
    e->text = text;
    e->tlen = tlen;
    e->ops = c->ops;
    e->bits = c->bits;
    e->fuel = 2000000;
    e->pool = c;
    eng_clear(e);
}

static void eng_free(Eng *e) {
    free(e->stack);
    free(e->pool->ops);
    free(e->pool->bits);
}

static PsBuf slice_of(const char *text, int64_t a, int64_t b) {
    PsBuf out;
    if (a < 0 || b < a) {
        ps_buf_copy(&out, "", 0);
        return out;
    }
    ps_buf_view(&out, text + a, b - a);
    return out;
}

static void push_piece(PsSeq *seq, const char *text, int64_t a, int64_t b) {
    PsBuf piece = slice_of(text, a, b);
    ps_seq_push(seq, &piece);
}

PsSeq *ps_re_findall(int64_t pn, char *pat, int64_t tn, char *text) {
    Comp comp;
    memset(&comp, 0, sizeof comp);
    comp.p = pat;
    comp.n = pn;
    compile_or_raise(&comp);
    if (comp.groups >= 2) {
        free(comp.ops);
        free(comp.bits);
        ps_raise_cstr(PS_VALUE, "re.findall with multiple capturing groups returns tuples");
    }
    Eng eng;
    eng_init(&eng, &comp, text, tn);
    PsSeq *seq = ps_seq_new(16, 0);
    int64_t pos = 0;
    while (pos <= tn) {
        int64_t a = 0;
        int64_t b = 0;
        if (!eng_search(&eng, pos, &a, &b)) {
            break;
        }
        if (comp.groups == 0) {
            push_piece(seq, text, a, b);
        } else if (eng.cs[1] < 0) {
            push_piece(seq, text, 0, 0);
        } else {
            push_piece(seq, text, eng.cs[1], eng.ce[1]);
        }
        if (b > a) {
            pos = b;
        } else if (b == tn) {
            break;
        } else {
            pos = b + 1;
        }
    }
    eng_free(&eng);
    return seq;
}

PsSeq *ps_re_findall_groups(int64_t pn, char *pat, int64_t tn, char *text) {
    Comp comp;
    int g;
    memset(&comp, 0, sizeof comp);
    comp.p = pat;
    comp.n = pn;
    compile_or_raise(&comp);
    if (comp.groups < 2 || comp.groups > 4) {
        free(comp.ops);
        free(comp.bits);
        ps_raise_cstr(PS_VALUE, "re.findall group count is not 2 to 4");
    }
    Eng eng;
    eng_init(&eng, &comp, text, tn);
    PsSeq *seq = ps_seq_new((int64_t)sizeof(void *), 0);
    int64_t pos = 0;
    while (pos <= tn) {
        int64_t a = 0;
        int64_t b = 0;
        PsBuf *row;
        if (!eng_search(&eng, pos, &a, &b)) {
            break;
        }
        row = malloc(sizeof(PsBuf) * (size_t)comp.groups);
        if (row == NULL) {
            abort();
        }
        for (g = 0; g < comp.groups; g++) {
            if (eng.cs[g + 1] < 0) {
                ps_buf_copy(&row[g], "", 0);
            } else {
                row[g] = slice_of(text, eng.cs[g + 1], eng.ce[g + 1]);
            }
        }
        ps_seq_push(seq, &row);
        if (b > a) {
            pos = b;
        } else if (b == tn) {
            break;
        } else {
            pos = b + 1;
        }
    }
    eng_free(&eng);
    return seq;
}

static int repl_expand(Eng *e, const char *repl, int64_t rn, int groups, PsGrow *g) {
    for (int64_t i = 0; i < rn; i++) {
        char ch = repl[i];
        if (ch != '\\') {
            ps_grow_byte(g, (unsigned char)ch);
            continue;
        }
        if (i + 1 >= rn) {
            return 0;
        }
        char ech = repl[++i];
        if (ech == '\\') {
            ps_grow_byte(g, '\\');
            continue;
        }
        if (ech == '0' && (i + 1 >= rn || repl[i + 1] < '0' || repl[i + 1] > '9')) {
            ps_grow_byte(g, 0);
            continue;
        }
        if (ech >= '1' && ech <= '9') {
            int val = ech - '0';
            while (i + 1 < rn && repl[i + 1] >= '0' && repl[i + 1] <= '9') {
                val = val * 10 + (repl[i + 1] - '0');
                i++;
            }
            if (val < 1 || val > groups) {
                return 0;
            }
            if (e->cs[val] >= 0) {
                ps_grow_add(g, e->text + e->cs[val], e->ce[val] - e->cs[val]);
            }
            continue;
        }
        return 0;
    }
    return 1;
}

void ps_re_sub(int64_t pn, char *pat, int64_t rn, char *repl, int64_t tn, char *text, PsBuf *out) {
    Comp comp;
    memset(&comp, 0, sizeof comp);
    comp.p = pat;
    comp.n = pn;
    compile_or_raise(&comp);
    Eng eng;
    eng_init(&eng, &comp, text, tn);
    PsGrow g;
    ps_grow_init(&g);
    int64_t pos = 0;
    while (pos <= tn) {
        int64_t a = 0;
        int64_t b = 0;
        if (!eng_search(&eng, pos, &a, &b)) {
            ps_grow_add(&g, text + pos, tn - pos);
            break;
        }
        ps_grow_add(&g, text + pos, a - pos);
        if (!repl_expand(&eng, repl, rn, comp.groups, &g)) {
            free(g.data);
            eng_free(&eng);
            ps_raise_cstr(PS_VALUE, "bad escape in replacement");
        }
        if (b > a) {
            pos = b;
        } else if (b == tn) {
            break;
        } else {
            ps_grow_add(&g, text + b, 1);
            pos = b + 1;
        }
    }
    eng_free(&eng);
    ps_grow_finish(&g, out);
}

PsSeq *ps_re_split(int64_t pn, char *pat, int64_t tn, char *text) {
    Comp comp;
    memset(&comp, 0, sizeof comp);
    comp.p = pat;
    comp.n = pn;
    compile_or_raise(&comp);
    Eng eng;
    eng_init(&eng, &comp, text, tn);
    PsSeq *seq = ps_seq_new(16, 0);
    int64_t last = 0;
    int64_t pos = 0;
    while (pos <= tn) {
        int64_t a = 0;
        int64_t b = 0;
        if (!eng_search(&eng, pos, &a, &b)) {
            push_piece(seq, text, last, tn);
            break;
        }
        push_piece(seq, text, last, a);
        for (int g = 1; g <= comp.groups; g++) {
            if (eng.cs[g] < 0) {
                push_piece(seq, text, 0, 0);
            } else {
                push_piece(seq, text, eng.cs[g], eng.ce[g]);
            }
        }
        if (b > a) {
            last = b;
            pos = b;
        } else if (b == tn) {
            push_piece(seq, text, last, tn);
            break;
        } else {
            last = b;
            pos = b + 1;
        }
    }
    eng_free(&eng);
    return seq;
}

void ps_re_escape(int64_t n, char *p, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    for (int64_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        int plain = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
        if (!plain) {
            ps_grow_byte(&g, '\\');
        }
        ps_grow_byte(&g, c);
    }
    ps_grow_finish(&g, out);
}
