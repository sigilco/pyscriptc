/* posixpath, os, stat predicates, fnmatch, and pathlib.Path.

Path objects are `{ PsBuf _path }`. OS errors raise RuntimeError. An unset
getenv raises KeyError, which is the subset stand-in for None.
*/

#include "ps_rt.h"

#include <dirent.h>
#include <errno.h>
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
