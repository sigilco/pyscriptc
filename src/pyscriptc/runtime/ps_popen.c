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
#include <fcntl.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

/* ps_sub.c's decoder (UTF-8 strict, the newline translation CPython's
   text mode applies): the text-mode pipe reads run their bytes through
   it. */
void ps_sub_decode(int64_t n, const char *p, int enc, int err, PsBuf *out);

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

#ifndef _WIN32
/* The spawn flag bits beyond the pipe mask (issue #1344): bit 3 puts the
   child in its own session (setsid in the child), bit 4 says the env
   map replaces the child's environment. The pipe mask keeps bits 0-2. */
#define PS_POPEN_NEW_SESSION 8
#define PS_POPEN_HAS_ENV 16

/* The env map as a NULL-terminated `KEY=VALUE` array, the execvpe
   spelling: the map replaces the child's environment whole. */
static char **ps_popen_envp(PsMap *env) {
    int64_t n = env == NULL ? 0 : env->len;
    char **envp = calloc((size_t)n + 1, sizeof(char *));
    int64_t i;
    if (envp == NULL) {
        abort();
    }
    for (i = 0; i < n; i++) {
        PsBuf key;
        PsBuf val;
        char *entry;
        ps_map_key_at(env, i, &key);
        ps_map_val_at(env, i, &val);
        entry = malloc((size_t)(key.len + val.len + 2));
        if (entry == NULL) {
            abort();
        }
        if (key.len > 0) {
            memcpy(entry, key.data, (size_t)key.len);
        }
        entry[key.len] = '=';
        if (val.len > 0) {
            memcpy(entry + key.len + 1, val.data, (size_t)val.len);
        }
        entry[key.len + 1 + val.len] = '\0';
        envp[i] = entry;
    }
    envp[n] = NULL;
    return envp;
}

static void ps_popen_free_vec(char **vec) {
    int64_t i;
    if (vec == NULL) {
        return;
    }
    for (i = 0; vec[i] != NULL; i++) {
        free(vec[i]);
    }
    free(vec);
}

/* The program lookup under a replaced environment: CPython walks the
   PATH of the env it hands the child (`os.get_exec_path(env)`, the
   default path `/bin:/usr/bin` when the env has no PATH), so the exec
   does the same walk and passes the env whole. A name with a slash runs
   as written. The errno left is CPython's: the first one that is not
   ENOENT or ENOTDIR wins, ENOENT otherwise. */
static void ps_popen_exec(char **argv, char **envp) {
    const char *path = "/bin:/usr/bin";
    const char *cur;
    int saved = 0;
    int64_t i;
    if (envp == NULL) {
        execvp(argv[0], argv);
        return;
    }
    if (strchr(argv[0], '/') != NULL) {
        execve(argv[0], argv, envp);
        return;
    }
    for (i = 0; envp[i] != NULL; i++) {
        if (strncmp(envp[i], "PATH=", 5) == 0) {
            path = envp[i] + 5;
        }
    }
    cur = path;
    for (;;) {
        const char *end = strchr(cur, ':');
        size_t dlen = end == NULL ? strlen(cur) : (size_t)(end - cur);
        size_t alen = strlen(argv[0]);
        char cand[4096];
        if (dlen + alen + 2 > sizeof cand) {
            /* Too long for a path: skip it, ENAMETOOLONG as the kernel says. */
            if (saved == 0) {
                saved = ENAMETOOLONG;
            }
            if (end == NULL) {
                break;
            }
            cur = end + 1;
            continue;
        }
        if (dlen == 0) {
            memcpy(cand, argv[0], alen + 1);
        } else {
            memcpy(cand, cur, dlen);
            cand[dlen] = '/';
            memcpy(cand + dlen + 1, argv[0], alen + 1);
        }
        execve(cand, argv, envp);
        if (errno != ENOENT && errno != ENOTDIR && saved == 0) {
            saved = errno;
        }
        if (end == NULL) {
            break;
        }
        cur = end + 1;
    }
    errno = saved != 0 ? saved : ENOENT;
}
#endif

/* Popen(argv, flags, cwd, env): the child starts now. A spawn failure
   raises, the way CPython's does: a program or a working directory that
   does not exist is FileNotFoundError (`[Errno 2] No such file or
   directory: '<name>'`), a directory that is not one NotADirectoryError,
   a refused exec PermissionError, anything else OSError. The child
   reports the errno through a close-on-exec pipe the parent reads before
   it answers, so a failed spawn is an exception and not an exit code.
   flags carries the pipe mask in bits 0-2 (bit 0 stdout, bit 1 stderr,
   bit 2 stdin, issue #1345), bit 3 start_new_session and bit 4 the env
   map (issue #1344). An empty cwd is the inherited one; the other
   streams inherit the parent's, the way CPython's Popen does when the
   caller passes None. */
