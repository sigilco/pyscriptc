/* subprocess native subset: run, call, check_call over argv lists.

Each call forks and execs; `capture_output` feeds one pipe back as bytes.
The child's exit status maps to returncode like CPython's
waitstatus_to_exitcode. `run` returns the CompletedProcess record: the
return code, the captured stdout bytes, and the captured stderr bytes.
*/

#include "ps_rt.h"

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
            ps_grow_byte(&g, '"');
            for (j = 0; j < item.len; j++) {
                if (item.data[j] == '"') {
                    ps_grow_byte(&g, '\\');
                }
                ps_grow_byte(&g, item.data[j]);
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

static int ps_sub_spawn(PsSeq *args, PsBuf *out_stdout, PsBuf *out_stderr) {
    char *cmdline = ps_build_cmdline(args);
    SECURITY_ATTRIBUTES sa;
    HANDLE out_read = NULL, out_write = NULL;
    HANDLE err_read = NULL, err_write = NULL;
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    DWORD exit_code = 0;
    BOOL ok;

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
    si.hStdOutput = (out_stdout != NULL) ? out_write : GetStdHandle(STD_OUTPUT_HANDLE);
    si.hStdError = (out_stderr != NULL) ? err_write : GetStdHandle(STD_ERROR_HANDLE);

    ZeroMemory(&pi, sizeof(pi));
    ok = CreateProcessA(NULL, cmdline, NULL, NULL, TRUE, 0, NULL, NULL, &si, &pi);
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
static int ps_sub_spawn(PsSeq *args, PsBuf *out_stdout, PsBuf *out_stderr) {
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
int64_t ps_sub_call(PsSeq *args) { return ps_sub_code(ps_sub_spawn(args, NULL, NULL)); }

/* check_call(cmd): the exit code of the child; a non-zero code raises
   RuntimeError, the subset stand-in for CalledProcessError. */
int64_t ps_sub_check_call(PsSeq *args) {
    int64_t code = ps_sub_code(ps_sub_spawn(args, NULL, NULL));
    if (code != 0) {
        ps_raise_cstr(PS_RUNTIME, "subprocess returned a non-zero exit code");
    }
    return code;
}

/* run(cmd): the CompletedProcess record. */
void ps_sub_run(PsSeq *args, int64_t capture, PsCompleted *out) {
    int status;
    out->stdout_buf.len = 0;
    out->stdout_buf.data = NULL;
    out->stderr_buf.len = 0;
    out->stderr_buf.data = NULL;
    if (capture) {
        status = ps_sub_spawn(args, &out->stdout_buf, &out->stderr_buf);
    } else {
        status = ps_sub_spawn(args, NULL, NULL);
    }
    out->returncode = ps_sub_code(status);
}
