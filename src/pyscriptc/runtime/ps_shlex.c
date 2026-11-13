/* shlex: the shell-style lexer.

`split` breaks a command line into words the way CPython's POSIX-mode
`shlex.split` does. The lexer follows CPython's state machine: whitespace
separates words, single and double quotes group, a backslash escapes the next
character outside single quotes, and an unterminated quote or a trailing
backslash is a ValueError. The `comments` flag makes an unquoted `#` start a
comment that runs to the end of the line.

`quote` and `join` quote a word and a list of words for a shell command line
the way CPython does: a word that holds only characters the shell reads as
ordinary is left alone, and any other word is wrapped in single quotes with
the embedded single quotes spelled `'"'"'`.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

/* The characters CPython's `shlex.quote` leaves alone: the letters, the
   digits, the underscore, and the punctuation the shell reads as part of a
   word. CPython requires the whole word to be ASCII, so any byte above 127
   is outside the set and the word is quoted. */
static int ps_shlex_safe(char c) {
    if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9')) {
        return 1;
    }
    return strchr("%+,-./:=@_", c) != NULL;
}

static int ps_shlex_space(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

/* The lexer state, mirroring CPython's `shlex.read_token`. */
typedef struct {
    const char *s;
    int64_t len;
    int64_t at;
    int comments;
    char state;
    char escaped;
    PsGrow token;
} ShlexLex;

/* One word: 1 when a word was read, 0 at the end of the text. */
static int ps_shlex_word(ShlexLex *lex, PsBuf *out) {
    int quoted = 0;
    ps_grow_init(&lex->token);
    for (;;) {
        int have = lex->at < lex->len;
        char c = have ? lex->s[lex->at] : '\0';
        if (lex->state == ' ') {
            if (!have) {
                break;
            }
            lex->at++;
            if (ps_shlex_space(c)) {
                if (lex->token.len > 0 || quoted) {
                    break;
                }
                continue;
            }
            if (lex->comments && c == '#') {
                while (lex->at < lex->len && lex->s[lex->at] != '\n') {
                    lex->at++;
                }
                continue;
            }
            if (c == '\\') {
                lex->escaped = 'a';
                lex->state = '\\';
                continue;
            }
            if (c == '\'' || c == '"') {
                lex->state = c;
                continue;
            }
            ps_grow_byte(&lex->token, (unsigned char)c);
            lex->state = 'a';
            continue;
        }
        if (lex->state == '\'' || lex->state == '"') {
            if (!have) {
                ps_grow_finish(&lex->token, out);
                ps_raise_cstr(PS_VALUE, "No closing quotation");
            }
            lex->at++;
            quoted = 1;
            if (c == lex->state) {
                lex->state = 'a';
                continue;
            }
            /* Only the double quote escapes; inside the single quote a
               backslash is an ordinary character. */
            if (lex->state == '"' && c == '\\') {
                lex->escaped = '"';
                lex->state = '\\';
                continue;
            }
            ps_grow_byte(&lex->token, (unsigned char)c);
            continue;
        }
        if (lex->state == '\\') {
            if (!have) {
                ps_grow_finish(&lex->token, out);
                ps_raise_cstr(PS_VALUE, "No escaped character");
            }
            lex->at++;
            /* Inside a quote the backslash survives unless it escapes the
               quote itself or another backslash. */
            if ((lex->escaped == '\'' || lex->escaped == '"') && c != '\\' && c != lex->escaped) {
                ps_grow_byte(&lex->token, '\\');
            }
            ps_grow_byte(&lex->token, (unsigned char)c);
            lex->state = lex->escaped;
            continue;
        }
        /* The word state. */
        if (!have) {
            break;
        }
        lex->at++;
        if (ps_shlex_space(c)) {
            lex->state = ' ';
            if (lex->token.len > 0 || quoted) {
                break;
            }
            continue;
        }
        if (lex->comments && c == '#') {
            while (lex->at < lex->len && lex->s[lex->at] != '\n') {
                lex->at++;
            }
            lex->state = ' ';
            if (lex->token.len > 0 || quoted) {
                break;
            }
            continue;
        }
        if (c == '\'' || c == '"') {
            lex->state = c;
            continue;
        }
        if (c == '\\') {
            lex->escaped = 'a';
            lex->state = '\\';
            continue;
        }
        ps_grow_byte(&lex->token, (unsigned char)c);
    }
    if (lex->token.len == 0 && !quoted) {
        ps_grow_finish(&lex->token, out);
        return 0;
    }
    ps_grow_finish(&lex->token, out);
    return 1;
}

/* `split(text)`: the words, as a list of str.

   The list is a seq of PsBuf, the same shape the `list[str]` carrier uses
   everywhere else in the runtime. */
PsSeq *ps_shlex_split(int64_t n, const char *s, int64_t comments) {
    if (n < 0) {
        ps_raise_cstr(PS_VALUE, "shlex length is negative");
    }
    ShlexLex lex;
    lex.s = s;
    lex.len = n;
    lex.at = 0;
    lex.comments = comments != 0;
    lex.state = ' ';
    lex.escaped = ' ';
    PsSeq *words = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    for (;;) {
        PsBuf word;
        if (!ps_shlex_word(&lex, &word)) {
            break;
        }
        ps_seq_push(words, &word);
    }
    return words;
}

/* `quote(word)`: the shell spelling of one word. */
void ps_shlex_quote(int64_t n, const char *s, PsBuf *out) {
    if (n == 0) {
        ps_buf_copy(out, "''", 2);
        return;
    }
    int safe = 1;
    for (int64_t i = 0; i < n; i++) {
        if (!ps_shlex_safe(s[i])) {
            safe = 0;
            break;
        }
    }
    if (safe) {
        ps_buf_copy(out, s, n);
        return;
    }
    /* The quoted form: '...' with every embedded single quote spelled
       '"'"'. */
    PsGrow grow;
    ps_grow_init(&grow);
    ps_grow_byte(&grow, '\'');
    for (int64_t i = 0; i < n; i++) {
        if (s[i] == '\'') {
            ps_grow_add(&grow, "'\"'\"'", 5);
        } else {
            ps_grow_byte(&grow, (unsigned char)s[i]);
        }
    }
    ps_grow_byte(&grow, '\'');
    ps_grow_finish(&grow, out);
}

/* `join(words)`: the command line that spells the words. */
void ps_shlex_join(PsSeq *words, PsBuf *out) {
    PsGrow grow;
    ps_grow_init(&grow);
    int64_t count = ps_seq_len(words);
    for (int64_t i = 0; i < count; i++) {
        if (i > 0) {
            ps_grow_byte(&grow, ' ');
        }
        PsBuf word;
        ps_seq_get(words, i, &word);
        PsBuf quoted;
        ps_shlex_quote(word.len, word.data, &quoted);
        ps_grow_add(&grow, quoted.data, quoted.len);
        free(quoted.data);
    }
    ps_grow_finish(&grow, out);
}
