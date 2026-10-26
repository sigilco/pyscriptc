/* subprocess native subset: run, call, check_call over argv lists.

Each call forks and execs; `capture_output` feeds one pipe back as bytes.
The child's exit status maps to returncode like CPython's
waitstatus_to_exitcode. `run` returns the CompletedProcess record: the
return code, the captured stdout bytes, and the captured stderr bytes.
*/

#include "ps_rt.h"

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef _NO_W32_PSEUDO_MODIFIERS
#define _NO_W32_PSEUDO_MODIFIERS
#endif
#include <windows.h>
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef _WIN32
#include <sys/wait.h>
#include <unistd.h>
#endif

/* The CompletedProcess record: the layout the CompletedProcess class in
   stdmore.CLASS_FIELDS uses. The field order is returncode, stdout, stderr,
   and a PsBuf is two words, so this matches the emitted class layout. */
typedef struct {
    int64_t returncode;
    PsBuf stdout_buf;
    PsBuf stderr_buf;
} PsCompleted;

static void ps_sub_fail(const char *what) { ps_raise_cstr(PS_RUNTIME, what); }

static char *ps_cstr_dup(int64_t n, const char *s) {
    char *text;
    if (n < 0) {
        ps_raise_code(PS_VALUE);
    }
    text = malloc((size_t)n + 1);
    if (text == NULL) {
        abort();
    }
    if (n > 0) {
        memcpy(text, s, (size_t)n);
    }
    text[n] = '\0';
    return text;
}

#ifdef _WIN32
static char *ps_build_cmdline(PsSeq *args) {
    PsGrow g;
    int64_t i;
    ps_grow_init(&g);
    for (i = 0; i < args->len; i++) {
        PsBuf item;
        int64_t j;
        int need_quote = 0;
        memcpy(&item, (char *)args->data + (size_t)i * (size_t)args->elem, sizeof item);
        if (i > 0) {
            ps_grow_byte(&g, ' ');
        }
        if (item.len == 0) {
            need_quote = 1;
        } else {
            for (j = 0; j < item.len; j++) {
                if (item.data[j] == ' ' || item.data[j] == '\t' || item.data[j] == '"') {
                    need_quote = 1;
                    break;
                }
            }
        }
        if (!need_quote) {
            ps_grow_add(&g, item.data, item.len);
        } else {
            /* The CommandLineToArgvW rule: a backslash run before a quote
               is literal unless it precedes the quote itself, in which case
               one backslash escapes the quote. */
            int64_t slashes = 0;
            ps_grow_byte(&g, '"');
            for (j = 0; j < item.len; j++) {
                if (item.data[j] == '\\') {
                    slashes += 1;
                    continue;
                }
                if (item.data[j] == '"') {
                    for (int64_t k = 0; k < slashes; k++) {
                        ps_grow_byte(&g, '\\');
                    }
                    ps_grow_byte(&g, '\\');
                    slashes = 0;
                } else {
                    for (int64_t k = 0; k < slashes; k++) {
                        ps_grow_byte(&g, '\\');
                    }
                    slashes = 0;
                }
                ps_grow_byte(&g, item.data[j]);
            }
            for (int64_t k = 0; k < slashes; k++) {
                ps_grow_byte(&g, '\\');
            }
            ps_grow_byte(&g, '"');
        }
    }
    ps_grow_byte(&g, '\0');
    return g.data;
}

static void ps_sub_readhandle(HANDLE h, PsBuf *out) {
    PsGrow g;
    char chunk[4096];
    DWORD bytesRead;
    ps_grow_init(&g);
    while (ReadFile(h, chunk, sizeof chunk, &bytesRead, NULL) && bytesRead > 0) {
        ps_grow_add(&g, chunk, (int64_t)bytesRead);
    }
    ps_grow_finish(&g, out);
}

