/* posixpath, os, stat predicates, fnmatch, and pathlib.Path.

Path objects are `{ PsBuf _path }`. OS errors raise RuntimeError. An unset
getenv raises KeyError, which is the subset stand-in for None.
*/

#include "ps_rt.h"

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

typedef struct {
    PsBuf path;
} PsPath;

/* Field names avoid the st_mtime macro from <sys/stat.h>. Layout is still
mode, size, mtime, which is what the Stat class stores. */
typedef struct {
    int64_t mode;
    int64_t size;
    double mtime;
} PsStat;

static void ps_os_fail(const char *what) { ps_raise_cstr(PS_RUNTIME, what); }

static char *ps_cstr(int64_t n, const char *s) {
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

static int ps_abs(int64_t n, const char *s) { return n > 0 && s[0] == '/'; }

int32_t ps_path_isabs(int64_t n, const char *s) { return ps_abs(n, s) ? 1 : 0; }

static void ps_copy_slice(const char *s, int64_t at, int64_t n, PsBuf *out) { ps_buf_copy(out, s + at, n); }

void ps_path_base(int64_t n, const char *s, PsBuf *out) {
    int64_t i = n;
    while (i > 0 && s[i - 1] != '/') {
        i -= 1;
    }
    ps_copy_slice(s, i, n - i, out);
}

void ps_path_dir(int64_t n, const char *s, PsBuf *out) {
    int64_t i = n;
    int64_t end;
    while (i > 0 && s[i - 1] != '/') {
        i -= 1;
    }
    end = i;
    if (end > 1) {
        while (end > 1 && s[end - 1] == '/') {
            end -= 1;
        }
    }
    ps_buf_copy(out, s, end);
}

void ps_path_join(PsBuf *parts, int64_t n, PsBuf *out) {
    PsGrow g;
    int64_t i;
    ps_grow_init(&g);
    for (i = 0; i < n; i++) {
        PsBuf part = parts[i];
        if (ps_abs(part.len, part.data)) {
            g.len = 0;
            ps_grow_add(&g, part.data, part.len);
            continue;
        }
        if (g.len > 0 && g.data[g.len - 1] != '/') {
            ps_grow_byte(&g, '/');
        }
        ps_grow_add(&g, part.data, part.len);
    }
    ps_grow_finish(&g, out);
}

PsSeq *ps_path_split(int64_t n, const char *s) {
    PsBuf dir;
    PsBuf base;
    PsSeq *seq = ps_seq_new((int64_t)sizeof(PsBuf), 2);
    ps_path_dir(n, s, &dir);
    ps_path_base(n, s, &base);
    ps_seq_set(seq, 0, &dir);
    ps_seq_set(seq, 1, &base);
    return seq;
}

PsSeq *ps_path_splitext(int64_t n, const char *s) {
    int64_t slash = -1;
    int64_t dot = -1;
    int64_t i;
    PsBuf root;
    PsBuf ext;
    PsSeq *seq;
    for (i = 0; i < n; i++) {
        if (s[i] == '/') {
            slash = i;
            dot = -1;
        } else if (s[i] == '.') {
            dot = i;
        }
    }
    if (dot <= slash + 1) {
        ps_buf_copy(&root, s, n);
        ps_buf_copy(&ext, "", 0);
    } else {
        ps_buf_copy(&root, s, dot);
        ps_buf_copy(&ext, s + dot, n - dot);
    }
    seq = ps_seq_new((int64_t)sizeof(PsBuf), 2);
    ps_seq_set(seq, 0, &root);
    ps_seq_set(seq, 1, &ext);
    return seq;
}

void ps_path_norm(int64_t n, const char *s, PsBuf *out) {
    int absolute = ps_abs(n, s);
    int64_t parts_at[64];
    int64_t parts_n[64];
    int count = 0;
    int64_t i = 0;
    PsGrow g;
    int p;
    while (i < n) {
        int64_t start;
        while (i < n && s[i] == '/') {
            i += 1;
        }
        start = i;
        while (i < n && s[i] != '/') {
            i += 1;
        }
        if (start == i) {
            break;
        }
        if (i - start == 1 && s[start] == '.') {
            continue;
        }
        if (i - start == 2 && s[start] == '.' && s[start + 1] == '.') {
            if (count > 0) {
                count -= 1;
            } else if (!absolute) {
                if (count == 64) {
                    ps_raise_cstr(PS_RUNTIME, "path is too deep");
                }
                parts_at[count] = start;
                parts_n[count] = 2;
                count += 1;
            }
            continue;
        }
        if (count == 64) {
            ps_raise_cstr(PS_RUNTIME, "path is too deep");
        }
        parts_at[count] = start;
        parts_n[count] = i - start;
        count += 1;
    }
    ps_grow_init(&g);
    if (absolute) {
        ps_grow_byte(&g, '/');
    }
    if (count == 0 && !absolute) {
        ps_grow_byte(&g, '.');
    }
    for (p = 0; p < count; p++) {
        if (!(absolute && p == 0)) {
            if (g.len > 0 && g.data[g.len - 1] != '/') {
                ps_grow_byte(&g, '/');
            }
        }
        ps_grow_add(&g, s + parts_at[p], parts_n[p]);
    }
    ps_grow_finish(&g, out);
}

void ps_os_getcwd(PsBuf *out) {
    char buf[4096];
    if (getcwd(buf, sizeof buf) == NULL) {
        ps_os_fail("getcwd failed");
    }
    ps_buf_copy(out, buf, (int64_t)strlen(buf));
}

void ps_path_abs(int64_t n, const char *s, PsBuf *out) {
    PsBuf cwd;
    PsBuf joined;
    PsBuf parts[2];
    if (ps_abs(n, s)) {
        ps_path_norm(n, s, out);
        return;
    }
    ps_os_getcwd(&cwd);
    parts[0] = cwd;
    parts[1].len = n;
    parts[1].data = (char *)s;
    ps_path_join(parts, 2, &joined);
    ps_path_norm(joined.len, joined.data, out);
    free(cwd.data);
    free(joined.data);
}

static int ps_stat_into(int64_t n, const char *s, struct stat *st) {
    char *text = ps_cstr(n, s);
    int rc = stat(text, st);
    free(text);
    return rc;
}

int32_t ps_path_exists(int64_t n, const char *s) {
    struct stat st;
    return ps_stat_into(n, s, &st) == 0;
}

int32_t ps_path_isfile(int64_t n, const char *s) {
    struct stat st;
    return ps_stat_into(n, s, &st) == 0 && S_ISREG(st.st_mode);
}

int32_t ps_path_isdir(int64_t n, const char *s) {
    struct stat st;
    return ps_stat_into(n, s, &st) == 0 && S_ISDIR(st.st_mode);
}

void ps_os_remove(int64_t n, const char *s) {
    char *text = ps_cstr(n, s);
    int rc = unlink(text);
    free(text);
    if (rc != 0) {
        ps_os_fail("remove failed");
    }
}

void ps_os_rename(int64_t an, const char *a, int64_t bn, const char *b) {
    char *left = ps_cstr(an, a);
    char *right = ps_cstr(bn, b);
    int rc = rename(left, right);
    free(left);
    free(right);
    if (rc != 0) {
        ps_os_fail("rename failed");
    }
}

void ps_os_makedirs(int64_t n, const char *s) {
    char *text = ps_cstr(n, s);
    int64_t i;
    for (i = 1; i <= n; i++) {
        int rc;
        char save;
        if (i < n && text[i] != '/') {
            continue;
        }
        save = text[i];
        text[i] = '\0';
        rc = mkdir(text, 0755);
        if (rc != 0 && errno == EEXIST && i == n) {
            free(text);
            ps_os_fail("makedirs failed");
        }
        if (rc != 0 && errno != EEXIST) {
            free(text);
            ps_os_fail("makedirs failed");
        }
        text[i] = save;
    }
    free(text);
}

void ps_os_getenv(int64_t n, const char *s, PsBuf *out) {
    char *text = ps_cstr(n, s);
    const char *value = getenv(text);
    free(text);
    if (value == NULL) {
        ps_raise_cstr(PS_KEY, "environment variable is unset");
    }
    ps_buf_copy(out, value, (int64_t)strlen(value));
}

int32_t ps_env_has(int64_t n, const char *s) {
    char *text = ps_cstr(n, s);
    const char *value = getenv(text);
    free(text);
    return value != NULL ? 1 : 0;
}

void ps_os_getenv_or(int64_t n, const char *s, int64_t dn, const char *d, PsBuf *out) {
    char *text = ps_cstr(n, s);
    const char *value = getenv(text);
    free(text);
    if (value == NULL) {
        ps_buf_copy(out, d, dn);
        return;
    }
    ps_buf_copy(out, value, (int64_t)strlen(value));
}

PsSeq *ps_os_listdir(int64_t n, const char *s) {
    char *text = ps_cstr(n, s);
    DIR *dir = opendir(text);
    struct dirent *ent;
    PsSeq *seq;
    free(text);
    if (dir == NULL) {
        ps_os_fail("listdir failed");
    }
    seq = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    while ((ent = readdir(dir)) != NULL) {
        PsBuf name;
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) {
            continue;
        }
        ps_buf_copy(&name, ent->d_name, (int64_t)strlen(ent->d_name));
        ps_seq_push(seq, &name);
    }
    closedir(dir);
    return seq;
}

