/* textwrap: a C port of CPython 3.13's TextWrapper at the lowered
   defaults: width from the call, initial/subsequent indent '',
   drop_whitespace, replace_whitespace, expand_tabs(8), break_long_words
   and break_on_hyphens all true, fix_sentence_endings false, and
   max_lines only through shorten() (placeholder ' [...]').
   The chunk splitter matches CPython's wordsep_re on ASCII input. */
#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int wrap_ws(unsigned char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\v' || c == '\f' || c == '\r';
}

/* [^\d\W]: a word char that is not a digit — ASCII letters, '_', and
   (approximately) non-ASCII bytes. */
static int wrap_letter(unsigned char c) {
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_' || c >= 0x80;
}

static int wrap_word(unsigned char c) { /* \w */
    return wrap_letter(c) || (c >= '0' && c <= '9');
}

static int wrap_wp(unsigned char c) { /* [\w!"'&.,?] */
    return wrap_word(c) || c == '!' || c == '"' || c == '\'' || c == '&' || c == '.' || c == ',' ||
           c == '?';
}

static void push_range(PsSeq *chunks, const char *p, int64_t a, int64_t b) {
    PsBuf buf;
    ps_buf_copy(&buf, p + a, b - a);
    ps_seq_push(chunks, &buf);
}

/* _munge_whitespace: expandtabs(8) then map each of \t\n\v\f\r to ' '. */
static void munge_ws(int64_t n, const char *p, PsBuf *out) {
    PsGrow g;
    int64_t i;
    int64_t col = 0;
    ps_grow_init(&g);
    for (i = 0; i < n; i++) {
        unsigned char c = (unsigned char)p[i];
        if (c == '\t') {
            int64_t fill = 8 - (col % 8);
            while (fill-- > 0) {
                ps_grow_byte(&g, ' ');
                col++;
            }
            continue;
        }
        if (c == '\n' || c == '\r' || c == '\v' || c == '\f') {
            ps_grow_byte(&g, ' ');
            if (c == '\n' || c == '\r') {
                col = 0;
            } else {
                col++;
            }
            continue;
        }
        ps_grow_byte(&g, c);
        col++;
    }
    ps_grow_finish(&g, out);
}

/* wordsep_re equivalent: whitespace runs stay together; a run of 2+
   hyphens between a word-punct char and a word char is its own chunk;
   word chunks can split after a hyphen that is preceded by two letters
   or by letter-hyphen-letter and followed by letter(-optional-hyphen)letter. */
static PsSeq *wrap_split(const char *p, int64_t n) {
    PsSeq *chunks = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    int64_t i = 0;
    while (i < n) {
        unsigned char c = (unsigned char)p[i];
        int64_t a;
        if (wrap_ws(c)) {
            a = i;
            while (i < n && wrap_ws((unsigned char)p[i])) {
                i++;
            }
            push_range(chunks, p, a, i);
            continue;
        }
        if (c == '-' && i > 0 && wrap_wp((unsigned char)p[i - 1])) {
            int64_t j = i;
            while (j < n && p[j] == '-') {
                j++;
            }
            if (j - i >= 2 && j < n && wrap_word((unsigned char)p[j])) {
                push_range(chunks, p, i, j);
                i = j;
                continue;
            }
        }
        a = i;
        while (i < n && !wrap_ws((unsigned char)p[i])) {
            if (p[i] == '-') {
                int64_t j = i;
                int64_t h = i;
                int split = 0;
                while (j < n && p[j] == '-') {
                    j++;
                }
                if (j - i >= 2 && i > a && wrap_wp((unsigned char)p[i - 1]) && j < n &&
                    wrap_word((unsigned char)p[j])) {
                    break;
                }
                i++;
                if ((h >= 2 && wrap_letter((unsigned char)p[h - 2]) && wrap_letter((unsigned char)p[h - 1])) ||
                    (h >= 3 && wrap_letter((unsigned char)p[h - 3]) && p[h - 2] == '-' &&
                     wrap_letter((unsigned char)p[h - 1]))) {
                    if (h + 1 < n && wrap_letter((unsigned char)p[h + 1]) &&
                        ((h + 2 < n && wrap_letter((unsigned char)p[h + 2])) ||
                         (h + 2 < n && p[h + 2] == '-' && h + 3 < n &&
                          wrap_letter((unsigned char)p[h + 3])))) {
                        split = 1;
                    }
                }
                if (split) {
                    break;
                }
                continue;
            }
            i++;
        }
        push_range(chunks, p, a, i);
    }
    return chunks;
}

