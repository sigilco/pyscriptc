/* time.time, sleep, gmtime, localtime, strftime, and mktime.

struct_time fields match CPython: tm_mon is 1-12, tm_wday is Monday 0,
tm_yday is 1-366. C's tm_* is converted at the boundary.
*/

#include "ps_rt.h"

#include <errno.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef struct {
    int64_t year;
    int64_t month;
    int64_t day;
} PsDate;

typedef struct {
    int64_t year;
    int64_t month;
    int64_t day;
    int64_t hour;
    int64_t minute;
    int64_t second;
    int64_t microsecond;
} PsDateTime;

typedef struct {
    int64_t tm_year;
    int64_t tm_mon;
    int64_t tm_mday;
    int64_t tm_hour;
    int64_t tm_min;
    int64_t tm_sec;
    int64_t tm_wday;
    int64_t tm_yday;
    int64_t tm_isdst;
} PsStructTime;

static void ps_fill_stm(const struct tm *tm, PsStructTime *out) {
    out->tm_year = (int64_t)tm->tm_year + 1900;
    out->tm_mon = (int64_t)tm->tm_mon + 1;
    out->tm_mday = tm->tm_mday;
    out->tm_hour = tm->tm_hour;
    out->tm_min = tm->tm_min;
    out->tm_sec = tm->tm_sec;
    out->tm_wday = (tm->tm_wday + 6) % 7;
    out->tm_yday = (int64_t)tm->tm_yday + 1;
    out->tm_isdst = tm->tm_isdst;
}

static int ps_read_stm(const PsStructTime *in, struct tm *tm) {
    if (in->tm_year < 1900 || in->tm_mon < 1 || in->tm_mon > 12 || in->tm_mday < 1 || in->tm_mday > 31 ||
        in->tm_hour < 0 || in->tm_hour > 23 || in->tm_min < 0 || in->tm_min > 59 || in->tm_sec < 0 ||
        in->tm_sec > 60) {
        return 0;
    }
    tm->tm_year = (int)(in->tm_year - 1900);
    tm->tm_mon = (int)(in->tm_mon - 1);
    tm->tm_mday = (int)in->tm_mday;
    tm->tm_hour = (int)in->tm_hour;
    tm->tm_min = (int)in->tm_min;
    tm->tm_sec = (int)in->tm_sec;
    tm->tm_wday = (int)((in->tm_wday + 1) % 7);
    tm->tm_yday = (int)(in->tm_yday - 1);
    tm->tm_isdst = (int)in->tm_isdst;
    return 1;
}

static time_t ps_as_time(double ts) {
    if (!isfinite(ts) || ts >= 9223372036854775808.0 || ts < -9223372036854775808.0) {
        ps_raise_cstr(PS_VALUE, "timestamp out of range");
    }
    return (time_t)ts;
}

static double ps_clock(clockid_t id) {
    struct timespec ts;
    if (clock_gettime(id, &ts) != 0) {
        ps_raise_cstr(PS_RUNTIME, "clock_gettime failed");
    }
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1000000000.0;
}

double ps_time_time(void) { return ps_clock(CLOCK_REALTIME); }

double ps_time_monotonic(void) { return ps_clock(CLOCK_MONOTONIC); }

void ps_time_sleep(double seconds) {
    struct timespec req;
    if (!isfinite(seconds) || seconds < 0) {
        ps_raise_cstr(PS_VALUE, "sleep length must be non-negative");
    }
    req.tv_sec = (time_t)seconds;
    req.tv_nsec = (long)((seconds - (double)req.tv_sec) * 1000000000.0);
    if (req.tv_nsec < 0) {
        req.tv_nsec = 0;
    }
    if (req.tv_nsec > 999999999) {
        req.tv_nsec = 999999999;
    }
    while (nanosleep(&req, &req) != 0) {
        if (errno != EINTR) {
            ps_raise_cstr(PS_RUNTIME, "sleep failed");
        }
    }
}

static void ps_gm(double ts, PsStructTime *out) {
    time_t t = ps_as_time(ts);
    struct tm tm;
    if (gmtime_r(&t, &tm) == NULL) {
        ps_raise_cstr(PS_VALUE, "timestamp out of range");
    }
    ps_fill_stm(&tm, out);
}

static void ps_local(double ts, PsStructTime *out) {
    time_t t = ps_as_time(ts);
    struct tm tm;
    if (localtime_r(&t, &tm) == NULL) {
        ps_raise_cstr(PS_VALUE, "timestamp out of range");
    }
    ps_fill_stm(&tm, out);
}

void ps_time_gm(double ts, PsStructTime *out) { ps_gm(ts, out); }

void ps_time_local(double ts, PsStructTime *out) { ps_local(ts, out); }

void ps_time_strftime(int64_t n, const char *fmt, PsStructTime *st, PsBuf *out) {
    struct tm tm;
    char buf[1024];
    size_t wrote;
    char *text;
    if (n < 0 || n > 200) {
        ps_raise_cstr(PS_VALUE, "strftime format is too long");
    }
    text = malloc((size_t)n + 1);
    if (text == NULL) {
        abort();
    }
    if (n > 0) {
        memcpy(text, fmt, (size_t)n);
    }
    text[n] = '\0';
    if (!ps_read_stm(st, &tm)) {
        free(text);
        ps_raise_cstr(PS_VALUE, "struct_time out of range");
    }
    wrote = strftime(buf, sizeof buf, text, &tm);
    free(text);
    if (wrote == 0) {
        ps_raise_cstr(PS_VALUE, "strftime failed");
    }
    ps_buf_copy(out, buf, (int64_t)wrote);
}

double ps_time_mktime(PsStructTime *st) {
    struct tm tm;
    time_t t;
    if (!ps_read_stm(st, &tm)) {
        ps_raise_cstr(PS_VALUE, "struct_time out of range");
    }
    t = mktime(&tm);
    if (t == (time_t)-1) {
        ps_raise_cstr(PS_VALUE, "mktime failed");
    }
    return (double)t;
}

void ps_date_today_fill(PsDate *out) {
    PsStructTime st;
    ps_local(ps_time_time(), &st);
    out->year = st.tm_year;
    out->month = st.tm_mon;
    out->day = st.tm_mday;
}

void ps_dt_now_fill(PsDateTime *out) {
    double now = ps_time_time();
    double whole = floor(now);
    double frac = now - whole;
    PsStructTime st;
    int64_t us;
    if (frac < 0) {
        frac = 0;
    }
    us = (int64_t)(frac * 1000000.0);
    if (us < 0) {
        us = 0;
    }
    if (us > 999999) {
        us = 999999;
    }
    ps_local(whole, &st);
    out->year = st.tm_year;
    out->month = st.tm_mon;
    out->day = st.tm_mday;
    out->hour = st.tm_hour;
    out->minute = st.tm_min;
    out->second = st.tm_sec;
    out->microsecond = us;
}
