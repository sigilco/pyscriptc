/* datetime.date, datetime.datetime, datetime.time, and timedelta.

Field order matches the synthetic classes: Date is year, month, day.
DateTime adds hour, minute, second, microsecond. TimeDelta is days, seconds,
microseconds, normalized the way CPython normalizes them.
*/

#include "ps_rt.h"

#include <stdint.h>
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
    int64_t hour;
    int64_t minute;
    int64_t second;
    int64_t microsecond;
} PsTod;

typedef struct {
    int64_t days;
    int64_t seconds;
    int64_t microseconds;
} PsDelta;

static const int64_t PS_DAY_US = 86400000000LL;

static int ps_leap(int64_t year) {
    return (year % 4 == 0) && (year % 100 != 0 || year % 400 == 0);
}

static int ps_valid_ymd(int64_t y, int64_t m, int64_t d) {
    static const int mdays[] = {0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    int dim;
    if (y < 1 || y > 9999 || m < 1 || m > 12 || d < 1) {
        return 0;
    }
    dim = mdays[m];
    if (m == 2 && ps_leap(y)) {
        dim = 29;
    }
    return d <= dim;
}

static int64_t ps_ymd_ord(int64_t y, int64_t m, int64_t d) {
    static const int before[] = {0, 0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334};
    int64_t y1 = y - 1;
    int64_t days = before[m];
    if (m > 2 && ps_leap(y)) {
        days += 1;
    }
    return y1 * 365 + y1 / 4 - y1 / 100 + y1 / 400 + days + d;
}

static void ps_ord_ymd(int64_t ordinal, int64_t *year, int64_t *month, int64_t *day) {
    static const int mdays[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    int64_t n = ordinal - 1;
    int64_t n400 = n / 146097;
    int64_t n100;
    int64_t n4;
    int64_t n1;
    int leap;
    int m;
    n %= 146097;
    n100 = n / 36524;
    n %= 36524;
    n4 = n / 1461;
    n %= 1461;
    n1 = n / 365;
    n %= 365;
    *year = n400 * 400 + n100 * 100 + n4 * 4 + n1 + 1;
    if (n1 == 4 || n100 == 4) {
        *year -= 1;
        *month = 12;
        *day = 31;
        return;
    }
    leap = n1 == 3 && (n4 != 24 || n100 == 3);
    for (m = 1; m <= 12; m++) {
        int dim = mdays[m - 1];
        if (m == 2 && leap) {
            dim += 1;
        }
        if (n < dim) {
            *month = m;
            *day = n + 1;
            return;
        }
        n -= dim;
    }
    ps_raise_cstr(PS_VALUE, "date out of range");
}

static void ps_put_ord(int64_t ordinal, PsDate *out) {
    int64_t max_ord = ps_ymd_ord(9999, 12, 31);
    if (ordinal < 1 || ordinal > max_ord) {
        ps_raise_cstr(PS_VALUE, "date out of range");
    }
    ps_ord_ymd(ordinal, &out->year, &out->month, &out->day);
}

static void ps_pad(char *dst, int width, int64_t value) {
    int i;
    for (i = width - 1; i >= 0; i--) {
        dst[i] = (char)('0' + (value % 10));
        value /= 10;
    }
}

static void ps_iso_date(int64_t y, int64_t m, int64_t d, PsBuf *out) {
    char buf[16];
    ps_pad(buf, 4, y);
    buf[4] = '-';
    ps_pad(buf + 5, 2, m);
    buf[7] = '-';
    ps_pad(buf + 8, 2, d);
    ps_buf_copy(out, buf, 10);
}

static void ps_iso_tod(int64_t h, int64_t m, int64_t s, int64_t us, PsBuf *out) {
    char buf[24];
    int n = 8;
    ps_pad(buf, 2, h);
    buf[2] = ':';
    ps_pad(buf + 3, 2, m);
    buf[5] = ':';
    ps_pad(buf + 6, 2, s);
    if (us != 0) {
        buf[8] = '.';
        ps_pad(buf + 9, 6, us);
        n = 15;
    }
    ps_buf_copy(out, buf, n);
}

static int ps_digits(const char *s, int64_t n, int64_t at, int width, int64_t *out) {
    int64_t value = 0;
    int i;
    if (at + width > n) {
        return 0;
    }
    for (i = 0; i < width; i++) {
        char c = s[at + i];
        if (c < '0' || c > '9') {
            return 0;
        }
        value = value * 10 + (c - '0');
    }
    *out = value;
    return 1;
}

static void ps_delta_norm(__int128 us, PsDelta *out) {
    __int128 day;
    __int128 rem;
    day = us / PS_DAY_US;
    rem = us - day * PS_DAY_US;
    if (rem < 0) {
        rem += PS_DAY_US;
        day -= 1;
    }
    if (day > INT64_MAX || day < INT64_MIN) {
        ps_raise_cstr(PS_OVERFLOW, "timedelta out of range");
    }
    out->days = (int64_t)day;
    out->seconds = (int64_t)(rem / 1000000);
    out->microseconds = (int64_t)(rem % 1000000);
}

static __int128 ps_delta_us(const PsDelta *d) {
    return (__int128)d->days * PS_DAY_US + (__int128)d->seconds * 1000000 + d->microseconds;
}

void ps_date_ymd(int64_t y, int64_t m, int64_t d, PsDate *out) {
    if (!ps_valid_ymd(y, m, d)) {
        ps_raise_cstr(PS_VALUE, "date out of range");
    }
    out->year = y;
    out->month = m;
    out->day = d;
}

void ps_date_today(PsDate *out) {
    /* Filled by ps_time via a local date. Implemented here with time.h. */
    extern void ps_date_today_fill(PsDate *out);
    ps_date_today_fill(out);
}

void ps_date_fromiso(int64_t n, const char *s, PsDate *out) {
    int64_t y, m, d;
    if (n != 10 || s[4] != '-' || s[7] != '-' || !ps_digits(s, n, 0, 4, &y) || !ps_digits(s, n, 5, 2, &m) ||
        !ps_digits(s, n, 8, 2, &d)) {
        ps_raise_cstr(PS_VALUE, "invalid isoformat date");
    }
    ps_date_ymd(y, m, d, out);
}

void ps_date_fromord(int64_t ordinal, PsDate *out) { ps_put_ord(ordinal, out); }

void ps_date_iso(PsDate *d, PsBuf *out) { ps_iso_date(d->year, d->month, d->day, out); }

int64_t ps_date_ord(PsDate *d) { return ps_ymd_ord(d->year, d->month, d->day); }

int64_t ps_date_weekday(PsDate *d) { return (ps_ymd_ord(d->year, d->month, d->day) + 6) % 7; }

int32_t ps_calendar_isleap(int64_t year) {
    return ((year % 4 == 0) && (year % 100 != 0 || year % 400 == 0)) ? 1 : 0;
}

int64_t ps_calendar_leapdays(int64_t y1, int64_t y2) {
    y1--;
    y2--;
    return (y2 / 4 - y1 / 4) - (y2 / 100 - y1 / 100) + (y2 / 400 - y1 / 400);
}

int64_t ps_calendar_weekday(int64_t year, int64_t month, int64_t day) {
    return (ps_ymd_ord(year, month, day) + 6) % 7;
}

void ps_date_add(PsDate *d, PsDelta *delta, int32_t sign, PsDate *out) {
    int64_t ord = ps_ymd_ord(d->year, d->month, d->day);
    __int128 days = delta->days;
    __int128 sum;
    if (sign < 0) {
        days = -days;
    }
    sum = (__int128)ord + days;
    if (sum < 1 || sum > ps_ymd_ord(9999, 12, 31)) {
        ps_raise_cstr(PS_VALUE, "date out of range");
    }
    ps_put_ord((int64_t)sum, out);
}

void ps_date_diff(PsDate *a, PsDate *b, PsDelta *out) {
    out->days = ps_ymd_ord(a->year, a->month, a->day) - ps_ymd_ord(b->year, b->month, b->day);
    out->seconds = 0;
    out->microseconds = 0;
}

static void ps_check_tod(int64_t h, int64_t mi, int64_t s, int64_t us) {
    if (h < 0 || h > 23 || mi < 0 || mi > 59 || s < 0 || s > 59 || us < 0 || us > 999999) {
        ps_raise_cstr(PS_VALUE, "time out of range");
    }
}

void ps_dt_new(int64_t y, int64_t m, int64_t d, int64_t h, int64_t mi, int64_t s, int64_t us, PsDateTime *out) {
    if (!ps_valid_ymd(y, m, d)) {
        ps_raise_cstr(PS_VALUE, "date out of range");
    }
    ps_check_tod(h, mi, s, us);
    out->year = y;
    out->month = m;
    out->day = d;
    out->hour = h;
    out->minute = mi;
    out->second = s;
    out->microsecond = us;
}

void ps_dt_now(PsDateTime *out) {
    extern void ps_dt_now_fill(PsDateTime *out);
    ps_dt_now_fill(out);
}

void ps_dt_fromtimestamp(double ts, PsDateTime *out) {
    extern void ps_dt_fromtimestamp_fill(double ts, PsDateTime *out);
    ps_dt_fromtimestamp_fill(ts, out);
}

void ps_date_fromtimestamp(double ts, PsDate *out) {
    extern void ps_date_fromtimestamp_fill(double ts, PsDate *out);
    ps_date_fromtimestamp_fill(ts, out);
}

void ps_dt_fromiso(int64_t n, const char *s, PsDateTime *out) {
    int64_t y, m, d, h = 0, mi = 0, sec = 0, us = 0;
    if (n != 10 && n != 19 && n != 26) {
        ps_raise_cstr(PS_VALUE, "invalid isoformat datetime");
    }
    if (s[4] != '-' || s[7] != '-' || !ps_digits(s, n, 0, 4, &y) || !ps_digits(s, n, 5, 2, &m) ||
        !ps_digits(s, n, 8, 2, &d)) {
        ps_raise_cstr(PS_VALUE, "invalid isoformat datetime");
    }
    if (n >= 19) {
        if ((s[10] != 'T' && s[10] != ' ') || s[13] != ':' || s[16] != ':' || !ps_digits(s, n, 11, 2, &h) ||
            !ps_digits(s, n, 14, 2, &mi) || !ps_digits(s, n, 17, 2, &sec)) {
            ps_raise_cstr(PS_VALUE, "invalid isoformat datetime");
        }
    }
    if (n == 26) {
        if (s[19] != '.' || !ps_digits(s, n, 20, 6, &us)) {
            ps_raise_cstr(PS_VALUE, "invalid isoformat datetime");
        }
    }
    ps_dt_new(y, m, d, h, mi, sec, us, out);
}

void ps_dt_iso(PsDateTime *d, PsBuf *out) {
    char buf[40];
    int n = 19;
    ps_pad(buf, 4, d->year);
    buf[4] = '-';
    ps_pad(buf + 5, 2, d->month);
    buf[7] = '-';
    ps_pad(buf + 8, 2, d->day);
    buf[10] = 'T';
    ps_pad(buf + 11, 2, d->hour);
    buf[13] = ':';
    ps_pad(buf + 14, 2, d->minute);
    buf[16] = ':';
    ps_pad(buf + 17, 2, d->second);
    if (d->microsecond != 0) {
        buf[19] = '.';
        ps_pad(buf + 20, 6, d->microsecond);
        n = 26;
    }
    ps_buf_copy(out, buf, n);
}

void ps_dt_date(PsDateTime *d, PsDate *out) { ps_date_ymd(d->year, d->month, d->day, out); }

void ps_date_min(PsDate *out) {
    out->year = 1;
    out->month = 1;
    out->day = 1;
}

void ps_date_max(PsDate *out) {
    out->year = 9999;
    out->month = 12;
    out->day = 31;
}

void ps_dt_min(PsDateTime *out) {
    out->year = 1;
    out->month = 1;
    out->day = 1;
    out->hour = 0;
    out->minute = 0;
    out->second = 0;
    out->microsecond = 0;
}

void ps_dt_max(PsDateTime *out) {
    out->year = 9999;
    out->month = 12;
    out->day = 31;
    out->hour = 23;
    out->minute = 59;
    out->second = 59;
    out->microsecond = 999999;
}

void ps_dt_combine(PsDate *d, PsTod *t, PsDateTime *out) {
    ps_dt_new(d->year, d->month, d->day, t->hour, t->minute, t->second, t->microsecond, out);
}

/* ps_dtparse.c owns the parser; the entry point keeps the ABI shape: the
   text comes first, the format second, like the Python call. */
void ps_dt_strptime(int64_t flen, const char *fmt, int64_t n, const char *text, PsDateTime *out);

void ps_dt_strptime_entry(int64_t n, const char *text, int64_t flen, const char *fmt, PsDateTime *out) {
    ps_dt_strptime(flen, fmt, n, text, out);
}

double ps_dt_timestamp(PsDateTime *d) {
    /* A naive datetime is read as local time, like CPython. */
    struct tm tm;
    time_t t;
    tm.tm_year = (int)(d->year - 1900);
    tm.tm_mon = (int)(d->month - 1);
    tm.tm_mday = (int)d->day;
    tm.tm_hour = (int)d->hour;
    tm.tm_min = (int)d->minute;
    tm.tm_sec = (int)d->second;
    tm.tm_isdst = -1;
    t = mktime(&tm);
    if (t == (time_t)-1) {
        ps_raise_cstr(PS_VALUE, "timestamp out of range");
    }
    return (double)t + (double)d->microsecond / 1000000.0;
}

void ps_dt_time(PsDateTime *d, PsTod *out) {
    ps_check_tod(d->hour, d->minute, d->second, d->microsecond);
    out->hour = d->hour;
    out->minute = d->minute;
    out->second = d->second;
    out->microsecond = d->microsecond;
}

static void ps_dt_parts(const PsDateTime *a, int64_t *ord, __int128 *us) {
    *ord = ps_ymd_ord(a->year, a->month, a->day);
    *us = (((__int128)a->hour * 3600 + a->minute * 60 + a->second) * 1000000) + a->microsecond;
}

void ps_dt_add(PsDateTime *a, PsDelta *delta, int32_t sign, PsDateTime *out) {
    int64_t ord;
    __int128 us;
    __int128 extra;
    __int128 rem;
    __int128 add = ps_delta_us(delta);
    if (sign < 0) {
        add = -add;
    }
    ps_dt_parts(a, &ord, &us);
    us += add;
    extra = us / PS_DAY_US;
    rem = us - extra * PS_DAY_US;
    if (rem < 0) {
        rem += PS_DAY_US;
        extra -= 1;
    }
    if (extra > INT64_MAX || extra < INT64_MIN || (extra > 0 && ord > INT64_MAX - (int64_t)extra) ||
        (extra < 0 && ord < (int64_t)(-extra))) {
        ps_raise_cstr(PS_VALUE, "datetime out of range");
    }
    ps_put_ord(ord + (int64_t)extra, (PsDate *)out);
    out->hour = (int64_t)(rem / 3600000000LL);
    rem %= 3600000000LL;
    out->minute = (int64_t)(rem / 60000000LL);
    rem %= 60000000LL;
    out->second = (int64_t)(rem / 1000000LL);
    out->microsecond = (int64_t)(rem % 1000000LL);
}

void ps_dt_diff(PsDateTime *a, PsDateTime *b, PsDelta *out) {
    int64_t oa, ob;
    __int128 ua, ub;
    ps_dt_parts(a, &oa, &ua);
    ps_dt_parts(b, &ob, &ub);
    ps_delta_norm(((__int128)oa - ob) * PS_DAY_US + (ua - ub), out);
}

void ps_tod_new(int64_t h, int64_t mi, int64_t s, int64_t us, PsTod *out) {
    ps_check_tod(h, mi, s, us);
    out->hour = h;
    out->minute = mi;
    out->second = s;
    out->microsecond = us;
}

void ps_tod_iso(PsTod *t, PsBuf *out) { ps_iso_tod(t->hour, t->minute, t->second, t->microsecond, out); }

void ps_delta_new(int64_t days, int64_t seconds, int64_t microseconds, PsDelta *out) {
    ps_delta_norm((__int128)days * PS_DAY_US + (__int128)seconds * 1000000 + microseconds, out);
}

void ps_delta_neg(PsDelta *d, PsDelta *out) { ps_delta_norm(-ps_delta_us(d), out); }

void ps_delta_add(PsDelta *a, PsDelta *b, int32_t sign, PsDelta *out) {
    __int128 add = ps_delta_us(b);
    if (sign < 0) {
        add = -add;
    }
    ps_delta_norm(ps_delta_us(a) + add, out);
}

double ps_delta_total(PsDelta *d) { return (double)ps_delta_us(d) / 1000000.0; }