void ps_os_stat(int64_t n, const char *s, PsStat *out) {
    struct stat st;
    if (ps_stat_into(n, s, &st) != 0) {
        ps_os_fail("stat failed");
    }
    out->mode = (int64_t)st.st_mode;
    out->size = (int64_t)st.st_size;
    out->mtime = (double)st.st_mtime;
}

int32_t ps_stat_isreg(int64_t mode) { return S_ISREG(mode) ? 1 : 0; }

int32_t ps_stat_isdir(int64_t mode) { return S_ISDIR(mode) ? 1 : 0; }

int32_t ps_stat_islnk(int64_t mode) { return S_ISLNK(mode) ? 1 : 0; }

/* expanduser: only the leading `~` and `~user` forms, from HOME or the
   passwd entry. This subset never raises RuntimeError for a missing user:
   an unknown `~user` keeps its text like CPython's KeyError-free fallback
   is not wanted, so it fails loudly instead of writing to a literal `~`
   directory. */
void ps_path_expanduser(int64_t n, const char *s, PsBuf *out) {
    if (n == 0 || s[0] != '~') {
        ps_buf_copy(out, s, n);
        return;
    }
    int64_t rest_at = 1;
    while (rest_at < n && s[rest_at] != '/') {
        rest_at += 1;
    }
    PsBuf home;
    if (rest_at == 1) {
        const char *env = getenv("HOME");
        if (env == NULL) {
            ps_os_fail("expanduser has no HOME");
        }
        ps_buf_copy(&home, env, (int64_t)strlen(env));
    } else {
        char *user = ps_cstr(rest_at - 1, s + 1);
        struct passwd *pw = getpwnam(user);
        free(user);
        if (pw == NULL || pw->pw_dir == NULL) {
            ps_os_fail("expanduser found no home for the user");
        }
        ps_buf_copy(&home, pw->pw_dir, (int64_t)strlen(pw->pw_dir));
    }
    PsGrow g;
    ps_grow_init(&g);
    ps_grow_add(&g, home.data, home.len);
    free(home.data);
    if (rest_at < n) {
        /* keep the user's separator shape: `~/x` keeps `/x`, `~` alone adds
           nothing, and a trailing slash stays. */
        ps_grow_add(&g, s + rest_at, n - rest_at);
    }
    ps_grow_finish(&g, out);
}

