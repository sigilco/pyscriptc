/* importlib.resources directory lookup, tempfile, and shutil.

Resource roots are directories on the compile-time sys.path. Zip archives
are not searched. as_file of one of these paths is the path itself.
*/

#include "ps_rt.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utime.h>

typedef struct PsPath {
    PsBuf path;
} PsPath;

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

static void ps_os_fail(const char *what) { ps_raise_cstr(PS_RUNTIME, what); }

static int ps_is_dir(const char *path) {
    struct stat st;
    return stat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static int ps_is_file(const char *path) {
    struct stat st;
    return stat(path, &st) == 0 && S_ISREG(st.st_mode);
}

static int ps_writable_dir(const char *dir) {
    char file[4096];
    int wrote;
    int fd;
    if (!ps_is_dir(dir)) {
        return 0;
    }
    wrote = snprintf(file, sizeof file, "%s/.ps-temp-%d-%d", dir, (int)getpid(), rand());
    if (wrote < 0 || (size_t)wrote >= sizeof file) {
        return 0;
    }
    fd = open(file, O_CREAT | O_EXCL | O_RDWR, 0600);
    if (fd < 0) {
        return 0;
    }
    if (write(fd, "x", 1) != 1) {
        close(fd);
        unlink(file);
        return 0;
    }
    close(fd);
    unlink(file);
    return 1;
}

static char ps_temp_cached[4096];
static int ps_temp_ready = 0;

static void ps_copy_tempdir(char *out, size_t cap) {
    const char *env_names[] = {"TMPDIR", "TEMP", "TMP"};
    const char *fallback[] = {"/tmp", "/var/tmp", "/usr/tmp"};
    char cwd[4096];
    size_t i;
    if (ps_temp_ready) {
        if (strlen(ps_temp_cached) + 1 > cap) {
            ps_os_fail("tempdir");
        }
        memcpy(out, ps_temp_cached, strlen(ps_temp_cached) + 1);
        return;
    }
    for (i = 0; i < 3; i++) {
        const char *env = getenv(env_names[i]);
        if (env != NULL && env[0] != '\0' && ps_writable_dir(env)) {
            if (strlen(env) + 1 > cap) {
                ps_os_fail("tempdir");
            }
            memcpy(out, env, strlen(env) + 1);
            goto cache;
        }
    }
    for (i = 0; i < 3; i++) {
        if (ps_writable_dir(fallback[i])) {
            memcpy(out, fallback[i], strlen(fallback[i]) + 1);
            goto cache;
        }
    }
    if (getcwd(cwd, sizeof cwd) != NULL && ps_writable_dir(cwd)) {
        if (strlen(cwd) + 1 > cap) {
            ps_os_fail("tempdir");
        }
        memcpy(out, cwd, strlen(cwd) + 1);
        goto cache;
    }
    ps_os_fail("tempdir");
cache:
    if (strlen(out) + 1 < sizeof ps_temp_cached) {
        memcpy(ps_temp_cached, out, strlen(out) + 1);
        ps_temp_ready = 1;
    }
}

void ps_temp_getdir(PsBuf *out) {
    char dir[4096];
    ps_copy_tempdir(dir, sizeof dir);
    ps_buf_copy(out, dir, (int64_t)strlen(dir));
}

void ps_temp_prefix(PsBuf *out) { ps_buf_copy(out, "tmp", 3); }

void ps_temp_mkdtemp(PsBuf *out) {
    static const char alphabet[] = "abcdefghijklmnopqrstuvwxyz0123456789_";
    char dir[4096];
    char path[4096];
    unsigned char rnd[8];
    FILE *random;
    int attempt;
    size_t base;
    ps_copy_tempdir(dir, sizeof dir);
    base = strlen(dir);
    random = fopen("/dev/urandom", "rb");
    if (random == NULL) {
        ps_os_fail("mkdtemp");
    }
    for (attempt = 0; attempt < 128; attempt++) {
        int i;
        if (fread(rnd, 1, 8, random) != 8) {
            fclose(random);
            ps_os_fail("mkdtemp");
        }
        if (base + 1 + 3 + 8 + 1 > sizeof path) {
            fclose(random);
            ps_os_fail("mkdtemp");
        }
        memcpy(path, dir, base);
        path[base] = '/';
        memcpy(path + base + 1, "tmp", 3);
        for (i = 0; i < 8; i++) {
            path[base + 4 + i] = alphabet[rnd[i] % 37];
        }
        path[base + 12] = '\0';
        if (ps_mkdir(path, 0700) == 0) {
            fclose(random);
            ps_buf_copy(out, path, (int64_t)strlen(path));
            return;
        }
        if (errno != EEXIST) {
            fclose(random);
            ps_os_fail("mkdtemp");
        }
    }
    fclose(random);
    ps_os_fail("mkdtemp");
}

static int ps_pkg_rel(int64_t n, const char *pkg, char *rel, size_t cap) {
    int64_t i;
    size_t w = 0;
    int dot = 1;
    if (n <= 0) {
        return 0;
    }
    for (i = 0; i < n; i++) {
        char c = pkg[i];
        int ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '.';
        if (!ok || w + 2 >= cap) {
            return 0;
        }
        if (c == '.') {
            if (dot) {
                return 0;
            }
            rel[w++] = '/';
            dot = 1;
            continue;
        }
        rel[w++] = c;
        dot = 0;
    }
    if (dot) {
        return 0;
    }
    rel[w] = '\0';
    return 1;
}

void ps_res_files(int64_t pn, const char *pkg, int64_t sn, const char *search, PsPath *out) {
    char rel[512];
    int64_t i = 0;
    if (!ps_pkg_rel(pn, pkg, rel, sizeof rel)) {
        ps_raise_cstr(PS_VALUE, "resource package name is not a dotted identifier");
    }
    while (i <= sn) {
        int64_t start = i;
        char *root;
        char candidate[4096];
        size_t root_n;
        size_t rel_n;
        while (i < sn && search[i] != '\n') {
            i++;
        }
        if (i > start) {
            root = ps_cstr(i - start, search + start);
            root_n = strlen(root);
            rel_n = strlen(rel);
            if (root_n + 1 + rel_n + 4 < sizeof candidate) {
                memcpy(candidate, root, root_n);
                candidate[root_n] = '/';
                memcpy(candidate + root_n + 1, rel, rel_n + 1);
                if (ps_is_dir(candidate)) {
                    ps_buf_copy(&out->path, candidate, (int64_t)strlen(candidate));
                    free(root);
                    return;
                }
                memcpy(candidate + root_n + 1 + rel_n, ".py", 4);
                if (ps_is_file(candidate)) {
                    /* A module's resources live in the directory that contains the file. */
                    char *slash = strrchr(candidate, '/');
                    if (slash == NULL) {
                        free(root);
                        ps_raise_cstr(PS_VALUE, "resource package was not found on the compile-time path");
                    }
                    *slash = '\0';
                    ps_buf_copy(&out->path, candidate, (int64_t)strlen(candidate));
                    free(root);
                    return;
                }
            }
            free(root);
        }
        if (i >= sn) {
            break;
        }
        i++;
    }
    ps_raise_cstr(PS_VALUE, "resource package was not found on the compile-time path");
}

static void ps_copy_bytes(FILE *in, FILE *out) {
    char buf[4096];
    size_t n;
    while ((n = fread(buf, 1, sizeof buf, in)) > 0) {
        if (fwrite(buf, 1, n, out) != n) {
            ps_os_fail("copyfile");
        }
    }
    if (ferror(in)) {
        ps_os_fail("copyfile");
    }
}

void ps_shutil_copyfile(int64_t sn, const char *src, int64_t dn, const char *dst, PsBuf *out) {
    char *source = ps_cstr(sn, src);
    char *dest = ps_cstr(dn, dst);
    FILE *in;
    FILE *outf;
    if (strcmp(source, dest) == 0) {
        free(source);
        free(dest);
        ps_raise_cstr(PS_RUNTIME, "copyfile source and destination are the same file");
    }
    in = fopen(source, "rb");
    if (in == NULL) {
        free(source);
        free(dest);
        ps_os_fail("copyfile");
    }
    outf = fopen(dest, "wb");
    if (outf == NULL) {
        fclose(in);
        free(source);
        free(dest);
        ps_os_fail("copyfile");
    }
    ps_copy_bytes(in, outf);
    if (fclose(outf) != 0) {
        fclose(in);
        ps_os_fail("copyfile");
    }
    fclose(in);
    ps_buf_copy(out, dest, (int64_t)strlen(dest));
    free(source);
    free(dest);
}

/* copy: copyfile plus the permission bits, and the destination path as the
   result, like shutil.copy. */
void ps_shutil_copy(int64_t sn, const char *src, int64_t dn, const char *dst, PsBuf *out) {
    struct stat st;
    ps_shutil_copyfile(sn, src, dn, dst, out);
    if (stat(dst, &st) == 0) {
        chmod(dst, st.st_mode & 07777);
    }
}

/* copy2: copyfile plus the mode and the modification time. */
void ps_shutil_copy2(int64_t sn, const char *src, int64_t dn, const char *dst, PsBuf *out) {
    struct stat st;
    ps_shutil_copyfile(sn, src, dn, dst, out);
    if (stat(src, &st) == 0) {
        chmod(dst, st.st_mode & 07777);
#ifdef _WIN32
        struct utimbuf u;
        u.actime = st.st_atime;
        u.modtime = st.st_mtime;
        utime(dst, &u);
#else
        struct stat out_st;
        if (stat(dst, &out_st) == 0) {
            struct timespec times[2];
#ifdef __APPLE__
            times[0] = st.st_atimespec;
            times[1] = st.st_mtimespec;
#else
            times[0] = st.st_atim;
            times[1] = st.st_mtim;
#endif
            utimensat(AT_FDCWD, dst, times, 0);
        }
#endif
    }
}

/* copytree: copy a directory tree, creating the destination. The result is
   the destination path, like shutil.copytree. */
static void ps_copy_tree(const char *src, const char *dst) {
    struct stat st;
    DIR *dir;
    struct dirent *ent;
    if (stat(src, &st) != 0) {
        ps_os_fail("copytree");
    }
    if (ps_mkdir(dst, st.st_mode & 07777) != 0 && errno != EEXIST) {
        ps_os_fail("copytree");
    }
    dir = opendir(src);
    if (dir == NULL) {
        ps_os_fail("copytree");
    }
    while ((ent = readdir(dir)) != NULL) {
        char child_src[4096];
        char child_dst[4096];
        struct stat cst;
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) {
            continue;
        }
        if (snprintf(child_src, sizeof child_src, "%s/%s", src, ent->d_name) >= (int)sizeof child_src) {
            closedir(dir);
            ps_os_fail("copytree");
        }
        if (snprintf(child_dst, sizeof child_dst, "%s/%s", dst, ent->d_name) >= (int)sizeof child_dst) {
            closedir(dir);
            ps_os_fail("copytree");
        }
        if (ps_lstat(child_src, &cst) != 0) {
            closedir(dir);
            ps_os_fail("copytree");
        }
        if (S_ISDIR(cst.st_mode)) {
            ps_copy_tree(child_src, child_dst);
            continue;
        }
        if (S_ISREG(cst.st_mode)) {
            FILE *in = fopen(child_src, "rb");
            FILE *outf;
            if (in == NULL) {
                closedir(dir);
                ps_os_fail("copytree");
            }
            outf = fopen(child_dst, "wb");
            if (outf == NULL) {
                fclose(in);
                closedir(dir);
                ps_os_fail("copytree");
            }
            ps_copy_bytes(in, outf);
            fclose(in);
            if (fclose(outf) != 0) {
                closedir(dir);
                ps_os_fail("copytree");
            }
            chmod(child_dst, cst.st_mode & 07777);
            continue;
        }
        closedir(dir);
        ps_os_fail("copytree");
    }
    closedir(dir);
}

