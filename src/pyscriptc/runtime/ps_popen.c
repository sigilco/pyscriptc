/* SPDX-License-Identifier: Apache-2.0 */
/* subprocess.Popen: the synchronous child handle (issue #1345).

The Popen spawn forks, execs and returns at once; the handle keeps the
child's pid and the pipe ends the caller asked for. `poll()` reaps with
WNOHANG, `wait(timeout=)` polls the child in small slices against the
deadline and raises TimeoutExpired when it passes (the child stays
alive, CPython's contract), and kill/terminate/send_signal signal the
child through the pid. The incremental pipe reads pull from the open
descriptors one line or one n at a time, the shape an interactive
protocol over the child's stdio needs, where communicate() would drain
to EOF and block until the child exits.

The record is the caller's: no loop pumps it, no task parks on it. The
wait is the caller's thread blocking, which is what a synchronous
subprocess call means. The spawn machinery follows ps_sub.c's (the
argv list, the pipe dup2s, the execvp and the _exit(127) spawn
failure); the two runtimes keep their own records because the sync
handle has to answer poll() between the caller's own statements. The
pipe handles (`proc.stdout` and friends) are small wrappers over the
child record plus the stream selector: the buffers live on the child,
so two spellings of `proc.stdout` read the same stream.
*/

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#ifndef _WIN32
#include <errno.h>
#ifndef __wasi__
/* WASI's signal.h errors out unless the emulation is requested; the
   signal helpers are POSIX-only and WASI refuses them at the emit, so
   the wasi sysroot never sees the include. */
#include <signal.h>
#endif
#include <sys/wait.h>
#include <unistd.h>
#endif

/* The stream selector a pipe handle carries: 0 stdout, 1 stderr,
   2 stdin. */
#define PS_PIPE_OUT 0
#define PS_PIPE_ERR 1
#define PS_PIPE_IN 2

/* The record one Popen call owns. live is 1 while the child runs and 0
   once it is reaped; code holds the exit status then (the
   waitstatus_to_exitcode mapping the sync subprocess applies). The fds
   hold the pipe ends: in_fd is the write end of the child's stdin and
   out_fd/err_fd the read ends of its stdout/stderr, -1 when that
   stream is inherited or closed. The pending counts are the bytes one
   short read left in the buffer, so an incremental reader never loses
   them between calls. */
typedef struct PsPopen {
    int64_t pid;
    int32_t live;
    int32_t reaped;
    int64_t code;
    /* The argv repr at spawn time (the "Command '...'" the timeout
       message carries), NULL when the spawn never armed a timeout
       path. */
    char *cmd_repr;
    int32_t in_fd;
    int32_t out_fd;
    int32_t err_fd;
    char out_buf[4096];
    char err_buf[4096];
    int64_t out_len;
    int64_t out_pos;
    int64_t err_len;
    int64_t err_pos;
} PsPopen;

/* The pipe handle: the child record plus the stream it reads or
   writes. Buffers stay on the child, so every spelling of proc.stdout
   is the same stream. */
typedef struct PsPipe {
    PsPopen *proc;
    int32_t stream;
} PsPipe;

static PsPopen *ps_popen_check(void *box) {
    PsPopen *proc = (PsPopen *)box;
    if (proc == NULL) {
        ps_raise_cstr(PS_RUNTIME, "process is not initialized");
    }
    return proc;
}

/* The exit code of a wait status, the mapping CPython's Popen applies:
   the exit code of a child that exited, and the negated signal number of
   a child a signal killed. */
static int64_t ps_popen_code(int status) {
#ifdef _WIN32
    return (int64_t)status;
#else
    if (WIFEXITED(status)) {
        return (int64_t)WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return -(int64_t)WTERMSIG(status);
    }
    return -1;
#endif
}

#ifndef _WIN32
/* The argv list as a C array: the execvp spelling. */
static char **ps_popen_argv(PsSeq *args) {
    char **argv = calloc((size_t)args->len + 1, sizeof(char *));
    int64_t i;
    if (argv == NULL) {
        abort();
    }
    for (i = 0; i < args->len; i++) {
        PsBuf item;
        memcpy(&item, (char *)args->data + (size_t)i * (size_t)args->elem, sizeof item);
        argv[i] = malloc((size_t)item.len + 1);
        if (argv[i] == NULL) {
            abort();
        }
        if (item.len > 0) {
            memcpy(argv[i], item.data, (size_t)item.len);
        }
        argv[i][item.len] = '\0';
    }
    argv[args->len] = NULL;
    return argv;
}
#endif