/* expandvars per posixpath: `$name`, `${name}` (unclosed left as-is), unset
   names left unchanged, and `$` not followed by a name left as-is. */
void ps_path_expandvars(int64_t n, const char *s, PsBuf *out) {
    PsGrow g;
    ps_grow_init(&g);
    int64_t i = 0;
    while (i < n) {
        int64_t start;
        int64_t end;
        int braced;
        char *name;
        const char *value;
        if (s[i] != '$' || i + 1 >= n) {
            ps_grow_byte(&g, s[i]);
            i += 1;
            continue;
        }
        start = i + 1;
        braced = s[start] == '{';
        if (braced) {
            start += 1;
            end = start;
            while (end < n && s[end] != '}') {
                end += 1;
            }
            if (end >= n) {
                /* `${name` without a close brace: keep the whole text. */
                ps_grow_add(&g, s + i, n - i);
                break;
            }
        } else {
            end = start;
            while (end < n && (isalnum((unsigned char)s[end]) || s[end] == '_')) {
                end += 1;
            }
            if (end == start) {
                ps_grow_byte(&g, '$');
                i += 1;
                continue;
            }
        }
        name = ps_cstr(end - start, s + start);
        value = getenv(name);
        free(name);
        if (value == NULL) {
            ps_grow_add(&g, s + i, (braced ? end + 1 : end) - i);
        } else {
            ps_grow_add(&g, value, (int64_t)strlen(value));
        }
        i = braced ? end + 1 : end;
    }
    ps_grow_finish(&g, out);
}