static int buf_all_ws(const PsBuf *b) {
    int64_t i;
    for (i = 0; i < b->len; i++) {
        if (!wrap_ws((unsigned char)b->data[i])) {
            return 0;
        }
    }
    return 1;
}

static PsBuf *seq_buf_at(PsSeq *s, int64_t i) { return (PsBuf *)s->data + i; }

static void push_line(PsSeq *lines, PsSeq *cur) {
    PsGrow g;
    PsBuf b;
    int64_t i;
    ps_grow_init(&g);
    for (i = 0; i < cur->len; i++) {
        PsBuf *part = seq_buf_at(cur, i);
        if (part->len > 0) {
            ps_grow_add(&g, part->data, part->len);
        }
    }
    ps_grow_finish(&g, &b);
    ps_seq_push(lines, &b);
}

#define PLACEHOLDER " [...]"
#define PLACEHOLDER_BARE "[...]"

static PsSeq *wrap_run(PsSeq *chunks, int64_t width, int64_t max_lines) {
    PsSeq *lines = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    int64_t ci = chunks->len; /* live range is chunks[0..ci); next is ci-1 */
    if (width <= 0) {
        char msg[64];
        snprintf(msg, sizeof(msg), "invalid width %d (must be > 0)", (int)width);
        ps_raise_cstr(PS_VALUE, msg);
    }
    if (max_lines > 0 && (int64_t)sizeof(PLACEHOLDER_BARE) - 1 > width) {
        ps_raise_cstr(PS_VALUE, "placeholder too large for max width");
    }
    {
        int64_t lo = 0;
        int64_t hi = chunks->len - 1;
        while (lo < hi) {
            PsBuf tmp = *seq_buf_at(chunks, lo);
            *seq_buf_at(chunks, lo) = *seq_buf_at(chunks, hi);
            *seq_buf_at(chunks, hi) = tmp;
            lo++;
            hi--;
        }
    }
    while (ci > 0) {
        PsSeq *cur = ps_seq_new((int64_t)sizeof(PsBuf), 0);
        int64_t cur_len = 0;
        if (lines->len > 0 && buf_all_ws(seq_buf_at(chunks, ci - 1))) {
            ci--;
        }
        while (ci > 0) {
            PsBuf *top = seq_buf_at(chunks, ci - 1);
            if (cur_len + top->len <= width) {
                ps_seq_push(cur, top);
                cur_len += top->len;
                ci--;
            } else {
                break;
            }
        }
        if (ci > 0 && seq_buf_at(chunks, ci - 1)->len > width) {
            PsBuf *top = seq_buf_at(chunks, ci - 1);
            int64_t space_left = width < 1 ? 1 : width - cur_len;
            if (space_left > 0) {
                int64_t end = space_left;
                if (top->len > space_left) {
                    int64_t hy;
                    for (hy = space_left - 1; hy >= 0; hy--) {
                        if (top->data[hy] == '-') {
                            break;
                        }
                    }
                    if (hy > 0) {
                        int64_t j;
                        int non = 0;
                        for (j = 0; j < hy; j++) {
                            if (top->data[j] != '-') {
                                non = 1;
                                break;
                            }
                        }
                        if (non) {
                            end = hy + 1;
                        }
                    }
                }
                if (end > 0) {
                    PsBuf piece;
                    ps_buf_copy(&piece, top->data, end);
                    ps_seq_push(cur, &piece);
                    top->data += end;
                    top->len -= end;
                }
            } else if (cur->len == 0) {
                ps_seq_push(cur, top);
                ci--;
            }
            cur_len = 0;
            {
                int64_t i;
                for (i = 0; i < cur->len; i++) {
                    cur_len += seq_buf_at(cur, i)->len;
                }
            }
        }
        if (cur->len > 0 && buf_all_ws(seq_buf_at(cur, cur->len - 1))) {
            cur_len -= seq_buf_at(cur, cur->len - 1)->len;
            cur->len--;
        }
        if (cur->len > 0) {
            int emit = 1;
            if (max_lines > 0 && lines->len + 1 >= max_lines) {
                emit = (ci == 0 || (ci == 1 && buf_all_ws(seq_buf_at(chunks, 0)))) && cur_len <= width;
            }
            if (emit) {
                push_line(lines, cur);
            } else {
                int done = 0;
                while (cur->len > 0) {
                    PsBuf *last = seq_buf_at(cur, cur->len - 1);
                    if (!buf_all_ws(last) && cur_len + (int64_t)sizeof(PLACEHOLDER) - 1 <= width) {
                        PsBuf ph;
                        ps_buf_copy(&ph, PLACEHOLDER, (int64_t)sizeof(PLACEHOLDER) - 1);
                        ps_seq_push(cur, &ph);
                        push_line(lines, cur);
                        done = 1;
                        break;
                    }
                    cur_len -= last->len;
                    cur->len--;
                }
                if (!done) {
                    if (lines->len > 0) {
                        PsBuf *prev = seq_buf_at(lines, lines->len - 1);
                        int64_t e = prev->len;
                        while (e > 0 && wrap_ws((unsigned char)prev->data[e - 1])) {
                            e--;
                        }
                        prev->len = e;
                        if (prev->len + (int64_t)sizeof(PLACEHOLDER) - 1 <= width) {
                            PsGrow g;
                            PsBuf joined;
                            ps_grow_init(&g);
                            if (prev->len > 0) {
                                ps_grow_add(&g, prev->data, prev->len);
                            }
                            ps_grow_add(&g, PLACEHOLDER, (int64_t)sizeof(PLACEHOLDER) - 1);
                            ps_grow_finish(&g, &joined);
                            *prev = joined;
                            done = 1;
                        }
                    }
                    if (!done) {
                        PsBuf ph;
                        ps_buf_copy(&ph, PLACEHOLDER_BARE, (int64_t)sizeof(PLACEHOLDER_BARE) - 1);
                        ps_seq_push(lines, &ph);
                    }
                }
                break;
            }
        }
    }
    return lines;
}