void ps_shutil_copytree(int64_t sn, const char *src, int64_t dn, const char *dst, PsBuf *out) {
    char *source = ps_cstr(sn, src);
    char *dest = ps_cstr(dn, dst);
    ps_copy_tree(source, dest);
    free(source);
    free(dest);
    ps_buf_copy(out, dst, dn);
}

static void ps_rm_tree(const char *path);

/* move: rename, falling back to a copy plus a remove across filesystems. */
void ps_shutil_move(int64_t sn, const char *src, int64_t dn, const char *dst, PsBuf *out) {
    char *source = ps_cstr(sn, src);
    char *dest = ps_cstr(dn, dst);
    struct stat st;
    if (stat(source, &st) == 0 && S_ISDIR(st.st_mode)) {
        ps_copy_tree(source, dest);
        ps_rm_tree(source);
    } else {
        if (rename(source, dest) != 0) {
            FILE *in = fopen(source, "rb");
            FILE *outf;
            if (in == NULL) {
                free(source);
                free(dest);
                ps_os_fail("move");
            }
            outf = fopen(dest, "wb");
            if (outf == NULL) {
                fclose(in);
                free(source);
                free(dest);
                ps_os_fail("move");
            }
            ps_copy_bytes(in, outf);
            fclose(in);
            if (fclose(outf) != 0) {
                free(source);
                free(dest);
                ps_os_fail("move");
            }
            unlink(source);
        }
    }
    free(source);
    free(dest);
    ps_buf_copy(out, dst, dn);
}