/* realpath: resolve symlinks step by step (no strict mode in this subset). */
void ps_path_realpath(int64_t n, const char *s, PsBuf *out) {
    PsBuf abs;
    char *text;
    char *resolved;
    ps_path_abs(n, s, &abs);
    text = ps_cstr(abs.len, abs.data);
    resolved = realpath(text, NULL);
    free(text);
    free(abs.data);
    if (resolved == NULL) {
        ps_os_fail("realpath failed");
    }
    ps_buf_copy(out, resolved, (int64_t)strlen(resolved));
    free(resolved);
}

/* getmtime/getsize/getatime over stat; each fails like os.stat does. */
double ps_path_getmtime(int64_t n, const char *s) {
    struct stat st;
    if (ps_stat_into(n, s, &st) != 0) {
        ps_os_fail("stat failed");
    }
    return (double)st.st_mtime;
}

int64_t ps_path_getsize(int64_t n, const char *s) {
    struct stat st;
    if (ps_stat_into(n, s, &st) != 0) {
        ps_os_fail("stat failed");
    }
    return (int64_t)st.st_size;
}

double ps_path_getatime(int64_t n, const char *s) {
    struct stat st;
    if (ps_stat_into(n, s, &st) != 0) {
        ps_os_fail("stat failed");
    }
    return (double)st.st_atime;
}

int32_t ps_path_islink(int64_t n, const char *s) {
    char *text = ps_cstr(n, s);
    struct stat st;
    int rc = lstat(text, &st);
    free(text);
    return rc == 0 && S_ISLNK(st.st_mode);
}

/* relpath(path, start=None): both made absolute and normalized first, then
   the parts of start that path does not share become `..` prefixes, per
   posixpath.relpath. */
typedef struct {
    const char *at;
    int64_t len;
} PsRelPart;

static int64_t ps_rel_parts(const char *s, int64_t n, PsRelPart *out) {
    int64_t count = 0;
    int64_t i = 0;
    while (i < n) {
        while (i < n && s[i] == '/') {
            i += 1;
        }
        int64_t start = i;
        while (i < n && s[i] != '/') {
            i += 1;
        }
        if (i > start) {
            out[count].at = s + start;
            out[count].len = i - start;
            count += 1;
        }
    }
    return count;
}

void ps_path_relpath(int64_t an, const char *a, int64_t bn, const char *b, PsBuf *out) {
    PsBuf abs_a;
    PsBuf abs_b;
    PsRelPart pa[64];
    PsRelPart pb[64];
    int64_t na;
    int64_t nb;
    int64_t common = 0;
    int64_t i;
    PsGrow g;
    ps_path_abs(an, a, &abs_a);
    if (bn == 0) {
        ps_os_getcwd(&abs_b);
        {
            PsBuf tmp;
            ps_path_abs(abs_b.len, abs_b.data, &tmp);
            free(abs_b.data);
            abs_b = tmp;
        }
    } else {
        ps_path_abs(bn, b, &abs_b);
    }
    na = ps_rel_parts(abs_a.data, abs_a.len, pa);
    nb = ps_rel_parts(abs_b.data, abs_b.len, pb);
    while (common < na && common < nb) {
        if (pa[common].len != pb[common].len) {
            break;
        }
        if (memcmp(pa[common].at, pb[common].at, (size_t)pa[common].len) != 0) {
            break;
        }
        common += 1;
    }
    ps_grow_init(&g);
    for (i = common; i < nb; i++) {
        if (g.len > 0) {
            ps_grow_byte(&g, '/');
        }
        ps_grow_add(&g, "..", 2);
    }
    for (i = common; i < na; i++) {
        if (g.len > 0) {
            ps_grow_byte(&g, '/');
        }
        ps_grow_add(&g, pa[i].at, pa[i].len);
    }
    free(abs_a.data);
    free(abs_b.data);
    if (g.len == 0) {
        ps_grow_byte(&g, '.');
    }
    ps_grow_finish(&g, out);
}