/* Popen(argv): the child starts now. A spawn failure raises RuntimeError,
   the stand-in the sync subprocess raises for a failed spawn. pipe_mask
   names the streams to pipe: bit 0 stdout, bit 1 stderr, bit 2 stdin
   (issue #1345). The other streams inherit the parent's, the way
   CPython's Popen does when the caller passes None. */
void *ps_popen_new(PsSeq *args, int64_t pipe_mask) {
    PsPopen *proc = calloc(1, sizeof(PsPopen));
    if (proc == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    proc->in_fd = -1;
    proc->out_fd = -1;
    proc->err_fd = -1;
#ifdef _WIN32
    (void)pipe_mask;
    ps_raise_cstr(PS_RUNTIME, "subprocess.Popen is not available on this platform");
#else
    {
        char **argv = ps_popen_argv(args);
        int in_pipe[2] = {-1, -1};
        int out_pipe[2] = {-1, -1};
        int err_pipe[2] = {-1, -1};
        if ((pipe_mask & 4) != 0 && pipe(in_pipe) != 0) {
            free(argv);
            ps_raise_cstr(PS_RUNTIME, "subprocess.Popen pipe failed");
        }
        if ((pipe_mask & 1) != 0 && pipe(out_pipe) != 0) {
            free(argv);
            ps_raise_cstr(PS_RUNTIME, "subprocess.Popen pipe failed");
        }
        if ((pipe_mask & 2) != 0 && pipe(err_pipe) != 0) {
            free(argv);
            ps_raise_cstr(PS_RUNTIME, "subprocess.Popen pipe failed");
        }
        pid_t pid = fork();
        if (pid < 0) {
            free(argv);
            ps_raise_cstr(PS_RUNTIME, "subprocess.Popen fork failed");
        }
        if (pid == 0) {
            if (in_pipe[0] >= 0) {
                close(in_pipe[1]);
                dup2(in_pipe[0], STDIN_FILENO);
                close(in_pipe[0]);
            }
            if (out_pipe[1] >= 0) {
                close(out_pipe[0]);
                dup2(out_pipe[1], STDOUT_FILENO);
                close(out_pipe[1]);
            }
            if (err_pipe[1] >= 0) {
                close(err_pipe[0]);
                dup2(err_pipe[1], STDERR_FILENO);
                close(err_pipe[1]);
            }
            execvp(argv[0], argv);
            _exit(127);
        }
        if (in_pipe[1] >= 0) {
            close(in_pipe[0]);
            proc->in_fd = in_pipe[1];
        }
        if (out_pipe[0] >= 0) {
            close(out_pipe[1]);
            proc->out_fd = out_pipe[0];
        }
        if (err_pipe[0] >= 0) {
            close(err_pipe[1]);
            proc->err_fd = err_pipe[0];
        }
        proc->pid = (int64_t)pid;
        proc->live = 1;
        /* The argv repr the timeout message carries (CPython's "Command
           '<cmd repr>' timed out after N seconds"). */
        {
            PsBuf repr;
            ps_seq_repr(args, 1, "s", 0, &repr);
            proc->cmd_repr = malloc((size_t)repr.len + 1);
            if (proc->cmd_repr != NULL) {
                memcpy(proc->cmd_repr, repr.data, (size_t)repr.len);
                proc->cmd_repr[repr.len] = '\0';
            }
            free(repr.data);
        }
        for (int64_t i = 0; argv[i] != NULL; i++) {
            free(argv[i]);
        }
        free(argv);
    }
#endif
    return proc;
}

/* The child's pid. CPython's pid attribute answers the pid the spawn
   recorded; the number stays readable after the exit (kill on a reaped
   pid may land on a recycled child, CPython's own caveat). */
int64_t ps_popen_pid(void *box) {
    PsPopen *proc = ps_popen_check(box);
    return proc->pid;
}

/* Reap the child if it exited. Returns 1 when the record holds the exit
   code and 0 while the child still runs. The pipes stay open: an
   incremental reader may still be pulling from them after the exit. */
static int64_t ps_popen_reap(PsPopen *proc) {
    if (proc->reaped) {
        return 1;
    }
#ifndef _WIN32
    {
        int status = 0;
        pid_t got;
        do {
            got = waitpid((pid_t)proc->pid, &status, WNOHANG);
        } while (got < 0 && errno == EINTR);
        if (got == (pid_t)proc->pid) {
            proc->code = ps_popen_code(status);
            proc->reaped = 1;
            proc->live = 0;
            return 1;
        }
        if (got < 0) {
            /* The child is gone and the status was taken elsewhere: the
               record cannot report a code, so it reports the shell's
               convention for an unknown status. */
            proc->code = -1;
            proc->reaped = 1;
            proc->live = 0;
            return 1;
        }
    }
#endif
    return 0;
}

/* poll(): the exit code of a reaped child, or -1 while it still runs.
   The lowered poll reads the reaped flag first and answers the tagged
   None there, the way CPython's poll() does; the -1 is the record's
   "not reaped yet" spelling, the one proc.returncode carries too. */
int64_t ps_popen_poll(void *box) {
    PsPopen *proc = ps_popen_check(box);
    if (!ps_popen_reap(proc)) {
        return -1;
    }
    return proc->code;
}

/* Whether the child has been reaped: the flag the tagged poll() reads
   to tell CPython's None (the child still runs) from the exit code. */
int32_t ps_popen_reaped(void *box) {
    PsPopen *proc = ps_popen_check(box);
    if (!ps_popen_reap(proc)) {
        return 0;
    }
    return 1;
}

/* The exit code of a reaped child, -1 while it still runs: the same
   spelling poll() reports, the attribute read CPython's returncode
   answers as None until the exit. */
int64_t ps_popen_code_of(void *box) {
    return ps_popen_poll(box);
}

/* send_signal(sig): the signal to the pid, CPython's implementation
   without the Popen surface around it. A signal to a pid that no longer
   exists lands on errno ESRCH; CPython swallows ESRCH (its
   `if retcode is not None: return` shortcut answers a reaped child
   before the kill and a recycled pid races past), so the runtime does
   the same: only an unexpected errno raises. */
void ps_popen_signal(void *box, int64_t sig) {
#ifndef _WIN32
    PsPopen *proc = ps_popen_check(box);
    if (proc->reaped) {
        return;
    }
    if (kill((pid_t)proc->pid, (int)sig) != 0 && errno != ESRCH) {
        ps_raise_cstr(PS_PROCESSLOOKUPERROR, "process not found");
    }
#else
    (void)box;
    (void)sig;
    ps_raise_cstr(PS_RUNTIME, "Popen.send_signal is not available on this platform");
#endif
}

/* terminate(): SIGTERM, CPython's default. */
void ps_popen_terminate(void *box) {
#if !defined(_WIN32) && !defined(__wasi__)
    ps_popen_signal(box, SIGTERM);
#else
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "Popen.terminate is not available on this platform");
#endif
}