static PsSeq *wrap_text(int64_t n, const char *p, int64_t width, int64_t max_lines) {
    PsBuf flat;
    PsSeq *chunks;
    munge_ws(n, p, &flat);
    chunks = wrap_split(flat.data == NULL ? "" : flat.data, flat.len);
    return wrap_run(chunks, width, max_lines);
}

PsSeq *ps_wrap_wrap(int64_t n, const char *p, int64_t width) { return wrap_text(n, p, width, 0); }

void ps_wrap_fill(int64_t n, const char *p, int64_t width, PsBuf *out) {
    PsSeq *lines = wrap_text(n, p, width, 0);
    ps_str_join(1, "\n", lines, out);
}

void ps_wrap_shorten(int64_t n, const char *p, int64_t width, PsBuf *out) {
    /* ' '.join(text.strip().split()) then fill with max_lines=1. */
    PsGrow g;
    int64_t i = 0;
    PsSeq *lines;
    ps_grow_init(&g);
    while (i < n) {
        int64_t a;
        while (i < n && wrap_ws((unsigned char)p[i])) {
            i++;
        }
        a = i;
        while (i < n && !wrap_ws((unsigned char)p[i])) {
            i++;
        }
        if (i > a) {
            if (g.len > 0) {
                ps_grow_byte(&g, ' ');
            }
            ps_grow_add(&g, p + a, i - a);
        }
    }
    {
        PsBuf flat;
        ps_grow_finish(&g, &flat);
        lines = wrap_text(flat.len, flat.data == NULL ? "" : flat.data, width, 1);
    }
    ps_str_join(1, "\n", lines, out);
}