/* splitdrive on posix always has an empty drive. */
PsSeq *ps_path_splitdrive(int64_t n, const char *s) {
    PsSeq *seq = ps_seq_new((int64_t)sizeof(PsBuf), 2);
    PsBuf drive;
    PsBuf rest;
    ps_buf_copy(&drive, "", 0);
    ps_buf_copy(&rest, s, n);
    ps_seq_set(seq, 0, &drive);
    ps_seq_set(seq, 1, &rest);
    return seq;
}

int32_t ps_os_access(int64_t n, const char *s, int64_t mode) {
    char *text = ps_cstr(n, s);
    int rc = access(text, (int)mode);
    free(text);
    return rc == 0;
}

int64_t ps_os_getpid(void) { return (int64_t)getpid(); }

void ps_os_chdir(int64_t n, const char *s) {
    char *text = ps_cstr(n, s);
    int rc = chdir(text);
    free(text);
    if (rc != 0) {
        ps_os_fail("chdir failed");
    }
}

void ps_os_chmod(int64_t n, const char *s, int64_t mode) {
    char *text = ps_cstr(n, s);
    int rc = chmod(text, (mode_t)mode);
    free(text);
    if (rc != 0) {
        ps_os_fail("chmod failed");
    }
}

void ps_os_mkdir(int64_t n, const char *s) {
    char *text = ps_cstr(n, s);
    int rc = mkdir(text, 0755);
    free(text);
    if (rc != 0) {
        ps_os_fail("mkdir failed");
    }
}

void ps_os_replace(int64_t an, const char *a, int64_t bn, const char *b) {
    ps_os_rename(an, a, bn, b);
}

static int ps_match(const char *s, int64_t ns, int64_t si, const char *p, int64_t np, int64_t pi, int fuel) {
    if (fuel <= 0) {
        ps_raise_cstr(PS_RUNTIME, "fnmatch spent its fuel");
    }
    while (pi < np) {
        if (p[pi] == '*') {
            while (pi < np && p[pi] == '*') {
                pi += 1;
            }
            if (pi == np) {
                return 1;
            }
            for (; si <= ns; si++) {
                if (ps_match(s, ns, si, p, np, pi, fuel - 1)) {
                    return 1;
                }
            }
            return 0;
        }
        if (si >= ns) {
            return 0;
        }
        if (p[pi] == '?') {
            si += 1;
            pi += 1;
            continue;
        }
        if (p[pi] == '[') {
            int neg = 0;
            int hit = 0;
            pi += 1;
            if (pi < np && p[pi] == '!') {
                neg = 1;
                pi += 1;
            }
            if (pi < np && p[pi] == ']') {
                hit = s[si] == ']';
                pi += 1;
            }
            while (pi < np && p[pi] != ']') {
                char lo = p[pi];
                if (pi + 2 < np && p[pi + 1] == '-' && p[pi + 2] != ']') {
                    char hi = p[pi + 2];
                    if (s[si] >= lo && s[si] <= hi) {
                        hit = 1;
                    }
                    pi += 3;
                    continue;
                }
                if (s[si] == lo) {
                    hit = 1;
                }
                pi += 1;
            }
            if (pi >= np || p[pi] != ']') {
                return 0;
            }
            pi += 1;
            if (neg) {
                hit = !hit;
            }
            if (!hit) {
                return 0;
            }
            si += 1;
            continue;
        }
        if (s[si] != p[pi]) {
            return 0;
        }
        si += 1;
        pi += 1;
    }
    return si == ns;
}

int32_t ps_fnmatch(int64_t nn, const char *name, int64_t pn, const char *pat) {
    return ps_match(name, nn, 0, pat, pn, 0, 10000) ? 1 : 0;
}