/* kill(): SIGKILL, CPython's hard stop. */
void ps_popen_kill(void *box) {
#if !defined(_WIN32) && !defined(__wasi__)
    ps_popen_signal(box, SIGKILL);
#else
    (void)box;
    ps_raise_cstr(PS_RUNTIME, "Popen.kill is not available on this platform");
#endif
}

/* wait(timeout_ms): the exit code of the child. The value is the whole
   millisecond bound (0 is no deadline, the omitted-timeout default).
   The bounded wait polls the child in small slices against the
   deadline the way ps_sub.c's run timeout does; a child that outlives
   the bound stays alive (CPython's wait raises TimeoutExpired and
   leaves the child running), and the call raises the TimeoutExpired
   class the module registered with CPython's message shape. */
int64_t ps_popen_wait(void *box, int64_t timeout_ms) {
    PsPopen *proc = ps_popen_check(box);
#ifndef _WIN32
    if (!ps_popen_reap(proc)) {
        struct timespec deadline;
        clock_gettime(CLOCK_MONOTONIC, &deadline);
        deadline.tv_sec += timeout_ms / 1000;
        deadline.tv_nsec += (timeout_ms % 1000) * 1000000L;
        if (deadline.tv_nsec >= 1000000000L) {
            deadline.tv_sec += 1;
            deadline.tv_nsec -= 1000000000L;
        }
        for (;;) {
            if (ps_popen_reap(proc)) {
                break;
            }
            struct timespec now;
            clock_gettime(CLOCK_MONOTONIC, &now);
            if (timeout_ms > 0
                && (now.tv_sec > deadline.tv_sec
                    || (now.tv_sec == deadline.tv_sec && now.tv_nsec >= deadline.tv_nsec))) {
                /* CPython's Popen.wait(timeout) raises TimeoutExpired and
                   leaves the child running; the caller decides between a
                   kill() and a keep-waiting. The message spells the
                   command and the bound the way CPython's does. */
                int32_t code = ps_exc_code_by_name("TimeoutExpired");
                if (code == 0) {
                    code = PS_EXCEPTION;
                }
                char msg[600];
                snprintf(msg, sizeof msg, "Command '%s' timed out after %.3g seconds",
                         proc->cmd_repr ? proc->cmd_repr : "?", (double)timeout_ms / 1000.0);
                ps_raise_cstr(code, msg);
            }
            struct timespec slice = {0, 2 * 1000000L};
            nanosleep(&slice, NULL);
        }
    }
    return proc->code;
#else
    (void)timeout_ms;
    while (!ps_popen_reap(proc)) {
    }
    return proc->code;
#endif
}

