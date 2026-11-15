/* SPDX-License-Identifier: Apache-2.0 */
/* The async subprocess: asyncio.create_subprocess_exec over the sync spawn.

`create_subprocess_exec` forks and execs the argv list and hands back an
opaque process record. `await proc.wait()` parks the task until the child
exits; the loop pumps the live records between iterations, reaps the exited
children with waitpid(WNOHANG) and wakes the parked task, the shape the
to_thread completion uses. `proc.returncode` reads the reaped code.

The record lives on the loop's thread (ps_loop.c owns the tasks), so the
waiter list needs no mutex of its own. The fork and the exec live here
rather than in ps_sub.c, because the async record is the loop's: the spawn
machinery is shared, but the two runtimes keep their own records.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

#ifndef _WIN32
#include <errno.h>
#include <fcntl.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

/* The loop's task handle (ps_loop.c owns the layout). */
extern void *ps_loop_current_task(void);
extern void ps_loop_wake_task(void *task);

/* The record one create_subprocess_exec call owns. live is 1 while the
   child runs and 0 once it is reaped; code holds the exit status then
   (waitstatus_to_exitcode, the mapping the sync subprocess applies).
   waiter is the task parked on wait(), if any. */
typedef struct PsAProc {
    int64_t pid;
    int32_t live;
    int32_t reaped;
    int64_t code;
    void *waiter;
    struct PsAProc *next;
} PsAProc;

/* Every live process record, so the loop can reap without a user call. The
   list is only touched by the loop's thread, so it needs no lock. */
static PsAProc *ps_aprocs;

static PsAProc *ps_aproc_check(void *box) {
    PsAProc *proc = (PsAProc *)box;
    if (proc == NULL) {
        ps_raise_cstr(PS_RUNTIME, "process is not initialized");
    }
    return proc;
}

/* The exit code of a wait status, the mapping CPython's
   waitstatus_to_exitcode applies. */
static int64_t ps_aproc_code(int status) {
#ifdef _WIN32
    return (int64_t)status;
#else
    if (WIFEXITED(status)) {
        return (int64_t)WEXITSTATUS(status);
    }
    if (WIFSIGNALED(status)) {
        return (int64_t)(128 + WTERMSIG(status));
    }
    return -1;
#endif
}

#ifndef _WIN32
/* The argv list as a C array: the execvp spelling. */
static char **ps_aproc_argv(PsSeq *args) {
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

/* `asyncio.create_subprocess_exec(argv)`: the child starts now. The stdout
   and stderr pipes are the child's; the record keeps the read ends so a
   later read sees the output. A spawn failure raises RuntimeError, the
   stand-in the sync subprocess raises for a failed spawn. */
void *ps_aproc_new(PsSeq *args) {
    PsAProc *proc = calloc(1, sizeof(PsAProc));
    if (proc == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
#ifdef _WIN32
    ps_raise_cstr(PS_RUNTIME, "asyncio.create_subprocess_exec is not available on this platform");
#else
    {
        char **argv = ps_aproc_argv(args);
        pid_t pid = fork();
        if (pid < 0) {
            free(argv);
            ps_raise_cstr(PS_RUNTIME, "asyncio.create_subprocess_exec fork failed");
        }
        if (pid == 0) {
            /* The child inherits the parent's stdout and stderr, the way
               CPython's create_subprocess_exec does when the caller did not
               ask for pipes. */
            execvp(argv[0], argv);
            _exit(127);
        }
        proc->pid = (int64_t)pid;
        proc->live = 1;
    }
#endif
    proc->next = ps_aprocs;
    ps_aprocs = proc;
    return proc;
}

/* Reap the child if it exited. Returns 1 when the record holds the exit
   code and 0 while the child still runs. */
static int64_t ps_aproc_reap(PsAProc *proc) {
    if (proc->reaped) {
        return 1;
    }
#ifndef _WIN32
    {
        int status = 0;
        pid_t got = waitpid((pid_t)proc->pid, &status, WNOHANG);
        if (got == (pid_t)proc->pid) {
            proc->code = ps_aproc_code(status);
            proc->reaped = 1;
            proc->live = 0;
            return 1;
        }
        if (got < 0 && errno != EINTR) {
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

/* `await proc.wait()`: the exit code of the child. The call returns 1 when
   the child already exited (the code is in the record) and 0 when the
   caller has to wait; the loop's pump wakes the parked task when the child
   exits. */
int64_t ps_aproc_wait(void *box) {
    PsAProc *proc = ps_aproc_check(box);
    if (ps_aproc_reap(proc)) {
        return 1;
    }
    /* Park the running task on the record. The wake is the pump's job. */
    proc->waiter = ps_loop_current_task();
    if (proc->waiter == NULL) {
        ps_raise_cstr(PS_RUNTIME, "awaiting a process is only usable inside a coroutine");
    }
    return 0;
}

/* The exit code of a reaped child. */
int64_t ps_aproc_code_of(void *box) {
    PsAProc *proc = ps_aproc_check(box);
    if (!proc->reaped && !ps_aproc_reap(proc)) {
        /* The child still runs: the code is not there yet. CPython's
           returncode is None until the child exits; the lowered subset
           reads -1 instead. */
        return -1;
    }
    return proc->code;
}

/* The loop's pump: reap the children that exited and wake the tasks parked
   on them. The loop calls this between iterations, so the wakeup lands
   before the next drain. */
void ps_aproc_pump(void) {
    PsAProc *proc = ps_aprocs;
    while (proc != NULL) {
        if (proc->live && proc->waiter != NULL && ps_aproc_reap(proc)) {
            void *waiter = proc->waiter;
            proc->waiter = NULL;
            ps_loop_wake_task(waiter);
        }
        proc = proc->next;
    }
}

/* True while a child still runs: the loop keeps spinning until the last
   one exits, the shape the to_thread pending check has. */
int32_t ps_aproc_pending(void) {
    PsAProc *proc = ps_aprocs;
    while (proc != NULL) {
        if (proc->live) {
            return 1;
        }
        proc = proc->next;
    }
    return 0;
}
