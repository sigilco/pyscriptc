/* tarfile and zipfile: the read surface an installer needs.

The archive handle is an opaque runtime pointer: a malloc'd record with the
member list (the name, the offset, the size and the compression of each
member) plus the path the archive was opened from. The compiler lowers
`tarfile.open(path, mode)` and `zipfile.ZipFile(path)` onto the open
helpers, the member calls onto the getnames/read/extractall helpers and the
context-manager form onto the close helper.

Two compressors are read: gzip (through zlib) for the `r:gz` tar mode and
the deflate method of a zip member. The bzip2 and lzma tar modes are
accepted at compile time and raise at run time, because the runtime links
no bzip2 or lzma library (the divergence is recorded in the docs).

The tar reader walks the 512-byte blocks of a POSIX ustar archive (the GNU
long-name and long-link extensions ride the same walk), and the zip reader
walks the central directory: the end-of-central-directory record names the
directory offset, and every central-directory entry names the local header
offset, the compression method and the uncompressed size.

Writing is refused at compile time, so nothing here creates an archive.

Linking: zlib rides the link line of a program whose emitted IR reaches one
of the archive symbols, the way libcurl rides an https:// program (the
driver adds the flag and the define). Without the define the gzip and
deflate paths raise at run time and the stored (uncompressed) members still
read, so a program that never touches a compressed member links without
zlib. */

#include "ps_rt.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "ps_compat.h"

#if !defined(__wasi__) && !defined(_WIN32) && defined(PSC_HAVE_ZLIB)
#include <zlib.h>
#endif

/* One member of the archive: the name as the archive spells it, the offset
   of its data in the file, the uncompressed size and the compression. */
typedef struct PsArchiveMember {
    char *name;
    int64_t offset;
    int64_t size;
    int64_t stored_size;
    int32_t method; /* 0 stored, 8 deflate, 9 deflate64 */
    int32_t kind; /* 0 file, 1 directory */
} PsArchiveMember;

/* The archive handle the compiler's pointer value carries. */
typedef struct PsArchive {
    PsArchiveMember *members;
    int64_t count;
    int64_t cap;
    char *path;
    /* The archive body the member offsets index: the file bytes for a plain
       tar and a zip, the gunzipped bytes for a `r:gz` tar (the member
       offsets run over the tar blocks, not the gzip stream). */
    unsigned char *body;
    int64_t body_size;
    int32_t is_zip;
} PsArchive;

static void ps_archive_fail(const char *what) { ps_raise_cstr(PS_RUNTIME, what); }