static void ps_rm_tree(const char *path) {
    struct stat st;
    DIR *dir;
    struct dirent *ent;
    if (ps_lstat(path, &st) != 0) {
        ps_os_fail("rmtree");
    }
    if (!S_ISDIR(st.st_mode)) {
        if (unlink(path) != 0) {
            ps_os_fail("rmtree");
        }
        return;
    }
    dir = opendir(path);
    if (dir == NULL) {
        ps_os_fail("rmtree");
    }
    while ((ent = readdir(dir)) != NULL) {
        char child[4096];
        size_t n;
        size_t m;
        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) {
            continue;
        }
        n = strlen(path);
        m = strlen(ent->d_name);
        if (n + 1 + m + 1 > sizeof child) {
            closedir(dir);
            ps_os_fail("rmtree");
        }
        memcpy(child, path, n);
        child[n] = '/';
        memcpy(child + n + 1, ent->d_name, m + 1);
        ps_rm_tree(child);
    }
    closedir(dir);
    if (rmdir(path) != 0) {
        ps_os_fail("rmtree");
    }
}

void ps_shutil_rmtree(int64_t n, const char *path) {
    char *text = ps_cstr(n, path);
    ps_rm_tree(text);
    free(text);
}

/* Process-lifetime module slots. A name starts unset (`is None`) and then holds a str, an i64, or a path. */