/* The pipe struct behind a handle: the child record, the selector and
   the fd the stream holds (the child's own field is the truth, the
   copy here just reads faster). */
static int32_t *ps_pipe_fd(PsPipe *pipe) {
    if (pipe->stream == PS_PIPE_IN) {
        return &pipe->proc->in_fd;
    }
    if (pipe->stream == PS_PIPE_ERR) {
        return &pipe->proc->err_fd;
    }
    return &pipe->proc->out_fd;
}

static char *ps_pipe_buf(PsPipe *pipe) {
    return pipe->stream == PS_PIPE_ERR ? pipe->proc->err_buf : pipe->proc->out_buf;
}

static int64_t *ps_pipe_len(PsPipe *pipe) {
    return pipe->stream == PS_PIPE_ERR ? &pipe->proc->err_len : &pipe->proc->out_len;
}

static int64_t *ps_pipe_pos(PsPipe *pipe) {
    return pipe->stream == PS_PIPE_ERR ? &pipe->proc->err_pos : &pipe->proc->out_pos;
}

/* proc.stdout / proc.stderr / proc.stdin: the pipe handle over the
   child record. Buffers live on the child, so every spelling of the
   attribute reads the same stream. A stream that was not piped still
   hands back the handle; the read/write on it raises ValueError (the
   closed-file family), CPython's shape for a None stream read. */
