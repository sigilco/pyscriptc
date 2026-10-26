#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif

/* importlib.resources directory lookup, tempfile, and shutil.

Resource roots are directories on the compile-time sys.path. Zip archives
are not searched. as_file of one of these paths is the path itself.
*/

#include "ps_rt.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <utime.h>

#if !defined(_WIN32)
#include <sys/utsname.h>
#endif

#ifdef __APPLE__
#include <copyfile.h>
#endif

#ifdef __linux__
#include <sys/sendfile.h>
#endif

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
    wrote =
#ifdef __wasm__
        /* WASI getpid is a deprecated stub; the name just needs to be unique
           per attempt, which rand() alone provides. */
        snprintf(file, sizeof file, "%s/.ps-temp-%d-%d", dir, 0, rand());
#else
        snprintf(file, sizeof file, "%s/.ps-temp-%d-%d", dir, (int)getpid(), rand());
#endif
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
/* A second thread can reach gettempdir. The one-time fill runs once. */
static pthread_mutex_t ps_temp_lock = PTHREAD_MUTEX_INITIALIZER;

/* CPython runs every tempdir candidate through os.path.abspath before it
tests it, and abspath normalizes: a trailing slash goes away and a relative
path is joined to the cwd. macOS launchd sets TMPDIR with a trailing slash
(".../T/"), so returning the raw value would not match CPython's gettempdir.
ps_path_abs is the same normalization the posixpath surface uses. */
static void ps_temp_abspath(const char *dir, char *out, size_t cap) {
    PsBuf abs;
    ps_path_abs((int64_t)strlen(dir), dir, &abs);
    if (abs.len + 1 > cap) {
        free(abs.data);
        ps_os_fail("tempdir");
    }
    memcpy(out, abs.data, (size_t)abs.len);
    out[abs.len] = '\0';
    free(abs.data);
}

