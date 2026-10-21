/* datetime.strptime: a subset of CPython's _strptime.

Supported directives: %Y %y %m %d %H %I %M %S %f %j %w %p %b %B %a %A %%.
Each directive mirrors the CPython regex alternation: a digit run that would
exceed the directive bound gives back its last digit so a shorter alternative
wins, names are case-insensitive longest-prefix matches, and a whitespace run
in the format matches one or more whitespace characters in the input. A
timezone directive is rejected: the DateTime class is naive and silently
dropping an offset would diverge from CPython. Like CPython, a %j day of year
overrides %m/%d, and a %a/%A/%w weekday is consumed but not validated.
*/

#include "ps_rt.h"

#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    int64_t year;
    int64_t month;
    int64_t day;
    int64_t hour;
    int64_t minute;
    int64_t second;
    int64_t microsecond;
} PsDateTime;

static const char *PS_MONTH_FULL[] = {
    "January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November",
    "December",
};
static const char *PS_MONTH_ABBR[] = {
    "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
};
static const char *PS_DAY_FULL[] = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"};
static const char *PS_DAY_ABBR[] = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"};

static void ps_clip(char *dst, int64_t avail, int64_t n, const char *s) {
    int64_t take = n < avail ? n : avail;
    memcpy(dst, s, (size_t)take);
    dst[take] = '\0';
}

static void ps_no_match(int64_t n, const char *text, int64_t flen, const char *fmt) {
    char msg[512];
    char t[160];
    char f[160];
    ps_clip(t, 158, n, text);
    ps_clip(f, 158, flen, fmt);
    snprintf(msg, sizeof msg, "time data '%s' does not match format '%s'", t, f);
    ps_raise_cstr(PS_VALUE, msg);
}

static void ps_bad_directive(char c, int64_t flen, const char *fmt) {
    char msg[512];
    char f[160];
    ps_clip(f, 158, flen, fmt);
    snprintf(msg, sizeof msg, "'%c' is a bad directive in format '%s'", c, f);
    ps_raise_cstr(PS_VALUE, msg);
}

static void ps_unconverted(int64_t n, const char *text, int64_t rest) {
    char msg[512];
    char tail[160];
    ps_clip(tail, 158, n - rest, text + rest);
    snprintf(msg, sizeof msg, "unconverted data remains: %s", tail);
    ps_raise_cstr(PS_VALUE, msg);
}

static int ps_is_digit(char c) { return c >= '0' && c <= '9'; }