/* The str/bytes carrier the compiler passes: a length and the bytes. */
static char *ps_archive_cstr(int64_t n, const char *s) {
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

static void ps_archive_push(PsArchive *archive, const char *name, int64_t offset, int64_t size,
                            int64_t stored_size, int32_t method, int32_t kind) {
    PsArchiveMember *member;
    if (archive->count == archive->cap) {
        int64_t cap = archive->cap == 0 ? 16 : archive->cap * 2;
        PsArchiveMember *grown = realloc(archive->members, (size_t)cap * sizeof(PsArchiveMember));
        if (grown == NULL) {
            abort();
        }
        archive->members = grown;
        archive->cap = cap;
    }
    member = &archive->members[archive->count];
    member->name = malloc(strlen(name) + 1);
    if (member->name == NULL) {
        abort();
    }
    memcpy(member->name, name, strlen(name) + 1);
    member->offset = offset;
    member->size = size;
    member->stored_size = stored_size;
    member->method = method;
    member->kind = kind;
    archive->count = archive->count + 1;
}

static PsArchive *ps_archive_new(const char *path, int32_t is_zip) {
    PsArchive *archive = calloc(1, sizeof(PsArchive));
    if (archive == NULL) {
        abort();
    }
    archive->path = malloc(strlen(path) + 1);
    if (archive->path == NULL) {
        abort();
    }
    memcpy(archive->path, path, strlen(path) + 1);
    archive->is_zip = is_zip;
    return archive;
}

/* The plain body the member offsets index: kept on the handle, because a
   `r:gz` tar's offsets run over the gunzipped blocks, not the file. */
static void ps_archive_hold(PsArchive *archive, unsigned char *body, int64_t body_size) {
    archive->body = body;
    archive->body_size = body_size;
}

/* The whole file as bytes: the members are read out of it. */
static unsigned char *ps_archive_slurp(const char *path, int64_t *size_out) {
    FILE *file = fopen(path, "rb");
    unsigned char *data = NULL;
    long size = 0;
    if (file == NULL) {
        ps_archive_fail("the archive file could not be opened");
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        ps_archive_fail("the archive file could not be read");
    }
    size = ftell(file);
    if (size < 0) {
        fclose(file);
        ps_archive_fail("the archive file could not be read");
    }
    if (fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        ps_archive_fail("the archive file could not be read");
    }
    data = malloc((size_t)size + 1);
    if (data == NULL) {
        abort();
    }
    if (size > 0 && fread(data, 1, (size_t)size, file) != (size_t)size) {
        free(data);
        fclose(file);
        ps_archive_fail("the archive file could not be read");
    }
    fclose(file);
    *size_out = (int64_t)size;
    return data;
}

/* ------------------------------------------------------------------ gzip */

/* The gzip stream of a `r:gz` tar: the tar blocks ride a deflate stream
   with a gzip header. Without zlib the mode raises. */
#if !defined(__wasi__) && !defined(_WIN32) && defined(PSC_HAVE_ZLIB)
static unsigned char *ps_archive_gunzip(const unsigned char *data, int64_t size, int64_t *out_size) {
    z_stream stream;
    unsigned char *out;
    int64_t cap = size * 4 + 1024;
    int64_t total = 0;
    if (cap < 1024) {
        cap = 1024;
    }
    out = malloc((size_t)cap);
    if (out == NULL) {
        abort();
    }
    memset(&stream, 0, sizeof(stream));
    if (inflateInit2(&stream, 16 + MAX_WBITS) != Z_OK) {
        free(out);
        ps_archive_fail("the gzip stream could not be opened");
    }
    stream.next_in = (Bytef *)data;
    stream.avail_in = (uInt)size;
    for (;;) {
        int rc;
        if (total == cap) {
            unsigned char *grown = realloc(out, (size_t)(cap * 2));
            if (grown == NULL) {
                abort();
            }
            out = grown;
            cap = cap * 2;
        }
        stream.next_out = out + total;
        stream.avail_out = (uInt)(cap - total);
        rc = inflate(&stream, Z_NO_FLUSH);
        total = cap - stream.avail_out;
        if (rc == Z_STREAM_END) {
            break;
        }
        if (rc != Z_OK) {
            inflateEnd(&stream);
            free(out);
            ps_archive_fail("the gzip stream could not be read");
        }
        if (stream.avail_in == 0 && stream.avail_out != 0) {
            inflateEnd(&stream);
            free(out);
            ps_archive_fail("the gzip stream could not be read");
        }
    }
    inflateEnd(&stream);
    *out_size = total;
    return out;
}

/* The deflate stream of a zip member: the raw deflate data (no zlib
   header), inflated into the member's size. */
static void ps_archive_inflate(const unsigned char *data, int64_t size, int64_t out_size,
                                unsigned char *out) {
    z_stream stream;
    memset(&stream, 0, sizeof(stream));
    if (inflateInit2(&stream, -MAX_WBITS) != Z_OK) {
        ps_archive_fail("the deflate stream could not be opened");
    }
    stream.next_in = (Bytef *)data;
    stream.avail_in = (uInt)size;
    stream.next_out = out;
    stream.avail_out = (uInt)out_size;
    if (inflate(&stream, Z_FINISH) != Z_STREAM_END) {
        inflateEnd(&stream);
        ps_archive_fail("the deflate stream could not be read");
    }
    inflateEnd(&stream);
}
#else
static unsigned char *ps_archive_gunzip(const unsigned char *data, int64_t size, int64_t *out_size) {
    (void)data;
    (void)size;
    (void)out_size;
    ps_archive_fail("the gzip mode needs zlib, which this build does not link");
    return NULL;
}

static void ps_archive_inflate(const unsigned char *data, int64_t size, int64_t out_size,
                                unsigned char *out) {
    (void)data;
    (void)size;
    (void)out_size;
    (void)out;
    ps_archive_fail("the deflate method needs zlib, which this build does not link");
}
#endif

/* ------------------------------------------------------------------- tar */

/* The octal field of a tar header: the number is ASCII octal, NUL- or
   space-padded. */
static int64_t ps_tar_octal(const unsigned char *field, int64_t size) {
    int64_t value = 0;
    int64_t index;
    int64_t start = 0;
    int64_t end = size;
    if (size > 0 && (field[0] & 0x80) != 0) {
        /* The GNU base-256 form: the high bit marks a binary number. */
        int64_t carry = 0;
        for (index = 1; index < size; index++) {
            carry = (carry << 8) | field[index];
        }
        return carry;
    }
    while (start < end && (field[start] == ' ' || field[start] == '\0')) {
        start++;
    }
    for (index = start; index < end; index++) {
        if (field[index] == ' ' || field[index] == '\0') {
            break;
        }
        if (field[index] < '0' || field[index] > '7') {
            break;
        }
        value = value * 8 + (field[index] - '0');
    }
    return value;
}

/* The name field of a tar header: the prefix and the name join with a
   slash, the way the ustar format spells a long path. A directory name
   loses its trailing slash, the way CPython's tarfile reads it. */
static void ps_tar_name(const unsigned char *header, char *out, int64_t size) {
    int64_t prefix_len = 0;
    int64_t name_len = 0;
    int64_t total;
    while (prefix_len < 155 && header[345 + prefix_len] != '\0') {
        prefix_len++;
    }
    while (name_len < 100 && header[name_len] != '\0') {
        name_len++;
    }
    if (prefix_len > 0) {
        memcpy(out, header + 345, (size_t)prefix_len);
        out[prefix_len] = '/';
        memcpy(out + prefix_len + 1, header, (size_t)name_len);
        total = prefix_len + 1 + name_len;
        out[total] = '\0';
    } else {
        memcpy(out, header, (size_t)name_len);
        total = name_len;
        out[total] = '\0';
    }
    if (total > 0 && out[total - 1] == '/') {
        out[total - 1] = '\0';
    }
}

/* The tar reader, the archive-growing form: the handle owns the members. */
static void ps_tar_read_into(PsArchive *archive, const unsigned char *data, int64_t size) {
    int64_t offset = 0;
    char pending[512];
    int64_t pending_len = 0;
    pending[0] = '\0';
    while (offset + 512 <= size) {
        const unsigned char *header = data + offset;
        char name[512];
        int64_t member_size;
        int32_t kind;
        int all_zero = 1;
        int64_t index;
        for (index = 0; index < 512; index++) {
            if (header[index] != 0) {
                all_zero = 0;
                break;
            }
        }
        if (all_zero) {
            break;
        }
        offset = offset + 512;
        ps_tar_name(header, name, 512);
        member_size = ps_tar_octal(header + 124, 12);
        /* 0 or '0' a regular file, '5' a directory, 'x' a pax extended
           header, 'L' the GNU long name, 'K' the GNU long link. */
        if (header[156] == '5') {
            kind = 1;
        } else if (header[156] == 'x') {
            kind = 2;
        } else {
            kind = 0;
        }
        if (pending_len > 0) {
            memcpy(name, pending, (size_t)pending_len);
            name[pending_len] = '\0';
            pending_len = 0;
        }
        if (name[0] == 'L' && name[1] == '\0') {
            int64_t take = member_size;
            if (take > size - offset) {
                take = size - offset;
            }
            if (take > 511) {
                take = 511;
            }
            memcpy(pending, data + offset, (size_t)take);
            pending_len = take;
            offset = offset + ((member_size + 511) / 512) * 512;
            continue;
        }
        if (name[0] == 'K' && name[1] == '\0') {
            offset = offset + ((member_size + 511) / 512) * 512;
            continue;
        }
        if (kind == 2) {
            /* The pax extended header ('x'): CPython's tarfile hides it and
               takes the metadata from its records, so the header block is
               skipped and the member it describes is kept under its own
               name. The mtime record the header carries is not read: the
               extraction writes with the process umask. */
            offset = offset + ((member_size + 511) / 512) * 512;
            continue;
        }
        ps_archive_push(archive, name, offset, member_size, member_size, 0, kind);
        offset = offset + ((member_size + 511) / 512) * 512;
    }
}

/* ------------------------------------------------------------------- zip */

/* The little-endian 16- and 32-bit fields of a zip record. */
static int64_t ps_zip_u16(const unsigned char *data, int64_t offset) {
    return (int64_t)data[offset] | ((int64_t)data[offset + 1] << 8);
}

static int64_t ps_zip_u32(const unsigned char *data, int64_t offset) {
    return (int64_t)data[offset] | ((int64_t)data[offset + 1] << 8) | ((int64_t)data[offset + 2] << 16) |
           ((int64_t)data[offset + 3] << 24);
}

/* The zip reader: the end-of-central-directory record names the central
   directory, and every central-directory entry names the local header of
   its member. */
static void ps_zip_read_into(PsArchive *archive, const unsigned char *data, int64_t size) {
    int64_t eocd = -1;
    int64_t scan;
    int64_t dir_offset;
    int64_t dir_size;
    int64_t count;
    int64_t index;
    int64_t entry;
    for (scan = size - 22; scan >= 0; scan--) {
        if (ps_zip_u32(data, scan) == 0x06054b50) {
            eocd = scan;
            break;
        }
        if (scan < size - 22 - 65536) {
            break;
        }
    }
    if (eocd < 0) {
        ps_archive_fail("the zip archive has no central directory");
    }
    count = ps_zip_u16(data, eocd + 10);
    dir_size = ps_zip_u32(data, eocd + 12);
    dir_offset = ps_zip_u32(data, eocd + 16);
    if (dir_offset < 0 || dir_offset > size || dir_size < 0 || dir_offset + dir_size > size) {
        ps_archive_fail("the zip archive has no central directory");
    }
    index = 0;
    entry = dir_offset;
    while (index < count) {
        int64_t name_len;
        int64_t extra_len;
        int64_t comment_len;
        int64_t local_offset;
        int64_t method;
        int64_t compressed;
        int64_t uncompressed;
        char name[1024];
        if (entry + 46 > size) {
            break;
        }
        if (ps_zip_u32(data, entry) != 0x02014b50) {
            break;
        }
        method = ps_zip_u16(data, entry + 10);
        compressed = ps_zip_u32(data, entry + 20);
        uncompressed = ps_zip_u32(data, entry + 24);
        name_len = ps_zip_u16(data, entry + 28);
        extra_len = ps_zip_u16(data, entry + 30);
        comment_len = ps_zip_u16(data, entry + 32);
        local_offset = ps_zip_u32(data, entry + 42);
        if (name_len >= (int64_t)sizeof(name)) {
            name_len = (int64_t)sizeof(name) - 1;
        }
        memcpy(name, data + entry + 46, (size_t)name_len);
        name[name_len] = '\0';
        {
            /* The local header repeats the name and the extra field, and
               their sizes can differ from the central directory's. */
            int64_t local_name = 0;
            int64_t local_extra = 0;
            int64_t data_offset = local_offset;
            if (local_offset + 30 <= size && ps_zip_u32(data, local_offset) == 0x04034b50) {
                local_name = ps_zip_u16(data, local_offset + 26);
                local_extra = ps_zip_u16(data, local_offset + 28);
                data_offset = local_offset + 30 + local_name + local_extra;
            }
            if (data_offset + compressed > size) {
                ps_archive_fail("the zip archive member is out of range");
            }
            ps_archive_push(archive, name, data_offset, uncompressed, compressed, (int32_t)method,
                            name_len > 0 && name[name_len - 1] == '/' ? 1 : 0);
        }
        entry = entry + 46 + name_len + extra_len + comment_len;
        index++;
    }
}

/* ------------------------------------------------------------------ open */

/* The tar modes the subset takes: the plain read and the three compressed
   reads. The write modes never reach the runtime (the compiler fences
   them), so the runtime refuses them too. */
static int32_t ps_tar_mode(const char *mode) {
    if (strcmp(mode, "r") == 0 || strcmp(mode, "r:") == 0) {
        return 0;
    }
    if (strcmp(mode, "r:gz") == 0) {
        return 1;
    }
    if (strcmp(mode, "r:bz2") == 0) {
        return 2;
    }
    if (strcmp(mode, "r:xz") == 0) {
        return 3;
    }
    return -1;
}

void *ps_tarfile_open(int64_t pn, const char *path, int64_t mn, const char *mode) {
    char *text = ps_archive_cstr(pn, path);
    char *mode_text = ps_archive_cstr(mn, mode);
    int32_t kind = ps_tar_mode(mode_text);
    unsigned char *data;
    int64_t size = 0;
    PsArchive *archive;
    free(mode_text);
    if (kind < 0) {
        free(text);
        ps_archive_fail("the tar mode is not a read mode");
    }
    if (kind == 2 || kind == 3) {
        free(text);
        ps_archive_fail("the bzip2 and lzma tar modes are not lowered");
    }
    data = ps_archive_slurp(text, &size);
    archive = ps_archive_new(text, 0);
    if (kind == 1) {
        int64_t plain_size = 0;
        unsigned char *plain = ps_archive_gunzip(data, size, &plain_size);
        free(data);
        data = plain;
        size = plain_size;
    }
    ps_tar_read_into(archive, data, size);
    ps_archive_hold(archive, data, size);
    free(text);
    return archive;
}

void *ps_zipfile_open(int64_t pn, const char *path) {
    char *text = ps_archive_cstr(pn, path);
    unsigned char *data;
    int64_t size = 0;
    PsArchive *archive;
    data = ps_archive_slurp(text, &size);
    archive = ps_archive_new(text, 1);
    ps_zip_read_into(archive, data, size);
    ps_archive_hold(archive, data, size);
    free(text);
    return archive;
}

/* The members: the file names, in the order the archive lists them. */
PsSeq *ps_archive_names(void *handle) {
    PsArchive *archive = (PsArchive *)handle;
    PsSeq *seq = ps_seq_new((int64_t)sizeof(PsBuf), 0);
    int64_t index;
    for (index = 0; index < archive->count; index++) {
        PsBuf name;
        ps_buf_copy(&name, archive->members[index].name, (int64_t)strlen(archive->members[index].name));
        ps_seq_push(seq, &name);
    }
    return seq;
}

/* One member's bytes into the out buffer: the stored bytes read as they
   are, the deflated ones inflated. Shared by read and extractfile. */
static void ps_archive_member_bytes(PsArchive *archive, PsArchiveMember *member, PsBuf *out) {
    const unsigned char *data = archive->body;
    if (member->offset + member->stored_size > archive->body_size) {
        ps_archive_fail("the archive member is out of range");
    }
    if (member->method == 0) {
        ps_buf_copy(out, (const char *)(data + member->offset), member->stored_size);
        return;
    }
    if (member->method == 8) {
        unsigned char *plain = malloc((size_t)(member->size > 0 ? member->size : 1));
        if (plain == NULL) {
            abort();
        }
        ps_archive_inflate(data + member->offset, member->stored_size, member->size, plain);
        ps_buf_copy(out, (const char *)plain, member->size);
        free(plain);
        return;
    }
    ps_archive_fail("the archive member uses a compression the subset does not read");
}

/* The archive member's file object, CPython's `extractfile(name)`:
   `ps_archive_extractfile_hit` answers whether a file member with that
   name exists (a directory and a missing name are both a miss, the shapes
   CPython answers None for), and `ps_archive_extractfile_make` hands back
   the BytesIO record seeded with the member's bytes. The two-call shape
   mirrors `os.getenv` and `re.search`: the tagged `BytesIO | None` box is
   filled only when the probe hit. */
int32_t ps_archive_extractfile_hit(void *handle, int64_t nn, const char *name) {
    PsArchive *archive = (PsArchive *)handle;
    char *wanted = ps_archive_cstr(nn, name);
    int64_t index;
    int32_t hit = 0;
    for (index = 0; index < archive->count; index++) {
        PsArchiveMember *member = &archive->members[index];
        if (strcmp(member->name, wanted) != 0) {
            continue;
        }
        free(wanted);
        if (member->kind == 0) {
            return 1;
        }
        /* A directory member: CPython answers None, the miss the probe
           reports. */
        return 0;
    }
    free(wanted);
    /* A name no member carries: CPython raises KeyError, so the probe
       raises before the box is built. */
    ps_raise_cstr(PS_KEY, "filename not found in the archive");
    return 0;
}

PsIO *ps_archive_extractfile_make(void *handle, int64_t nn, const char *name) {
    PsArchive *archive = (PsArchive *)handle;
    char *wanted = ps_archive_cstr(nn, name);
    int64_t index;
    for (index = 0; index < archive->count; index++) {
        PsArchiveMember *member = &archive->members[index];
        if (strcmp(member->name, wanted) != 0) {
            continue;
        }
        free(wanted);
        if (member->kind == 1) {
            ps_archive_fail("the archive member is a directory");
        }
        {
            PsBuf bytes;
            PsIO *io;
            ps_archive_member_bytes(archive, member, &bytes);
            io = ps_io_bytes(bytes.len, bytes.data == NULL ? "" : bytes.data);
            free(bytes.data);
            return io;
        }
    }
    free(wanted);
    ps_archive_fail("the archive has no member with that name");
    return NULL;
}

/* The member's bytes: the stored bytes read as they are, the deflated ones
   inflated. */
void ps_archive_read(void *handle, int64_t nn, const char *name, PsBuf *out) {
    PsArchive *archive = (PsArchive *)handle;
    char *wanted = ps_archive_cstr(nn, name);
    int64_t index;
    for (index = 0; index < archive->count; index++) {
        PsArchiveMember *member = &archive->members[index];
        if (strcmp(member->name, wanted) != 0) {
            continue;
        }
        free(wanted);
        if (member->kind == 1) {
            ps_archive_fail("the archive member is a directory");
        }
        ps_archive_member_bytes(archive, member, out);
        return;
    }
    free(wanted);
    ps_archive_fail("the archive has no member with that name");
}

/* The extraction target: a member path under the destination directory.
   An absolute path and a path that walks up are refused, the way
   CPython's tarfile refuses them since 3.12 (the "path traversal" guard),
   so an archive cannot write outside the destination. */
static char *ps_archive_target(const char *dest, const char *name) {
    int64_t dest_len = (int64_t)strlen(dest);
    char *target;
    int64_t index;
    int64_t start = 0;
    if (name[0] == '/') {
        ps_archive_fail("the archive member has an absolute path");
    }
    for (index = 0; name[index] != '\0'; index++) {
        if (name[index] == '/' && index > 0 && name[index - 1] == '.') {
            int64_t back = index - 1;
            int64_t dots = 0;
            while (back >= 0 && name[back] == '.') {
                back--;
                dots++;
            }
            if (dots == 2 && (back < 0 || name[back] == '/')) {
                ps_archive_fail("the archive member walks up out of the destination");
            }
        }
    }
    if (dest_len > 0 && dest[dest_len - 1] == '/') {
        start = dest_len;
    } else {
        start = dest_len + 1;
    }
    target = malloc((size_t)(start + strlen(name)) + 1);
    if (target == NULL) {
        abort();
    }
    memcpy(target, dest, (size_t)dest_len);
    if (start == dest_len + 1) {
        target[dest_len] = '/';
    }
    memcpy(target + start, name, strlen(name) + 1);
    return target;
}

/* The directories of the target path: every prefix of the member name, so
   a member inside a directory creates the directory first. */
static void ps_archive_make_dirs(const char *path) {
    char *copy = malloc(strlen(path) + 1);
    int64_t index;
    if (copy == NULL) {
        abort();
    }
    memcpy(copy, path, strlen(path) + 1);
    for (index = 0; copy[index] != '\0'; index++) {
        if (copy[index] == '/') {
            copy[index] = '\0';
            if (copy[0] != '\0') {
                ps_mkdir(copy, 0755);
            }
            copy[index] = '/';
        }
    }
    free(copy);
}

/* The extraction: every member is written under the destination directory,
   the file modes of the archive are not carried (CPython's extractall
   keeps them, this subset writes with the process umask). */
void ps_archive_extractall(void *handle, int64_t dn, const char *dest) {
    PsArchive *archive = (PsArchive *)handle;
    char *destination = ps_archive_cstr(dn, dest);
    const unsigned char *data = archive->body;
    int64_t index;
    for (index = 0; index < archive->count; index++) {
        PsArchiveMember *member = &archive->members[index];
        char *target = ps_archive_target(destination, member->name);
        if (member->kind == 1) {
            ps_archive_make_dirs(target);
            free(target);
            continue;
        }
        ps_archive_make_dirs(target);
        {
            FILE *out = fopen(target, "wb");
            if (out == NULL) {
                free(target);
                free(destination);
                ps_archive_fail("the extracted file could not be written");
            }
            if (member->method == 0) {
                if (member->stored_size > 0 &&
                    fwrite(data + member->offset, 1, (size_t)member->stored_size, out) !=
                        (size_t)member->stored_size) {
                    fclose(out);
                    free(target);
                    free(destination);
                    ps_archive_fail("the extracted file could not be written");
                }
            } else {
                PsBuf bytes;
                ps_archive_member_bytes(archive, member, &bytes);
                if (bytes.len > 0 && fwrite(bytes.data, 1, (size_t)bytes.len, out) != (size_t)bytes.len) {
                    free(bytes.data);
                    fclose(out);
                    free(target);
                    free(destination);
                    ps_archive_fail("the extracted file could not be written");
                }
                free(bytes.data);
            }
            fclose(out);
        }
        free(target);
    }
    free(destination);
}

/* The close: the handle's members and its path ride with it. */
void ps_archive_close(void *handle) {
    PsArchive *archive = (PsArchive *)handle;
    int64_t index;
    if (archive == NULL) {
        return;
    }
    for (index = 0; index < archive->count; index++) {
        free(archive->members[index].name);
    }
    free(archive->members);
    free(archive->body);
    free(archive->path);
    free(archive);
}