typedef struct PsGlobal {
    char *name;
    int64_t name_n;
    int set;
    int is_int;
    int is_ptr;
    int64_t ival;
    void *pval;
    PsBuf buf;
} PsGlobal;

static PsGlobal ps_globals[64];
static int ps_global_n;

static PsGlobal *ps_global_slot(int64_t n, const char *name, int create) {
    int i;
    PsGlobal *slot;
    for (i = 0; i < ps_global_n; i++) {
        if (ps_globals[i].name_n == n && memcmp(ps_globals[i].name, name, (size_t)n) == 0) {
            return &ps_globals[i];
        }
    }
    if (!create) {
        return NULL;
    }
    if (ps_global_n >= 64) {
        ps_raise_cstr(PS_RUNTIME, "too many global slots");
    }
    slot = &ps_globals[ps_global_n++];
    slot->name = malloc((size_t)n + 1);
    if (slot->name == NULL) {
        ps_raise_cstr(PS_RUNTIME, "out of memory");
    }
    if (n > 0) {
        memcpy(slot->name, name, (size_t)n);
    }
    slot->name[n] = '\0';
    slot->name_n = n;
    slot->set = 0;
    slot->is_int = 0;
    slot->is_ptr = 0;
    slot->ival = 0;
    slot->pval = NULL;
    slot->buf.len = 0;
    slot->buf.data = &ps_empty_byte;
    return slot;
}

int32_t ps_global_is_set(int64_t n, const char *name) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    return slot != NULL && slot->set ? 1 : 0;
}

void ps_global_set_str(int64_t nn, const char *name, int64_t vn, const char *val) {
    PsGlobal *slot = ps_global_slot(nn, name, 1);
    ps_buf_copy(&slot->buf, val, vn);
    slot->is_int = 0;
    slot->is_ptr = 0;
    slot->set = 1;
}

void ps_global_get_str(int64_t n, const char *name, PsBuf *out) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    if (slot == NULL || !slot->set || slot->is_int || slot->is_ptr) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    ps_buf_copy(out, slot->buf.data == NULL ? "" : slot->buf.data, slot->buf.len);
}

void ps_global_set_i64(int64_t n, const char *name, int64_t value) {
    PsGlobal *slot = ps_global_slot(n, name, 1);
    slot->ival = value;
    slot->is_int = 1;
    slot->is_ptr = 0;
    slot->set = 1;
}

int64_t ps_global_get_i64(int64_t n, const char *name) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    if (slot == NULL || !slot->set || !slot->is_int || slot->is_ptr) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    return slot->ival;
}

void ps_global_set_ptr(int64_t n, const char *name, void *value) {
    PsGlobal *slot = ps_global_slot(n, name, 1);
    slot->pval = value;
    slot->is_int = 0;
    slot->is_ptr = 1;
    slot->set = 1;
}