PsSeq *ps_fnfilter(PsSeq *names, int64_t pn, const char *pat) {
    PsSeq *out = ps_seq_new(names->elem, 0);
    int64_t i;
    for (i = 0; i < names->len; i++) {
        PsBuf name;
        memcpy(&name, (char *)names->data + (size_t)i * (size_t)names->elem, sizeof name);
        if (ps_fnmatch(name.len, name.data, pn, pat)) {
            ps_seq_push(out, &name);
        }
    }
    return out;
}

void ps_path_new(int64_t n, const char *s, PsPath *out) { ps_buf_copy(&out->path, s, n); }

void ps_path_posix(PsPath *path, PsBuf *out) { ps_buf_copy(out, path->path.data, path->path.len); }

void ps_path_name(PsPath *path, PsBuf *out) { ps_path_base(path->path.len, path->path.data, out); }

void ps_path_suffix(PsPath *path, PsBuf *out) {
    PsSeq *parts = ps_path_splitext(path->path.len, path->path.data);
    PsBuf ext;
    ps_seq_get(parts, 1, &ext);
    ps_buf_copy(out, ext.data, ext.len);
}

void ps_path_stem(PsPath *path, PsBuf *out) {
    PsBuf name;
    PsSeq *parts;
    PsBuf root;
    PsBuf ext;
    int64_t slash;
    ps_path_base(path->path.len, path->path.data, &name);
    parts = ps_path_splitext(name.len, name.data);
    ps_seq_get(parts, 0, &root);
    ps_seq_get(parts, 1, &ext);
    (void)slash;
    ps_buf_copy(out, root.data, root.len);
}

void ps_path_parent(PsPath *path, PsPath *out) {
    PsBuf dir;
    ps_path_dir(path->path.len, path->path.data, &dir);
    if (dir.len == 0) {
        ps_buf_copy(&out->path, ".", 1);
        return;
    }
    out->path = dir;
}

int32_t ps_path_exists_p(PsPath *path) { return ps_path_exists(path->path.len, path->path.data); }

int32_t ps_path_isfile_p(PsPath *path) { return ps_path_isfile(path->path.len, path->path.data); }

int32_t ps_path_isdir_p(PsPath *path) { return ps_path_isdir(path->path.len, path->path.data); }

static void ps_read_file(int64_t n, const char *path, PsBuf *out) {
    char *text = ps_cstr(n, path);
    FILE *fp = fopen(text, "rb");
    long size;
    char *buf;
    free(text);
    if (fp == NULL) {
        ps_os_fail("read failed");
    }
    if (fseek(fp, 0, SEEK_END) != 0) {
        fclose(fp);
        ps_os_fail("read failed");
    }
    size = ftell(fp);
    if (size < 0) {
        fclose(fp);
        ps_os_fail("read failed");
    }
    if (fseek(fp, 0, SEEK_SET) != 0) {
        fclose(fp);
        ps_os_fail("read failed");
    }
    buf = malloc((size_t)size + 1);
    if (buf == NULL) {
        fclose(fp);
        abort();
    }
    if (size > 0 && fread(buf, 1, (size_t)size, fp) != (size_t)size) {
        free(buf);
        fclose(fp);
        ps_os_fail("read failed");
    }
    fclose(fp);
    buf[size] = '\0';
    out->len = (int64_t)size;
    out->data = buf;
}

static int64_t ps_write_file(int64_t n, const char *path, int64_t bn, const char *bytes) {
    char *text = ps_cstr(n, path);
    FILE *fp = fopen(text, "wb");
    free(text);
    if (fp == NULL) {
        ps_os_fail("write failed");
    }
    if (bn > 0 && fwrite(bytes, 1, (size_t)bn, fp) != (size_t)bn) {
        fclose(fp);
        ps_os_fail("write failed");
    }
    if (fclose(fp) != 0) {
        ps_os_fail("write failed");
    }
    return bn;
}

void ps_path_read_text(PsPath *path, PsBuf *out) { ps_read_file(path->path.len, path->path.data, out); }

void ps_path_read_bytes(PsPath *path, PsBuf *out) { ps_read_file(path->path.len, path->path.data, out); }

int64_t ps_path_write_text(PsPath *path, int64_t n, const char *s) {
    return ps_write_file(path->path.len, path->path.data, n, s);
}

