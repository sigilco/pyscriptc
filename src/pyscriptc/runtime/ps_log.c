/* logging: the script subset of the logging module.

A Logger is `{ PsBuf name }`, mirroring the class layout the compiler
emits: the name is a PsBuf (16 bytes). getLogger records the name, and
every record call writes one line to stderr.

With no handler configured, CPython records fall through to the last-resort
stderr handler, whose format is the bare message. The default root level is
WARNING, so debug and info stay silent and warning and above print.
*/

#include "ps_rt.h"

#include <stdlib.h>
#include <string.h>

typedef struct {
    PsBuf name;
} PsLogger;

/* Level thresholds, mirroring the logging module's numeric order. */
enum { PS_LOG_DEBUG = 10, PS_LOG_INFO = 20, PS_LOG_WARNING = 30, PS_LOG_ERROR = 40, PS_LOG_CRITICAL = 50 };

void ps_log_new(int64_t n, const char *name, PsLogger *out) {
    out->name.len = n;
    out->name.data = ps_alloc(n + 1);
    if (n > 0) {
        memcpy(out->name.data, name, (size_t)n);
    }
    out->name.data[n] = '\0';
}

static void ps_log_emit(int64_t n, const char *msg) {
    ps_stderr_write(n, msg);
    ps_stderr_write(1, "\n");
}

static int ps_log_enabled(int64_t level) {
    /* debug(10) and info(20) stay silent at the default WARNING threshold;
       warning and above always print, matching CPython's last-resort
       handler and root level. */
    return level >= PS_LOG_WARNING;
}

void ps_log_debug(const PsLogger *logger, int64_t n, const char *msg) {
    (void)logger;
    if (ps_log_enabled(PS_LOG_DEBUG)) {
        ps_log_emit(n, msg);
    }
}

void ps_log_info(const PsLogger *logger, int64_t n, const char *msg) {
    (void)logger;
    if (ps_log_enabled(PS_LOG_INFO)) {
        ps_log_emit(n, msg);
    }
}

void ps_log_warning(const PsLogger *logger, int64_t n, const char *msg) {
    (void)logger;
    if (ps_log_enabled(PS_LOG_WARNING)) {
        ps_log_emit(n, msg);
    }
}

void ps_log_error(const PsLogger *logger, int64_t n, const char *msg) {
    (void)logger;
    if (ps_log_enabled(PS_LOG_ERROR)) {
        ps_log_emit(n, msg);
    }
}

void ps_log_critical(const PsLogger *logger, int64_t n, const char *msg) {
    (void)logger;
    if (ps_log_enabled(PS_LOG_CRITICAL)) {
        ps_log_emit(n, msg);
    }
}

void ps_log_exception(const PsLogger *logger, int64_t n, const char *msg) {
    /* Without a live traceback the record text is the message, the same
       line CPython's last-resort handler prints before the traceback. */
    (void)logger;
    if (ps_log_enabled(PS_LOG_ERROR)) {
        ps_log_emit(n, msg);
    }
}