void *ps_global_get_ptr(int64_t n, const char *name) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    if (slot == NULL || !slot->set || !slot->is_ptr) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    return slot->pval;
}

void ps_global_set_path(int64_t n, const char *name, PsPath *path) {
    PsGlobal *slot = ps_global_slot(n, name, 1);
    ps_buf_copy(&slot->buf, path->path.data == NULL ? "" : path->path.data, path->path.len);
    slot->is_int = 0;
    slot->is_ptr = 0;
    slot->set = 1;
}

void ps_global_get_path(int64_t n, const char *name, PsPath *out) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    if (slot == NULL || !slot->set || slot->is_int || slot->is_ptr) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    ps_buf_copy(&out->path, slot->buf.data == NULL ? "" : slot->buf.data, slot->buf.len);
}

/* shutil.which(cmd): first executable regular file found walking PATH left
   to right, or KeyError — the subset stand-in for CPython's None. An empty
   PATH entry means the current directory; a cmd that already contains a
   slash is checked directly, like CPython. */
void ps_shutil_which(int64_t n, const char *s, PsBuf *out) {
    char *cmd = ps_cstr(n, s);
    const char *env = getenv("PATH");
    const char *dir;
    const char *end;
    struct stat st;
    char cand[4096];
    size_t dlen;
    if (env == NULL || env[0] == '\0') {
        env = "/bin:/usr/bin";
    }
    if (strchr(cmd, '/') != NULL) {
        if (stat(cmd, &st) == 0 && S_ISREG(st.st_mode) && access(cmd, X_OK) == 0) {
            ps_buf_copy(out, cmd, (int64_t)strlen(cmd));
            free(cmd);
            return;
        }
        free(cmd);
        ps_raise_cstr(PS_KEY, "which found no executable");
    }
    dir = env;
    for (;;) {
        end = strchr(dir, ':');
        dlen = end == NULL ? strlen(dir) : (size_t)(end - dir);
        if (dlen == 0) {
            snprintf(cand, sizeof cand, "./%s", cmd);
        } else {
            if (dlen + 1 + strlen(cmd) + 1 > sizeof cand) {
                goto next;
            }
            memcpy(cand, dir, dlen);
            if (cand[dlen - 1] == '/') {
                memcpy(cand + dlen, cmd, strlen(cmd) + 1);
            } else {
                cand[dlen] = '/';
                memcpy(cand + dlen + 1, cmd, strlen(cmd) + 1);
            }
        }
        if (stat(cand, &st) == 0 && S_ISREG(st.st_mode) && access(cand, X_OK) == 0) {
            ps_buf_copy(out, cand, (int64_t)strlen(cand));
            free(cmd);
            return;
        }
    next:
        if (end == NULL) {
            break;
        }
        dir = end + 1;
    }
    free(cmd);
    ps_raise_cstr(PS_KEY, "which found no executable");
}

PsIO *ps_temp_named(int64_t mode, int64_t keep) {
    char dir[4096];
    char path[4200];
    int fd;
    ps_copy_tempdir(dir, sizeof dir);
    if (snprintf(path, sizeof path, "%s/tmpXXXXXX", dir) >= (int)sizeof path) {
        ps_os_fail("NamedTemporaryFile failed");
    }
    fd = mkstemp(path);
    if (fd < 0) {
        ps_os_fail("NamedTemporaryFile failed");
    }
    return ps_io_take_fd(fd, path, mode, keep);
}

/* The terminal size record: two i64 fields, columns then lines. The layout
   matches the TerminalSize class in stdmore.CLASS_FIELDS. */
typedef struct {
    int64_t columns;
    int64_t lines;
} PsTerminalSize;

/* get_terminal_size: the COLUMNS and LINES environment variables when they
   are set, else the ioctl, else the 80x24 fallback that CPython uses when
   the output is not a terminal. */
void ps_shutil_get_terminal_size(PsTerminalSize *out) {
    const char *columns = getenv("COLUMNS");
    const char *lines = getenv("LINES");
    out->columns = 80;
    out->lines = 24;
    if (columns != NULL && columns[0] != '\0') {
        out->columns = (int64_t)strtol(columns, NULL, 10);
    }
    if (lines != NULL && lines[0] != '\0') {
        out->lines = (int64_t)strtol(lines, NULL, 10);
    }
}