static int ps_sub_spawn(PsSeq *args, const char *cwd, PsBuf *out_stdout, PsBuf *out_stderr) {
    char *cmdline = ps_build_cmdline(args);
    SECURITY_ATTRIBUTES sa;
    HANDLE out_read = NULL, out_write = NULL;
    HANDLE err_read = NULL, err_write = NULL;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    DWORD exit_code = 0;
    BOOL ok;
    const char *cwd_arg = (cwd != NULL && cwd[0] != '\0') ? cwd : NULL;

    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    sa.lpSecurityDescriptor = NULL;

    if (out_stdout != NULL) {
        if (!CreatePipe(&out_read, &out_write, &sa, 0)) {
            free(cmdline);
            ps_sub_fail("subprocess pipe failed");
        }
        SetHandleInformation(out_read, HANDLE_FLAG_INHERIT, 0);
    }
    if (out_stderr != NULL) {
        if (!CreatePipe(&err_read, &err_write, &sa, 0)) {
            if (out_stdout != NULL) {
                CloseHandle(out_read);
                CloseHandle(out_write);
            }
            free(cmdline);
            ps_sub_fail("subprocess pipe failed");
        }
        SetHandleInformation(err_read, HANDLE_FLAG_INHERIT, 0);
    }

    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    si.dwFlags |= STARTF_USESTDHANDLES;
    si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    if (si.hStdInput == INVALID_HANDLE_VALUE) {
        si.hStdInput = NULL;
    }
    si.hStdOutput = (out_stdout != NULL) ? out_write : GetStdHandle(STD_OUTPUT_HANDLE);
    si.hStdError = (out_stderr != NULL) ? err_write : GetStdHandle(STD_ERROR_HANDLE);
    if (si.hStdOutput == INVALID_HANDLE_VALUE) {
        si.hStdOutput = NULL;
    }
    if (si.hStdError == INVALID_HANDLE_VALUE) {
        si.hStdError = NULL;
    }

    ZeroMemory(&pi, sizeof(pi));
    ok = CreateProcessA(NULL, cmdline, NULL, NULL, TRUE, 0, NULL, cwd_arg, &si, &pi);
    free(cmdline);

    if (out_stdout != NULL) {
        CloseHandle(out_write);
    }
    if (out_stderr != NULL) {
        CloseHandle(err_write);
    }

    if (!ok) {
        if (out_stdout != NULL) {
            CloseHandle(out_read);
        }
        if (out_stderr != NULL) {
            CloseHandle(err_read);
        }
        ps_sub_fail("subprocess CreateProcess failed");
    }

    if (out_stdout != NULL) {
        ps_sub_readhandle(out_read, out_stdout);
        CloseHandle(out_read);
    }
    if (out_stderr != NULL) {
        ps_sub_readhandle(err_read, out_stderr);
        CloseHandle(err_read);
    }

    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);

    return (int)exit_code;
}

static int64_t ps_sub_code(int status) {
    return (int64_t)status;
}
#else
/* Read a whole pipe into a grow buffer, then hand it out as a PsBuf. */
static void ps_sub_readall(int fd, PsBuf *out) {
    PsGrow g;
    char chunk[4096];
    ssize_t n;
    ps_grow_init(&g);
    while ((n = read(fd, chunk, sizeof chunk)) > 0) {
        ps_grow_add(&g, chunk, (int64_t)n);
    }
    ps_grow_finish(&g, out);
    if (n < 0) {
        ps_sub_fail("subprocess read failed");
    }
}

static char **ps_sub_argv(PsSeq *args) {
    char **argv = ps_alloc(((size_t)args->len + 1) * sizeof(char *));
    int64_t i;
    for (i = 0; i < args->len; i++) {
        PsBuf item;
        memcpy(&item, (char *)args->data + (size_t)i * (size_t)args->elem, sizeof item);
        argv[i] = ps_cstr_dup(item.len, item.data);
    }
    argv[args->len] = NULL;
    return argv;
}

/* Run argv. `out_stdout` and `out_stderr` are NULL when that stream is not
   captured. Returns the waitpid status. */
static int ps_sub_spawn(PsSeq *args, const char *cwd, PsBuf *out_stdout, PsBuf *out_stderr) {
    char **argv = ps_sub_argv(args);
    int out_pipe[2] = {-1, -1};
    int err_pipe[2] = {-1, -1};
    pid_t pid;
    int status = 0;
    if (out_stdout != NULL && pipe(out_pipe) != 0) {
        ps_sub_fail("subprocess pipe failed");
    }
    if (out_stderr != NULL && pipe(err_pipe) != 0) {
        ps_sub_fail("subprocess pipe failed");
    }
    pid = fork();
    if (pid < 0) {
        ps_sub_fail("subprocess fork failed");
    }
    if (pid == 0) {
        if (cwd != NULL && cwd[0] != '\0') {
            if (chdir(cwd) != 0) {
                _exit(127);
            }
        }
        if (out_stdout != NULL) {
            close(out_pipe[0]);
            dup2(out_pipe[1], STDOUT_FILENO);
            close(out_pipe[1]);
        }
        if (out_stderr != NULL) {
            close(err_pipe[0]);
            dup2(err_pipe[1], STDERR_FILENO);
            close(err_pipe[1]);
        }
        execvp(argv[0], argv);
        _exit(127);
    }
    if (out_stdout != NULL) {
        close(out_pipe[1]);
        ps_sub_readall(out_pipe[0], out_stdout);
        close(out_pipe[0]);
    }
    if (out_stderr != NULL) {
        close(err_pipe[1]);
        ps_sub_readall(err_pipe[0], out_stderr);
        close(err_pipe[0]);
    }
    if (waitpid(pid, &status, 0) < 0) {
        ps_sub_fail("subprocess wait failed");
    }
    for (int64_t i = 0; argv[i] != NULL; i++) {
        free(argv[i]);
    }
    free(argv);
    return status;
}