static int ps_is_space(char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f'; }

static char ps_lower(char c) { return c >= 'A' && c <= 'Z' ? (char)(c - 'A' + 'a') : c; }

/* Bounded greedy digit run. Like CPython's ordered alternation, a run that
   would exceed `hi` gives back its last digit so a shorter alternative wins. */
static int64_t ps_read_num(int64_t n, const char *s, int64_t *at, int width, int64_t lo, int64_t hi) {
    int64_t start = *at;
    int64_t value = 0;
    int count = 0;
    while (*at < n && ps_is_digit(s[*at]) && count < width) {
        value = value * 10 + (s[*at] - '0');
        (*at)++;
        count++;
    }
    while (count > 1 && value > hi) {
        (*at)--;
        value /= 10;
        count--;
    }
    if (count == 0 || value < lo || value > hi) {
        *at = start;
        return -1;
    }
    return value;
}

/* Fixed width, like CPython's %y (\d\d) and %Y (\d\d\d\d). */
static int64_t ps_read_fixed(int64_t n, const char *s, int64_t *at, int width) {
    int64_t start = *at;
    int64_t value = 0;
    int i;
    for (i = 0; i < width; i++) {
        if (*at >= n || !ps_is_digit(s[*at])) {
            *at = start;
            return -1;
        }
        value = value * 10 + (s[*at] - '0');
        (*at)++;
    }
    return value;
}

/* Case-insensitive longest match over an abbr and a full table. 1-based, 0 when nothing matched. */
static int64_t ps_match_name(
    int64_t n, const char *s, int64_t *at, const char *const *abbr, const char *const *full, int count
) {
    int64_t best_len = 0;
    int64_t best_index = 0;
    int64_t best_end = 0;
    int i;
    for (i = 0; i < count; i++) {
        const char *cands[2];
        int c;
        cands[0] = abbr == NULL ? NULL : abbr[i];
        cands[1] = full == NULL ? NULL : full[i];
        for (c = 0; c < 2; c++) {
            const char *name = cands[c];
            int64_t len;
            int64_t k;
            if (name == NULL) {
                continue;
            }
            len = (int64_t)strlen(name);
            if (len > best_len && *at + len <= n) {
                int same = 1;
                for (k = 0; k < len; k++) {
                    if (ps_lower(s[*at + k]) != ps_lower(name[k])) {
                        same = 0;
                        break;
                    }
                }
                if (same) {
                    best_len = len;
                    best_index = i + 1;
                    best_end = *at + len;
                }
            }
        }
    }
    if (best_len == 0) {
        return 0;
    }
    *at = best_end;
    return best_index;
}

static int64_t ps_jan1_ordinal(int64_t year) {
    int64_t y1 = year - 1;
    return y1 * 365 + y1 / 4 - y1 / 100 + y1 / 400 + 1;
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
    *month = 12;
    *day = 31;
}

void ps_dt_strptime(int64_t flen, const char *fmt, int64_t n, const char *text, PsDateTime *out) {
    /* Defaults match CPython: 1900-01-01T00:00:00 with no fraction. A repeated
       directive is a regex group redefinition and fails, like CPython. */
    int64_t y = 1900;
    int64_t m = 1;
    int64_t d = 1;
    int64_t hour = -1;
    int64_t h_pos = -1;
    int64_t i_val = -1;
    int64_t i_pos = -1;
    int64_t M = 0;
    int64_t S = 0;
    int64_t f = -1;
    int64_t j = -1;
    int64_t pm = -1;
    int64_t fi = 0;
    int64_t ti = 0;
    int64_t v;
    int64_t index;
    unsigned char seen[128] = {0};

    while (fi < flen) {
        char c = fmt[fi];
        if (c != '%') {
            if (ps_is_space(c)) {
                /* A whitespace run in the format is \s+ in the input. */
                if (ti >= n || !ps_is_space(text[ti])) {
                    ps_no_match(n, text, flen, fmt);
                }
                while (ti < n && ps_is_space(text[ti])) {
                    ti++;
                }
                while (fi < flen && ps_is_space(fmt[fi])) {
                    fi++;
                }
                continue;
            }
            if (ti >= n || text[ti] != c) {
                ps_no_match(n, text, flen, fmt);
            }
            ti++;
            fi++;
            continue;
        }
        fi++;
        if (fi >= flen) {
            ps_raise_cstr(PS_VALUE, "stray % in format");
        }
        c = fmt[fi++];
        if (c != '%') {
            if (seen[(unsigned char)c]) {
                ps_raise_cstr(PS_VALUE, "redefinition of group name");
            }
            seen[(unsigned char)c] = 1;
        }
        switch (c) {
        case 'Y':
            v = ps_read_fixed(n, text, &ti, 4);
            if (v < 0) {
                ps_no_match(n, text, flen, fmt);
            }
            y = v;
            break;
        case 'y':
            v = ps_read_fixed(n, text, &ti, 2);
            if (v < 0) {
                ps_no_match(n, text, flen, fmt);
            }
            y = v < 69 ? 2000 + v : 1900 + v;
            break;
        case 'm':
            v = ps_read_num(n, text, &ti, 2, 1, 12);
            if (v < 0) {
                ps_no_match(n, text, flen, fmt);
            }
            m = v;
            break;
        case 'd':
            v = ps_read_num(n, text, &ti, 2, 1, 31);
            if (v < 0) {
                ps_no_match(n, text, flen, fmt);
            }
            d = v;
            break;
        case 'H':
            v = ps_read_num(n, text, &ti, 2, 0, 23);
            if (v < 0) {
                ps_no_match(n, text, flen, fmt);
            }
            hour = v;
            h_pos = fi;
            break;
        case 'I':
            v = ps_read_num(n, text, &ti, 2, 1, 12);
            if (v < 0) {
                ps_no_match(n, text, flen, fmt);
            }
            i_val = v;
            i_pos = fi;
            break;
        case 'M':
            v = ps_read_num(n, text, &ti, 2, 0, 59);
            if (v < 0) {
                ps_no_match(n, text, flen, fmt);
            }
            M = v;
            break;
        case 'S':
            v = ps_read_num(n, text, &ti, 2, 0, 61);
            if (v < 0) {
                ps_no_match(n, text, flen, fmt);
            }
            S = v;
            break;
        case 'f':
            v = 0;
            {
                int count = 0;
                while (ti < n && ps_is_digit(text[ti]) && count < 6) {
                    v = v * 10 + (text[ti] - '0');
                    ti++;
                    count++;
                }
                while (count < 6) {
                    v *= 10;
                    count++;
                }
                if (count == 0) {
                    ps_no_match(n, text, flen, fmt);
                }
            }
            f = v;
            break;
        case 'j':
            v = ps_read_num(n, text, &ti, 3, 1, 366);
            if (v < 0) {
                ps_no_match(n, text, flen, fmt);
            }
            j = v;
            break;
        case 'w':
            /* Consumed, never validated, like CPython. */
            if (ti < n && text[ti] >= '0' && text[ti] <= '6') {
                ti++;
            } else {
                ps_no_match(n, text, flen, fmt);
            }
            break;
        case 'p': {
            int64_t save = ti;
            static const char *ps_am[] = {"am"};
            static const char *ps_pm[] = {"pm"};
            static const char *ps_am_full[] = {"AM"};
            static const char *ps_pm_full[] = {"PM"};
            index = ps_match_name(n, text, &ti, ps_am, ps_am_full, 1);
            if (index != 0) {
                pm = 0;
                break;
            }
            ti = save;
            index = ps_match_name(n, text, &ti, ps_pm, ps_pm_full, 1);
            if (index == 0) {
                ti = save;
                ps_no_match(n, text, flen, fmt);
            }
            pm = 1;
            break;
        }
        case 'b':
            /* %b is the abbreviation table only: "June" leaves "e" unconverted. */
            index = ps_match_name(n, text, &ti, PS_MONTH_ABBR, NULL, 12);
            if (index == 0) {
                ps_no_match(n, text, flen, fmt);
            }
            m = index;
            break;
        case 'B':
            index = ps_match_name(n, text, &ti, NULL, PS_MONTH_FULL, 12);
            if (index == 0) {
                ps_no_match(n, text, flen, fmt);
            }
            m = index;
            break;
        case 'a':
            index = ps_match_name(n, text, &ti, PS_DAY_ABBR, NULL, 7);
            if (index == 0) {
                ps_no_match(n, text, flen, fmt);
            }
            break;
        case 'A':
            index = ps_match_name(n, text, &ti, NULL, PS_DAY_FULL, 7);
            if (index == 0) {
                ps_no_match(n, text, flen, fmt);
            }
            break;
        case '%':
            if (ti >= n || text[ti] != '%') {
                ps_no_match(n, text, flen, fmt);
            }
            ti++;
            break;
        default:
            ps_bad_directive(c, flen, fmt);
        }
    }

    if (ti != n) {
        ps_unconverted(n, text, ti);
    }

    /* CPython applies the parsed groups in format order, so the last of a
       conflicting pair (%H vs %I) wins; %I always consults the final %p. */
    if (j >= 0) {
        /* %j wins over %m/%d, computed from the year like CPython. */
        ps_ord_ymd(ps_jan1_ordinal(y) + j - 1, &y, &m, &d);
    }
    if (i_pos > h_pos) {
        hour = i_val;
        if (pm < 0 || pm == 0) {
            if (hour == 12) {
                hour = 0;
            }
        } else if (hour != 12) {
            hour += 12;
        }
    }
    if (hour < 0) {
        hour = 0;
    }
    if (f < 0) {
        f = 0;
    }

    /* The same range checks the datetime constructor makes. %S can read 60 or
       61 for leap seconds, but the constructor rejects them. */
    if (S > 59) {
        ps_raise_cstr(PS_VALUE, "second must be in 0..59");
    }
    if (m < 1 || m > 12) {
        ps_raise_cstr(PS_VALUE, "month must be in 1..12");
    }
    {
        static const int mdays[] = {0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
        int64_t leap = (y % 4 == 0) && (y % 100 != 0 || y % 400 == 0);
        int64_t dim = mdays[m];
        if (m == 2 && leap) {
            dim = 29;
        }
        if (d < 1 || d > dim) {
            ps_raise_cstr(PS_VALUE, "day is out of range for month");
        }
    }
    if (y < 1 || y > 9999) {
        ps_raise_cstr(PS_VALUE, "year is out of range");
    }

    out->year = y;
    out->month = m;
    out->day = d;
    out->hour = hour;
    out->minute = M;
    out->second = S;
    out->microsecond = f;
}