void ps_wrap_dedent(int64_t n, const char *p, PsBuf *out) {
    /* Blank out whitespace-only lines, find the common leading [ \t]
       margin over non-blank lines, then remove it. */
    PsGrow norm;
    int64_t i = 0;
    int64_t mlen = 0;
    char *margin = NULL;
    ps_grow_init(&norm);
    while (i <= n) {
        int64_t a = i;
        int64_t e;
        int only_ws;
        while (i < n && p[i] != '\n') {
            i++;
        }
        e = i;
        only_ws = 1;
        {
            int64_t j;
            for (j = a; j < e; j++) {
                if (p[j] != ' ' && p[j] != '\t') {
                    only_ws = 0;
                    break;
                }
            }
        }
        if (e > a && only_ws) {
            /* whitespace-only line: drop its content */
        } else {
            ps_grow_add(&norm, p + a, e - a);
        }
        if (i < n && p[i] == '\n') {
            ps_grow_byte(&norm, '\n');
            i++;
        } else {
            break;
        }
    }
    {
        const char *t = norm.data == NULL ? "" : norm.data;
        int64_t tn = norm.len;
        i = 0;
        while (i <= tn) {
            int64_t a = i;
            int64_t e;
            while (i < tn && t[i] != '\n') {
                i++;
            }
            e = i;
            /* leading [ \t]* of a line that holds a non-[ \t\n] char */
            if (e > a) {
                int64_t w = a;
                while (w < e && (t[w] == ' ' || t[w] == '\t')) {
                    w++;
                }
                if (w < e) {
                    int64_t il = w - a;
                    if (margin == NULL) {
                        margin = malloc((size_t)il + 1);
                        if (margin == NULL) {
                            abort();
                        }
                        memcpy(margin, t + a, (size_t)il);
                        mlen = il;
                    } else if (il >= mlen && memcmp(t + a, margin, (size_t)mlen) == 0) {
                        /* deeper indent: margin unchanged */
                    } else if (mlen > il && memcmp(margin, t + a, (size_t)il) == 0 && il > 0) {
                        mlen = il;
                    } else {
                        int64_t j = 0;
                        while (j < mlen && j < il && margin[j] == t[a + j]) {
                            j++;
                        }
                        mlen = j;
                    }
                }
            }
            if (i < tn && t[i] == '\n') {
                i++;
            } else {
                break;
            }
        }
    }
    {
        PsGrow res;
        const char *t = norm.data == NULL ? "" : norm.data;
        int64_t tn = norm.len;
        ps_grow_init(&res);
        i = 0;
        while (i <= tn) {
            int64_t a = i;
            int64_t e;
            int64_t skip = 0;
            while (i < tn && t[i] != '\n') {
                i++;
            }
            e = i;
            if (margin != NULL && mlen > 0 && e - a >= mlen) {
                int64_t j;
                int match = 1;
                for (j = 0; j < mlen; j++) {
                    if (t[a + j] != ' ' && t[a + j] != '\t') {
                        match = 0;
                        break;
                    }
                }
                if (match) {
                    skip = mlen;
                }
            }
            ps_grow_add(&res, t + a + skip, e - a - skip);
            if (i < tn && t[i] == '\n') {
                ps_grow_byte(&res, '\n');
                i++;
            } else {
                break;
            }
        }
        ps_grow_finish(&res, out);
    }
    free(margin);
}

static int line_break(unsigned char c) {
    return c == '\n' || c == '\r' || c == '\v' || c == '\f' || c == 0x1c || c == 0x1d || c == 0x1e;
}

void ps_wrap_indent(int64_t n, const char *p, int64_t pn, const char *pp, PsBuf *out) {
    /* prefix every line that is not all-whitespace (str.splitlines keepends). */
    PsGrow g;
    int64_t i = 0;
    ps_grow_init(&g);
    while (i < n) {
        int64_t a = i;
        int64_t e;
        int has_non_ws = 0;
        while (i < n) {
            unsigned char c = (unsigned char)p[i];
            if (c == '\r') {
                i++;
                if (i < n && p[i] == '\n') {
                    i++;
                }
                break;
            }
            if (line_break(c)) {
                i++;
                break;
            }
            i++;
        }
        e = i;
        {
            int64_t j;
            for (j = a; j < e; j++) {
                unsigned char c = (unsigned char)p[j];
                if (c != ' ' && c != '\t' && c != '\n' && c != '\r' && c != '\v' && c != '\f' &&
                    !(c >= 0x1c && c <= 0x1f) && c != 0x85) {
                    has_non_ws = 1;
                    break;
                }
            }
        }
        if (has_non_ws) {
            ps_grow_add(&g, pp, pn);
        }
        ps_grow_add(&g, p + a, e - a);
    }
    ps_grow_finish(&g, out);
}