static int64_t ps_sub_code(int status) {
    if (WIFEXITED(status)) {
        return WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return 128 + WTERMSIG(status);
    }
    return -1;
}
#endif

/* call(cmd): the exit code of the child. */
int64_t ps_sub_call(PsSeq *args) { return ps_sub_code(ps_sub_spawn(args, NULL, NULL, NULL)); }

/* check_call(cmd): the exit code of the child; a non-zero code raises
   RuntimeError, the subset stand-in for CalledProcessError. */
int64_t ps_sub_check_call(PsSeq *args) {
    int64_t code = ps_sub_code(ps_sub_spawn(args, NULL, NULL, NULL));
    if (code != 0) {
        ps_raise_cstr(PS_RUNTIME, "subprocess returned a non-zero exit code");
    }
    return code;
}

/* Text-mode decode for the captured streams: CPython's
   TextIOWrapper.decode + _translate_newlines over one buffer. enc is 0
   utf-8, 1 ascii, 2 latin-1; err is 0 strict, 1 replace, 2 ignore,
   3 backslashreplace. The text result is the buffer's UTF-8 spelling, so
   every producer re-encodes into a grow buffer and translates newlines
   on the way. */

/* One code point emitted as UTF-8 into the grow buffer. */
static void ps_sub_emit_utf8(PsGrow *g, unsigned int cp) {
    if (cp < 0x80) {
        ps_grow_byte(g, (unsigned char)cp);
    } else if (cp < 0x800) {
        ps_grow_byte(g, (unsigned char)(0xC0 | (cp >> 6)));
        ps_grow_byte(g, (unsigned char)(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        ps_grow_byte(g, (unsigned char)(0xE0 | (cp >> 12)));
        ps_grow_byte(g, (unsigned char)(0x80 | ((cp >> 6) & 0x3F)));
        ps_grow_byte(g, (unsigned char)(0x80 | (cp & 0x3F)));
    } else {
        ps_grow_byte(g, (unsigned char)(0xF0 | (cp >> 18)));
        ps_grow_byte(g, (unsigned char)(0x80 | ((cp >> 12) & 0x3F)));
        ps_grow_byte(g, (unsigned char)(0x80 | ((cp >> 6) & 0x3F)));
        ps_grow_byte(g, (unsigned char)(0x80 | (cp & 0x3F)));
    }
}

/* CPython's replacement character, U+FFFD. */
static void ps_sub_emit_replacement(PsGrow *g) {
    ps_sub_emit_utf8(g, 0xFFFD);
}

/* CPython's backslash escape for one byte: \xHH lowercase hex. */
static void ps_sub_emit_backslash(PsGrow *g, unsigned char c) {
    static const char hex[] = "0123456789abcdef";
    ps_grow_byte(g, '\\');
    ps_grow_byte(g, 'x');
    ps_grow_byte(g, (unsigned char)hex[c >> 4]);
    ps_grow_byte(g, (unsigned char)hex[c & 0xF]);
}

static void ps_sub_newlines(PsGrow *g) {
    /* _translate_newlines: \r\n to \n, then a lone \r to \n. The walk
       rewrites in place over the grow buffer's tail. */
    int64_t r = 0;
    int64_t w = 0;
    while (r < g->len) {
        unsigned char c = (unsigned char)g->data[r];
        if (c == '\r') {
            g->data[w++] = '\n';
            r++;
            if (r < g->len && g->data[r] == '\n') {
                r++;
            }
            continue;
        }
        g->data[w++] = (char)c;
        r++;
    }
    g->len = w;
}

static void ps_sub_decode(int64_t n, const char *p, int enc, int err, PsBuf *out) {
    PsGrow g;
    int64_t i = 0;
    ps_grow_init(&g);
    if (enc == 2) {
        /* latin-1: every byte is its code point; no error path exists. */
        for (i = 0; i < n; i++) {
            ps_sub_emit_utf8(&g, (unsigned char)p[i]);
        }
    } else {
        while (i < n) {
            unsigned char c = (unsigned char)p[i];
            unsigned int cp;
            int w;
            int bad = 0;
            const char *why = "invalid start byte";
            if (c < 0x80) {
                ps_grow_byte(&g, c);
                i++;
                continue;
            }
            if (enc == 1) {
                /* ascii: a byte above 0x7f is not a code point here. */
                w = 0;
                cp = 0;
                bad = 1;
                why = "ordinal not in range(128)";
            } else if ((c & 0xE0) == 0xC0) {
                w = 2;
                cp = (unsigned int)(c & 0x1F);
            } else if ((c & 0xF0) == 0xE0) {
                w = 3;
                cp = (unsigned int)(c & 0x0F);
            } else if ((c & 0xF8) == 0xF0) {
                w = 4;
                cp = (unsigned int)(c & 0x07);
            } else {
                w = 0;
                cp = 0;
                bad = 1;
                why = "invalid start byte";
            }
            if (!bad && i + w > n) {
                bad = 1;
                why = "unexpected end of data";
            }
            for (int k = 1; !bad && k < w; k++) {
                unsigned char cc = (unsigned char)p[i + k];
                if ((cc & 0xC0) != 0x80) {
                    bad = 1;
                    why = "invalid continuation byte";
                } else {
                    cp = (cp << 6) | (unsigned int)(cc & 0x3F);
                }
            }
            if (!bad && w == 2 && cp < 0x80) {
                bad = 1;
                why = "invalid start byte";
            }
            if (!bad && w == 3 && cp < 0x800) {
                bad = 1;
                why = "invalid start byte";
            }
            if (!bad && w == 4 && cp < 0x10000) {
                bad = 1;
                why = "invalid start byte";
            }
            if (!bad && (cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF))) {
                bad = 1;
                why = "invalid continuation byte";
            }
            if (!bad) {
                ps_sub_emit_utf8(&g, cp);
                i += w;
                continue;
            }
            if (err == 1) {
                /* replace: one U+FFFD for the broken byte. */
                ps_sub_emit_replacement(&g);
                i++;
                continue;
            }
            if (err == 2) {
                /* ignore: the broken byte drops out. */
                i++;
                continue;
            }
            if (err == 3) {
                /* backslashreplace: the byte escapes as \xHH. */
                ps_sub_emit_backslash(&g, c);
                i++;
                continue;
            }
            {
                char msg[160];
                if (why[0] == 'u') {
                    /* "unexpected end of data" names the whole partial
                       sequence, like CPython's decoder. */
                    snprintf(
                        msg,
                        sizeof msg,
                        "'%s' codec can't decode bytes in position %lld-%lld: %s",
                        enc == 1 ? "ascii" : "utf-8",
                        (long long)i,
                        (long long)(n - 1),
                        why
                    );
                } else {
                    snprintf(
                        msg,
                        sizeof msg,
                        "'%s' codec can't decode byte 0x%02x in position %lld: %s",
                        enc == 1 ? "ascii" : "utf-8",
                        c,
                        (long long)i,
                        why
                    );
                }
                free(g.data);
                ps_raise_cstr(PS_UNICODEDECODE, msg);
            }
        }
    }
    ps_sub_newlines(&g);
    out->len = g.len;
    out->data = g.data;
}

/* run(cmd): the CompletedProcess record. `check` (flag bit 1) raises the
   check_call stand-in when the exit code is non-zero; capture is bit 0. */
void ps_sub_run(PsSeq *args, int64_t flags, int64_t cwd_len, const char *cwd, PsCompleted *out) {
    int status;
    char *cwd_cstr = NULL;
    if (cwd_len > 0 && cwd != NULL) {
        cwd_cstr = ps_cstr_dup(cwd_len, cwd);
    }
    out->stdout_buf.len = 0;
    out->stdout_buf.data = NULL;
    out->stderr_buf.len = 0;
    out->stderr_buf.data = NULL;
    if (flags & 1) {
        status = ps_sub_spawn(args, cwd_cstr, &out->stdout_buf, &out->stderr_buf);
    } else {
        status = ps_sub_spawn(args, cwd_cstr, NULL, NULL);
    }
    if (cwd_cstr != NULL) {
        free(cwd_cstr);
    }
    out->returncode = ps_sub_code(status);
    if ((flags & 2) && out->returncode != 0) {
        ps_raise_cstr(PS_RUNTIME, "subprocess returned a non-zero exit code");
    }
}