void *ps_popen_pipe(void *box, int64_t stream) {
    PsPopen *proc = ps_popen_check(box);
    PsPipe *pipe = calloc(1, sizeof(PsPipe));
    if (pipe == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    pipe->proc = proc;
    pipe->stream = (int32_t)stream;
    return pipe;
}

/* read(fd, buf, pending): one chunk off the descriptor into the pending
   buffer, or -1 at EOF (the fd closes there). */
static int64_t ps_pipe_fill(int32_t *fd, char *buf) {
#ifndef _WIN32
    ssize_t got;
    do {
        got = read(*fd, buf, 4096);
    } while (got < 0 && errno == EINTR);
    if (got <= 0) {
        close(*fd);
        *fd = -1;
        return -1;
    }
    return (int64_t)got;
#else
    (void)fd;
    (void)buf;
    return -1;
#endif
}

/* proc.stdout.read(n) / proc.stderr.read(n): the pipe's bytes. The
   read blocks until the child writes (or closes the pipe); n <= 0
   reads to EOF; a positive n reads exactly up to n bytes, parking the
   surplus in the buffer for the next call. A read on a stream that was
   not piped raises ValueError, the closed-file family (CPython answers
   None for the stream and the read raises on the None). */
void ps_pipe_read(void *box, int64_t n, PsBuf *out) {
#ifndef _WIN32
    PsPipe *pipe = (PsPipe *)box;
    if (pipe == NULL || pipe->proc == NULL || pipe->stream == PS_PIPE_IN) {
        ps_raise_cstr(PS_VALUE, "stdin is a write pipe: write() it, do not read() it");
    }
    if (*ps_pipe_fd(pipe) < 0) {
        ps_raise_cstr(PS_VALUE, "the stream was not piped");
    }
    int32_t *fd = ps_pipe_fd(pipe);
    char *buf = ps_pipe_buf(pipe);
    int64_t *len = ps_pipe_len(pipe);
    int64_t *pos = ps_pipe_pos(pipe);
    PsGrow g;
    ps_grow_init(&g);
    /* The bytes an earlier short read parked come first. */
    int64_t have = *len - *pos;
    if (have > 0) {
        int64_t take = (n > 0 && n < have) ? n : have;
        ps_grow_add(&g, buf + *pos, take);
        *pos += take;
        if (*pos == *len) {
            *pos = 0;
            *len = 0;
        }
    }
    while ((n <= 0 || g.len < n) && *fd >= 0) {
        int64_t got = ps_pipe_fill(fd, buf);
        if (got < 0) {
            break;
        }
        int64_t take = got;
        if (n > 0 && g.len + got > n) {
            take = n - g.len;
        }
        ps_grow_add(&g, buf, take);
        if (take < got) {
            /* The rest stays pending for the next call. */
            memmove(buf, buf + take, (size_t)(got - take));
            *len = got - take;
            *pos = 0;
        }
    }
    ps_grow_finish(&g, out);
#else
    (void)box;
    (void)n;
    ps_buf_copy(out, "", 0);
#endif
}

/* One line through the next '\n' (kept), the empty buffer at EOF:
   CPython's readline. Bytes an earlier read(n) parked in the buffer
   come first. */
static void ps_pipe_readline_impl(PsPipe *pipe, PsBuf *out) {
#ifndef _WIN32
    int32_t *fd = ps_pipe_fd(pipe);
    char *buf = ps_pipe_buf(pipe);
    int64_t *len = ps_pipe_len(pipe);
    int64_t *pos = ps_pipe_pos(pipe);
    PsGrow g;
    ps_grow_init(&g);
    for (;;) {
        if (*pos < *len) {
            char c = buf[(*pos)++];
            ps_grow_byte(&g, (unsigned char)c);
            if (c == '\n') {
                break;
            }
            if (*pos == *len) {
                *pos = 0;
                *len = 0;
            }
            continue;
        }
        if (*fd < 0) {
            break;
        }
        int64_t got = ps_pipe_fill(fd, buf);
        if (got < 0) {
            break;
        }
        *len = got;
        *pos = 0;
    }
    ps_grow_finish(&g, out);
#else
    (void)pipe;
    (void)out;
    ps_buf_copy(out, "", 0);
#endif
}

/* proc.stdout.readline() / proc.stderr.readline(): one line off the
   pipe the handle names. */
void ps_pipe_out_readline(void *box, PsBuf *out) {
    PsPipe *pipe = (PsPipe *)box;
    if (pipe == NULL || pipe->proc == NULL || pipe->stream == PS_PIPE_IN) {
        ps_raise_cstr(PS_RUNTIME, "the pipe handle is not initialized");
    }
    if (*ps_pipe_fd(pipe) < 0) {
        ps_raise_cstr(PS_VALUE, "the stream was not piped");
    }
    ps_pipe_readline_impl(pipe, out);
}

/* proc.stdin.write(data): the bytes into the stdin pipe. A write
   against a closed or unpiped stdin raises ValueError, the closed-file
   family CPython raises on a None stream. */
int64_t ps_pipe_write(void *box, int64_t n, const char *s) {
#ifndef _WIN32
    PsPipe *pipe = (PsPipe *)box;
    if (pipe == NULL || pipe->proc == NULL || pipe->stream != PS_PIPE_IN) {
        ps_raise_cstr(PS_VALUE, "stdout/stderr are read pipes: read() them, do not write() them");
    }
    int32_t *fd = ps_pipe_fd(pipe);
    if (*fd < 0) {
        ps_raise_cstr(PS_VALUE, "stdin was not piped or is closed");
    }
    int64_t done = 0;
    while (done < n) {
        ssize_t put;
        do {
            put = write(*fd, s + done, (size_t)(n - done));
        } while (put < 0 && errno == EINTR);
        if (put < 0) {
            if (errno == EPIPE) {
                close(*fd);
                *fd = -1;
                ps_raise_cstr(PS_BROKENPIPEERROR, "stdin pipe closed");
            }
            ps_raise_cstr(PS_OSERROR, "stdin write failed");
        }
        done += (int64_t)put;
    }
    return done;
#else
    (void)box;
    (void)n;
    (void)s;
    ps_raise_cstr(PS_VALUE, "stdin was not piped or is closed");
    return 0;
#endif
}

/* proc.stdin.flush(): write(2) has no buffer to flush, so this is the
   no-op CPython's unbuffered pipe writer is. */
void ps_pipe_flush(void *box) {
    PsPipe *pipe = (PsPipe *)box;
    if (pipe == NULL || pipe->proc == NULL || pipe->stream != PS_PIPE_IN) {
        ps_raise_cstr(PS_VALUE, "stdout/stderr are read pipes: they have nothing to flush");
    }
}

/* proc.stdin.close(): the write end goes away, so the child reading its
   stdin sees EOF. */
void ps_pipe_close(void *box) {
    PsPipe *pipe = (PsPipe *)box;
    if (pipe == NULL || pipe->proc == NULL) {
        ps_raise_cstr(PS_RUNTIME, "the pipe handle is not initialized");
    }
#ifndef _WIN32
    int32_t *fd = ps_pipe_fd(pipe);
    if (*fd >= 0) {
        close(*fd);
        *fd = -1;
    }
    int64_t *len = ps_pipe_len(pipe);
    int64_t *pos = ps_pipe_pos(pipe);
    *len = 0;
    *pos = 0;
#endif
}