int64_t ps_path_write_bytes(PsPath *path, int64_t n, const char *s) {
    return ps_write_file(path->path.len, path->path.data, n, s);
}

void ps_path_joinpath(PsPath *path, int64_t n, const char *s, PsPath *out) {
    PsBuf parts[2];
    PsBuf joined;
    parts[0] = path->path;
    parts[1].len = n;
    parts[1].data = (char *)s;
    ps_path_join(parts, 2, &joined);
    out->path = joined;
}

/* `p / other` for a Path right side, and `s / p` for a str left side. */

void ps_path_joinpath_p(PsPath *path, PsPath *other, PsPath *out) {
    PsBuf parts[2];
    parts[0] = path->path;
    parts[1] = other->path;
    ps_path_join(parts, 2, &out->path);
}

void ps_path_rjoin(int64_t n, const char *s, PsPath *path, PsPath *out) {
    PsBuf parts[2];
    parts[0].len = n;
    parts[0].data = (char *)s;
    parts[1] = path->path;
    ps_path_join(parts, 2, &out->path);
}

/* `p.with_name(s)` replaces the basename. CPython raises ValueError when the
   path has no name or `s` is not a plain name. */

void ps_path_with_name(PsPath *path, int64_t n, const char *s, PsPath *out) {
    PsBuf base;
    PsBuf dir;
    PsBuf parts[2];
    int64_t i;
    for (i = 0; i < n; i++) {
        if (s[i] == '/') {
            ps_raise_cstr(PS_VALUE, "Invalid name");
        }
    }
    if (n == 0) {
        ps_raise_cstr(PS_VALUE, "Invalid name");
    }
    ps_path_base(path->path.len, path->path.data, &base);
    if (base.len == 0) {
        ps_raise_cstr(PS_VALUE, "path has an empty name");
    }
    ps_path_dir(path->path.len, path->path.data, &dir);
    parts[0] = dir;
    parts[1].len = n;
    parts[1].data = (char *)s;
    ps_path_join(parts, 2, &out->path);
}

/* `p.mkdir(mode, parents, exist_ok)`: parents walks the path like makedirs;
   exist_ok only forgives an existing leaf that is itself a directory. */

void ps_path_mkdir(PsPath *path, int64_t mode, int64_t parents, int64_t exist_ok) {
    char *text = ps_cstr(path->path.len, path->path.data);
    int rc;
    if (parents) {
        int leaf_exists = 0;
        int64_t i;
        for (i = 1; i <= path->path.len; i++) {
            char save;
            if (i < path->path.len && text[i] != '/') {
                continue;
            }
            save = text[i];
            text[i] = '\0';
            rc = mkdir(text, (mode_t)mode);
            if (rc != 0) {
                if (errno == EEXIST && i == path->path.len) {
                    leaf_exists = 1;
                } else if (errno != EEXIST) {
                    free(text);
                    ps_os_fail("mkdir failed");
                }
            }
            text[i] = save;
        }
        free(text);
        if (leaf_exists && (!exist_ok || !ps_path_isdir(path->path.len, path->path.data))) {
            ps_os_fail("mkdir failed");
        }
        return;
    }
    rc = mkdir(text, (mode_t)mode);
    free(text);
    if (rc != 0) {
        if (exist_ok && errno == EEXIST && ps_path_isdir(path->path.len, path->path.data)) {
            return;
        }
        ps_os_fail("mkdir failed");
    }
}

/* `p.iterdir()` materializes the child paths; the caller wraps the list in
   the iterator record. */

/* A `list[Path]` stores pointers like every other class list. */
static void ps_path_emit(PsSeq *out, PsBuf *joined) {
    PsPath *hit = ps_alloc((int64_t)sizeof(PsPath));
    hit->path = *joined;
    ps_seq_push(out, &hit);
}

PsSeq *ps_path_iterdir(PsPath *path) {
    PsSeq *names = ps_os_listdir(path->path.len, path->path.data);
    PsSeq *out = ps_seq_new((int64_t)sizeof(PsPath *), 0);
    int64_t i;
    for (i = 0; i < names->len; i++) {
        PsBuf name;
        PsPath child;
        memcpy(&name, (char *)names->data + (size_t)i * (size_t)names->elem, sizeof name);
        ps_path_joinpath(path, name.len, name.data, &child);
        ps_path_emit(out, &child.path);
    }
    return out;
}