void *ps_popen_new(PsSeq *args, int64_t flags, int64_t cwd_len, const char *cwd, PsMap *env) {
    PsPopen *proc = calloc(1, sizeof(PsPopen));
    if (proc == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    proc->in_fd = -1;
    proc->out_fd = -1;
    proc->err_fd = -1;
#ifdef _WIN32
    (void)flags;
    (void)cwd_len;
    (void)cwd;
    (void)env;
    ps_raise_cstr(PS_RUNTIME, "subprocess.Popen is not available on this platform");
#else
    {
        int64_t pipe_mask = flags & 7;
        char **argv = ps_popen_argv(args);
        char **envp = (flags & PS_POPEN_HAS_ENV) != 0 ? ps_popen_envp(env) : NULL;
        char *cwd_cstr = NULL;
        int in_pipe[2] = {-1, -1};
        int out_pipe[2] = {-1, -1};
        int err_pipe[2] = {-1, -1};
        int status_pipe[2] = {-1, -1};
        if (cwd_len > 0 && cwd != NULL) {
            cwd_cstr = malloc((size_t)cwd_len + 1);
            if (cwd_cstr == NULL) {
                abort();
            }
            memcpy(cwd_cstr, cwd, (size_t)cwd_len);
            cwd_cstr[cwd_len] = '\0';
        }
        if ((pipe_mask & 4) != 0 && pipe(in_pipe) != 0) {
            ps_popen_free_vec(argv);
            ps_popen_free_vec(envp);
            free(cwd_cstr);
            ps_raise_cstr(PS_RUNTIME, "subprocess.Popen pipe failed");
        }
        if ((pipe_mask & 1) != 0 && pipe(out_pipe) != 0) {
            ps_popen_free_vec(argv);
            ps_popen_free_vec(envp);
            free(cwd_cstr);
            ps_raise_cstr(PS_RUNTIME, "subprocess.Popen pipe failed");
        }
        if ((pipe_mask & 2) != 0 && pipe(err_pipe) != 0) {
            ps_popen_free_vec(argv);
            ps_popen_free_vec(envp);
            free(cwd_cstr);
            ps_raise_cstr(PS_RUNTIME, "subprocess.Popen pipe failed");
        }
        if (pipe(status_pipe) != 0) {
            ps_popen_free_vec(argv);
            ps_popen_free_vec(envp);
            free(cwd_cstr);
            ps_raise_cstr(PS_RUNTIME, "subprocess.Popen pipe failed");
        }
        (void)fcntl(status_pipe[0], F_SETFD, FD_CLOEXEC);
        (void)fcntl(status_pipe[1], F_SETFD, FD_CLOEXEC);
        pid_t pid = fork();
        if (pid < 0) {
            ps_popen_free_vec(argv);
            ps_popen_free_vec(envp);
            free(cwd_cstr);
            ps_raise_cstr(PS_RUNTIME, "subprocess.Popen fork failed");
        }
        if (pid == 0) {
            int32_t fail_kind = 0;
            int32_t fail_errno;
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
            if ((flags & PS_POPEN_NEW_SESSION) != 0) {
                (void)setsid();
            }
            if (cwd_cstr != NULL && chdir(cwd_cstr) != 0) {
                fail_kind = 1;
            } else {
                ps_popen_exec(argv, envp);
                fail_kind = 2;
            }
            /* The exec (or the chdir) failed: the errno and the failing
               step go to the parent, and the child leaves. */
            fail_errno = (int32_t)errno;
            {
                int32_t report[2];
                ssize_t put;
                report[0] = fail_kind;
                report[1] = fail_errno;
                put = write(status_pipe[1], report, sizeof report);
                (void)put;
            }
            _exit(127);
        }
        close(status_pipe[1]);
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
        /* The failure report: two int32s when the child could not start
           the program, EOF (the close-on-exec fired) when it did. */
        {
            int32_t report[2] = {0, 0};
            size_t have = 0;
            while (have < sizeof report) {
                ssize_t got = read(status_pipe[0], (char *)report + have, sizeof report - have);
                if (got < 0 && errno == EINTR) {
                    continue;
                }
                if (got <= 0) {
                    break;
                }
                have += (size_t)got;
            }
            close(status_pipe[0]);
            if (have == sizeof report) {
                int status = 0;
                char msg[4400];
                int32_t code = PS_OSERROR;
                const char *name = report[0] == 1 ? cwd_cstr : argv[0];
                pid_t got;
                do {
                    got = waitpid(pid, &status, 0);
                } while (got < 0 && errno == EINTR);
                if (proc->in_fd >= 0) {
                    close(proc->in_fd);
                }
                if (proc->out_fd >= 0) {
                    close(proc->out_fd);
                }
                if (proc->err_fd >= 0) {
                    close(proc->err_fd);
                }
                if (report[1] == ENOENT) {
                    code = PS_FILENOTFOUNDERROR;
                } else if (report[1] == ENOTDIR) {
                    code = PS_NOTADIRECTORYERROR;
                } else if (report[1] == EACCES || report[1] == EPERM) {
                    code = PS_PERMISSIONERROR;
                }
                snprintf(msg, sizeof msg, "[Errno %d] %s: '%.4000s'", (int)report[1], strerror((int)report[1]), name);
                free(proc);
                ps_popen_free_vec(argv);
                ps_popen_free_vec(envp);
                free(cwd_cstr);
                ps_raise_cstr(code, msg);
            }
        }
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
        ps_popen_free_vec(argv);
        ps_popen_free_vec(envp);
        free(cwd_cstr);
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

/* The text-mode pipe (issue #1344): `Popen(..., text=True)` and
   `Popen[str]`. The reads pull the bytes the binary helpers do and
   decode them as UTF-8, translating `\r\n` and `\r` to `\n` the way
   CPython's universal newlines do; the write encodes nothing (a str is
   its UTF-8 bytes) and answers the character count. */
void ps_pipe_text_read(void *box, PsBuf *out) {
    PsBuf raw;
    ps_pipe_read(box, -1, &raw);
    ps_sub_decode(raw.len, raw.data, 0, 0, out);
    free(raw.data);
}

void ps_pipe_text_readline(void *box, PsBuf *out) {
    PsBuf raw;
    ps_pipe_out_readline(box, &raw);
    ps_sub_decode(raw.len, raw.data, 0, 0, out);
    free(raw.data);
}

int64_t ps_pipe_text_write(void *box, int64_t n, const char *s) {
    int64_t chars = 0;
    int64_t i;
    (void)ps_pipe_write(box, n, s);
    for (i = 0; i < n; i++) {
        if (((unsigned char)s[i] & 0xC0) != 0x80) {
            chars++;
        }
    }
    return chars;
}