static void ps_copy_tempdir(char *out, size_t cap) {
    const char *env_names[] = {"TMPDIR", "TEMP", "TMP"};
    const char *fallback[] = {"/tmp", "/var/tmp", "/usr/tmp"};
    char cwd[4096];
    size_t i;
    pthread_mutex_lock(&ps_temp_lock);
    if (ps_temp_ready) {
        if (strlen(ps_temp_cached) + 1 > cap) {
            pthread_mutex_unlock(&ps_temp_lock);
            ps_os_fail("tempdir");
        }
        memcpy(out, ps_temp_cached, strlen(ps_temp_cached) + 1);
        pthread_mutex_unlock(&ps_temp_lock);
        return;
    }
    for (i = 0; i < 3; i++) {
        const char *env = getenv(env_names[i]);
        if (env != NULL && env[0] != '\0' && ps_writable_dir(env)) {
            ps_temp_abspath(env, out, cap);
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
        ps_temp_abspath(cwd, out, cap);
        goto cache;
    }
    pthread_mutex_unlock(&ps_temp_lock);
    ps_os_fail("tempdir");
cache:
    if (strlen(out) + 1 < sizeof ps_temp_cached) {
        memcpy(ps_temp_cached, out, strlen(out) + 1);
        ps_temp_ready = 1;
    }
    pthread_mutex_unlock(&ps_temp_lock);
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
    int infd = fileno(in);
    int outfd = fileno(out);
    struct stat st;

    if (infd >= 0 && outfd >= 0 && fstat(infd, &st) == 0 && S_ISREG(st.st_mode)) {
        if (st.st_size == 0) {
            return;
        }

#ifdef __APPLE__
        if (fcopyfile(infd, outfd, NULL, COPYFILE_DATA) == 0) {
            return;
        }
        if (errno != EINVAL && errno != ENOTSUP) {
            ps_os_fail("copyfile");
        }
        rewind(in);
        rewind(out);
        (void)ftruncate(outfd, 0);
#elif defined(__linux__) && !defined(__ANDROID__)
        int cfr_failed = 0;
        off_t total_copied = 0;
        while (1) {
            ssize_t n = copy_file_range(infd, NULL, outfd, NULL, (size_t)1024 * 1024 * 128, 0);
            if (n == 0) {
                if (total_copied == 0) {
                    cfr_failed = 1;
                    break;
                }
                return;
            }
            if (n < 0) {
                if (total_copied == 0 && (errno == ENOSYS || errno == EXDEV || errno == EINVAL || errno == EOPNOTSUPP)) {
                    cfr_failed = 1;
                    break;
                }
                ps_os_fail("copyfile");
            }
            total_copied += n;
        }

        if (cfr_failed) {
            rewind(in);
            rewind(out);
            lseek(infd, 0, SEEK_SET);
            lseek(outfd, 0, SEEK_SET);
            (void)ftruncate(outfd, 0);

            total_copied = 0;
            int sendfile_failed = 0;
            while (1) {
                ssize_t n = sendfile(outfd, infd, NULL, (size_t)1024 * 1024 * 128);
                if (n == 0) {
                    return;
                }
                if (n < 0) {
                    if (total_copied == 0 && (errno == EINVAL || errno == ENOSYS || errno == ENOTSOCK || errno == EOPNOTSUPP)) {
                        sendfile_failed = 1;
                        break;
                    }
                    ps_os_fail("copyfile");
                }
                total_copied += n;
            }

            if (sendfile_failed) {
                rewind(in);
                rewind(out);
                lseek(infd, 0, SEEK_SET);
                lseek(outfd, 0, SEEK_SET);
                (void)ftruncate(outfd, 0);
            }
        }
#endif
    }

    char stack_buf[4096];
    size_t buf_size = 256 * 1024;
    char *buf = (char *)malloc(buf_size);
    if (buf == NULL) {
        buf = stack_buf;
        buf_size = sizeof(stack_buf);
    }
    size_t n;
    while ((n = fread(buf, 1, buf_size, in)) > 0) {
        if (fwrite(buf, 1, n, out) != n) {
            if (buf != stack_buf) {
                free(buf);
            }
            ps_os_fail("copyfile");
        }
    }
    if (ferror(in)) {
        if (buf != stack_buf) {
            free(buf);
        }
        ps_os_fail("copyfile");
    }
    if (buf != stack_buf) {
        free(buf);
    }
}

void ps_shutil_copyfile(int64_t sn, const char *src, int64_t dn, const char *dst, PsBuf *out) {
    char *source = ps_cstr(sn, src);
    char *dest = ps_cstr(dn, dst);
    FILE *in;
    FILE *outf;
    struct stat st_src, st_dst;
    int same = (strcmp(source, dest) == 0);
    if (!same && stat(source, &st_src) == 0 && stat(dest, &st_dst) == 0) {
        if (st_src.st_dev == st_dst.st_dev && st_src.st_ino == st_dst.st_ino) {
            same = 1;
        }
    }
    if (same) {
        free(source);
        free(dest);
        ps_raise_cstr(PS_RUNTIME, "copyfile source and destination are the same file");
    }
    if (stat(source, &st_src) == 0 && S_ISDIR(st_src.st_mode)) {
        free(source);
        free(dest);
        ps_os_fail("copyfile");
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
/* The slot table is process-wide, so a second thread can reach it. One
   mutex covers the table and the slot values: the read and the write of a
   slot are separate calls, so a program that counts under its own Lock is
   the one that has to be correct (the SC-DRF contract). The mutex here
   keeps the table itself coherent and the value read atomic. */
static pthread_mutex_t ps_global_lock = PTHREAD_MUTEX_INITIALIZER;

static PsGlobal *ps_global_slot(int64_t n, const char *name, int create) {
    int i;
    PsGlobal *slot;
    pthread_mutex_lock(&ps_global_lock);
    for (i = 0; i < ps_global_n; i++) {
        if (ps_globals[i].name_n == n && memcmp(ps_globals[i].name, name, (size_t)n) == 0) {
            pthread_mutex_unlock(&ps_global_lock);
            return &ps_globals[i];
        }
    }
    if (!create) {
        pthread_mutex_unlock(&ps_global_lock);
        return NULL;
    }
    if (ps_global_n >= 64) {
        pthread_mutex_unlock(&ps_global_lock);
        ps_raise_cstr(PS_RUNTIME, "too many global slots");
    }
    slot = &ps_globals[ps_global_n++];
    slot->name = malloc((size_t)n + 1);
    if (slot->name == NULL) {
        pthread_mutex_unlock(&ps_global_lock);
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
    pthread_mutex_unlock(&ps_global_lock);
    return slot;
}

int32_t ps_global_is_set(int64_t n, const char *name) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    return slot != NULL && slot->set ? 1 : 0;
}

void ps_global_set_str(int64_t nn, const char *name, int64_t vn, const char *val) {
    PsGlobal *slot = ps_global_slot(nn, name, 1);
    pthread_mutex_lock(&ps_global_lock);
    ps_buf_copy(&slot->buf, val, vn);
    slot->is_int = 0;
    slot->is_ptr = 0;
    slot->set = 1;
    pthread_mutex_unlock(&ps_global_lock);
}

void ps_global_get_str(int64_t n, const char *name, PsBuf *out) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    if (slot == NULL) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    pthread_mutex_lock(&ps_global_lock);
    if (!slot->set || slot->is_int || slot->is_ptr) {
        pthread_mutex_unlock(&ps_global_lock);
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    ps_buf_copy(out, slot->buf.data == NULL ? "" : slot->buf.data, slot->buf.len);
    pthread_mutex_unlock(&ps_global_lock);
}

void ps_global_set_i64(int64_t n, const char *name, int64_t value) {
    PsGlobal *slot = ps_global_slot(n, name, 1);
    pthread_mutex_lock(&ps_global_lock);
    slot->ival = value;
    slot->is_int = 1;
    slot->is_ptr = 0;
    slot->set = 1;
    pthread_mutex_unlock(&ps_global_lock);
}

int64_t ps_global_get_i64(int64_t n, const char *name) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    if (slot == NULL) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    pthread_mutex_lock(&ps_global_lock);
    int64_t value = slot->ival;
    int ok = slot->set && slot->is_int && !slot->is_ptr;
    pthread_mutex_unlock(&ps_global_lock);
    if (!ok) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    return value;
}

void ps_global_set_ptr(int64_t n, const char *name, void *value) {
    PsGlobal *slot = ps_global_slot(n, name, 1);
    pthread_mutex_lock(&ps_global_lock);
    slot->pval = value;
    slot->is_int = 0;
    slot->is_ptr = 1;
    slot->set = 1;
    pthread_mutex_unlock(&ps_global_lock);
}

void *ps_global_get_ptr(int64_t n, const char *name) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    if (slot == NULL) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    pthread_mutex_lock(&ps_global_lock);
    void *value = slot->pval;
    int ok = slot->set && slot->is_ptr;
    pthread_mutex_unlock(&ps_global_lock);
    if (!ok) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    return value;
}

void ps_global_set_path(int64_t n, const char *name, PsPath *path) {
    PsGlobal *slot = ps_global_slot(n, name, 1);
    pthread_mutex_lock(&ps_global_lock);
    ps_buf_copy(&slot->buf, path->path.data == NULL ? "" : path->path.data, path->path.len);
    slot->is_int = 0;
    slot->is_ptr = 0;
    slot->set = 1;
    pthread_mutex_unlock(&ps_global_lock);
}

void ps_global_get_path(int64_t n, const char *name, PsPath *out) {
    PsGlobal *slot = ps_global_slot(n, name, 0);
    if (slot == NULL) {
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    pthread_mutex_lock(&ps_global_lock);
    if (!slot->set || slot->is_int || slot->is_ptr) {
        pthread_mutex_unlock(&ps_global_lock);
        ps_raise_cstr(PS_RUNTIME, "global is unset");
    }
    ps_buf_copy(&out->path, slot->buf.data == NULL ? "" : slot->buf.data, slot->buf.len);
    pthread_mutex_unlock(&ps_global_lock);
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
    /* wasi-libc declares no mkstemp (TODO(zig-0.18): still true upstream),
       so the portable ps_mkstemp opens with O_CREAT|O_EXCL on a randomized
       suffix: the same unique-file guarantee, and mode 0600 matches musl's
       mkstemp. */
    fd = ps_mkstemp(path);
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

/* platform helpers */
void ps_platform_system(PsBuf *out) {
#if defined(_WIN32)
    ps_buf_copy(out, "Windows", 7);
#else
    struct utsname u;
    if (uname(&u) == 0) {
        ps_buf_copy(out, u.sysname, (int64_t)strlen(u.sysname));
    } else {
        ps_buf_copy(out, "", 0);
    }
#endif
}

void ps_platform_machine(PsBuf *out) {
#if defined(_WIN32)
    ps_buf_copy(out, "AMD64", 5);
#else
    struct utsname u;
    if (uname(&u) == 0) {
        ps_buf_copy(out, u.machine, (int64_t)strlen(u.machine));
    } else {
        ps_buf_copy(out, "", 0);
    }
#endif
}

void ps_platform_python_version(PsBuf *out) {
    ps_buf_copy(out, "3.14.9", 6);
}

/* platform.platform() per the CPython 3.14 format.

   The string is built the way platform._platform does: the parts are
   joined with '-', the empty parts are dropped, the characters that are
   awkward in a file name are folded to '_', "unknown" is dropped, runs of
   '-' are folded to one and a trailing '-' is removed.

   The parts differ per system, exactly as in CPython:

   - Linux: system, release, machine, processor, "with", libc name+version.
     The libc part is what CPython's platform.libc_ver() reports; the
     glibc version comes from the runtime's own libc, so it is read from
     the host and never hardcoded.
   - macOS: "macOS", the product version and the machine.
   - Windows: "Windows", the release and the version.
   - anything else: system, release, machine, processor, the pointer size
     in bits and the linkage format.

   The processor is empty on the systems where CPython cannot tell it, and
   an empty part is dropped from the join. */
static void ps_platform_join(PsGrow *g, const char *part) {
    if (part == NULL || part[0] == '\0') {
        return;
    }
    if (g->len > 0) {
        ps_grow_byte(g, '-');
    }
    ps_grow_add(g, part, (int64_t)strlen(part));
}

/* The character folding platform._platform applies: a space and the
   punctuation that is awkward in a file name become '_'. */
static void ps_platform_fold(PsGrow *g) {
    int64_t i;
    for (i = 0; i < g->len; i++) {
        char c = g->data[i];
        if (c == ' ' || c == '/' || c == '\\' || c == ':' || c == ';' || c == '"' || c == '(' || c == ')') {
            g->data[i] = '_';
        }
    }
}

/* "unknown" is dropped by CPython, and so is every run of '-' it leaves
   behind; a trailing '-' is removed too. */
static void ps_platform_cleanup(PsGrow *g) {
    char *w = g->data;
    int64_t r = 0;
    int64_t i;
    for (i = 0; i < g->len; i++) {
        if (w[i] == 'u' && g->len - i >= 7 && memcmp(w + i, "unknown", 7) == 0) {
            i += 6;
            continue;
        }
        if (w[i] == '-' && r > 0 && w[r - 1] == '-') {
            continue;
        }
        w[r] = w[i];
        r += 1;
    }
    while (r > 0 && w[r - 1] == '-') {
        r -= 1;
    }
    g->len = r;
    if (g->data != NULL) {
        g->data[r] = '\0';
    }
}

/* The libc name and version CPython's platform.libc_ver() reports: the
   glibc version is read from the running libc through confstr, and the
   musl libc is read from the version banner of the loader. */
#if defined(__linux__)
static void ps_platform_libc(char *name, size_t name_len, char *version, size_t version_len) {
    name[0] = '\0';
    version[0] = '\0';
#if defined(_CS_GNU_LIBC_VERSION)
    {
        char buf[128];
        size_t len = confstr(_CS_GNU_LIBC_VERSION, buf, sizeof buf);
        if (len > 0 && len <= sizeof buf) {
            char *space = strchr(buf, ' ');
            if (space != NULL) {
                *space = '\0';
                snprintf(name, name_len, "%s", buf);
                snprintf(version, version_len, "%s", space + 1);
                return;
            }
        }
    }
#endif
#if defined(__GLIBC__)
    snprintf(name, name_len, "glibc");
    snprintf(version, version_len, "%d.%d", __GLIBC__, __GLIBC_MINOR__);
#else
    snprintf(name, name_len, "libc");
#endif
}
#elif defined(__APPLE__)
static void ps_platform_libc(char *name, size_t name_len, char *version, size_t version_len) {
    /* macOS has no libc version CPython reports: platform.libc_ver()
       returns an empty name and version there. */
    name[0] = '\0';
    version[0] = '\0';
}
#else
static void ps_platform_libc(char *name, size_t name_len, char *version, size_t version_len) {
    name[0] = '\0';
    version[0] = '\0';
}
#endif

#if defined(__APPLE__)
/* The macOS product version, the way CPython's platform.mac_ver() reads it:
   the product version of the running system, with the build number
   dropped. */
static void ps_platform_macos_release(char *out, size_t len) {
    struct utsname u;
    out[0] = '\0';
    if (uname(&u) == 0 && strncmp(u.release, "Darwin ", 7) == 0) {
        const char *rest = u.release + 7;
        size_t i = 0;
        while (rest[i] != '\0' && rest[i] != '.' && i + 1 < len) {
            out[i] = rest[i];
            i += 1;
        }
        out[i] = '\0';
    }
}
#endif

void ps_platform_platform(PsBuf *out) {
    PsGrow g;
    char libc_name[64];
    char libc_version[64];
    char part[128];
    ps_grow_init(&g);
    ps_platform_libc(libc_name, sizeof libc_name, libc_version, sizeof libc_version);
#if defined(_WIN32)
    ps_platform_join(&g, "Windows");
    ps_platform_join(&g, NULL);
    ps_platform_join(&g, NULL);
#elif defined(__APPLE__)
    {
        struct utsname u;
        ps_platform_join(&g, "macOS");
        ps_platform_macos_release(part, sizeof part);
        ps_platform_join(&g, part);
        if (uname(&u) == 0) {
            ps_platform_join(&g, u.machine);
        }
    }
#else
    {
        struct utsname u;
        if (uname(&u) == 0) {
            ps_platform_join(&g, u.sysname);
            ps_platform_join(&g, u.release);
            ps_platform_join(&g, u.machine);
            /* CPython's uname fills the processor from `uname -p` and drops
               it when it equals the machine; the struct has no processor
               field, so the subprocess shape CPython queries cannot be
               read here and the processor stays empty, exactly like a
               host whose `uname -p` answers "unknown". */
            if (strcmp(u.sysname, "Linux") == 0) {
                snprintf(part, sizeof part, "with");
                ps_platform_join(&g, part);
                snprintf(part, sizeof part, "%s%s", libc_name, libc_version);
                ps_platform_join(&g, part);
            } else {
                /* The generic branch reports the pointer size and the
                   linkage format, which is what the ELF headers say. */
                snprintf(part, sizeof part, "%dbit", (int)(sizeof(void *) * 8));
                ps_platform_join(&g, part);
                ps_platform_join(&g, "ELF");
            }
        }
    }
#endif
    ps_platform_fold(&g);
    ps_platform_cleanup(&g);
    ps_grow_finish(&g, out);
}

/* zipfile helpers */
int32_t ps_zipfile_is_zipfile(int64_t n, const char *s) {
    char *path = ps_cstr(n, s);
    FILE *f = fopen(path, "rb");
    free(path);
    if (f == NULL) {
        return 0;
    }
    unsigned char buf[4];
    size_t read_bytes = fread(buf, 1, 4, f);
    fclose(f);
    if (read_bytes < 4) {
        return 0;
    }
    return (buf[0] == 'P' && buf[1] == 'K' && (
        (buf[2] == 3 && buf[3] == 4) ||
        (buf[2] == 5 && buf[3] == 6) ||
        (buf[2] == 7 && buf[3] == 8)
    )) ? 1 : 0;
}

/* tarfile helpers */
int32_t ps_tarfile_is_tarfile(int64_t n, const char *s) {
    char *path = ps_cstr(n, s);
    FILE *f = fopen(path, "rb");
    free(path);
    if (f == NULL) {
        return 0;
    }
    unsigned char buf[512];
    size_t read_bytes = fread(buf, 1, 512, f);
    fclose(f);
    if (read_bytes < 512) {
        return 0;
    }
    return (memcmp(buf + 257, "ustar", 5) == 0) ? 1 : 0;
}