/* `p.glob(pat)` descends segment by segment. `**` is the only recursive
   segment and matches directories; every other segment is an fnmatch on the
   names readdir yields, like CPython's selector over scandir. */

static int ps_glob_star(const char *seg, int64_t ns) { return ns == 2 && seg[0] == '*' && seg[1] == '*'; }

static void ps_glob_pat(PsBuf *base, const char *pat, int64_t np, PsSeq *out);

static void ps_glob_seg(PsBuf *base, const char *seg, int64_t ns, const char *rest, int64_t nrest, PsSeq *out) {
    int deep = ps_glob_star(seg, ns);
    char *text;
    DIR *dir;
    struct dirent *ent;
    if (deep) {
        if (rest != NULL) {
            /* `**` matching zero directories is `rest` from here. */
            ps_glob_pat(base, rest, nrest, out);
        } else {
            /* A trailing `**` yields the directory itself, then every
               descendant directory. */
            ps_path_emit(out, base);
        }
    }
    text = ps_cstr(base->len, base->data);
    dir = opendir(text);
    free(text);
    if (dir == NULL) {
        return;
    }
    while ((ent = readdir(dir)) != NULL) {
        const char *name = ent->d_name;
        PsBuf child;
        PsBuf parts[2];
        if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0) {
            continue;
        }
        parts[0] = *base;
        parts[1].len = (int64_t)strlen(name);
        parts[1].data = (char *)name;
        if (deep) {
            ps_path_join(parts, 2, &child);
            if (ps_path_isdir(child.len, child.data)) {
                if (rest == NULL) {
                    ps_path_emit(out, &child);
                }
                ps_glob_seg(&child, seg, ns, rest, nrest, out);
            }
            continue;
        }
        if (!ps_fnmatch(parts[1].len, parts[1].data, ns, seg)) {
            continue;
        }
        if (rest == NULL) {
            ps_path_join(parts, 2, &child);
            ps_path_emit(out, &child);
            continue;
        }
        ps_path_join(parts, 2, &child);
        if (ps_path_isdir(child.len, child.data)) {
            ps_glob_pat(&child, rest, nrest, out);
        }
    }
    closedir(dir);
}

static void ps_glob_pat(PsBuf *base, const char *pat, int64_t np, PsSeq *out) {
    int64_t slash = 0;
    while (slash < np && pat[slash] != '/') {
        slash += 1;
    }
    /* PurePath parsing drops empty and '.' segments. */
    if (slash == 0 || (slash == 1 && pat[0] == '.')) {
        if (slash < np) {
            ps_glob_pat(base, pat + slash + 1, np - slash - 1, out);
        }
        return;
    }
    ps_glob_seg(base, pat, slash, slash < np ? pat + slash + 1 : NULL, slash < np ? np - slash - 1 : 0, out);
}

PsSeq *ps_path_glob(PsPath *path, int64_t np, const char *pat) {
    int64_t at;
    PsSeq *out;
    while (np > 0 && pat[np - 1] == '/') {
        np -= 1;
    }
    if (np == 0) {
        ps_raise_cstr(PS_VALUE, "Unacceptable pattern");
    }
    if (pat[0] == '/') {
        ps_raise_cstr(PS_VALUE, "Non-relative patterns are unsupported");
    }
    for (at = 0; at < np;) {
        int64_t ns = 0;
        int64_t i;
        int deep = 0;
        while (at + ns < np && pat[at + ns] != '/') {
            ns += 1;
        }
        for (i = 0; i + 1 < ns; i++) {
            if (pat[at + i] == '*' && pat[at + i + 1] == '*') {
                deep = 1;
            }
        }
        if (deep && !ps_glob_star(pat + at, ns)) {
            ps_raise_cstr(PS_VALUE, "Invalid pattern: '**' can only be an entire path component");
        }
        at += ns + 1;
    }
    out = ps_seq_new((int64_t)sizeof(PsPath *), 0);
    ps_glob_pat(&path->path, pat, np, out);
    return out;
}
