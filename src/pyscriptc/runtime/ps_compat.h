#ifndef PYSCRIPTC_PS_COMPAT_H
#define PYSCRIPTC_PS_COMPAT_H

/* Per-platform shims for the few POSIX calls the runtime uses.

   This header is included by ps_rt.h, so it is on the include path of every
   runtime translation unit. It deliberately does NOT include <windows.h>:
   that header defines min, max, IN and OUT as macros, which then collide
   with runtime identifiers in files that never touch Windows APIs. The
   Windows-only units that need the Win32 API include <windows.h> themselves,
   after defining NOMINMAX and _NO_W32_PSEUDO_MODIFIERS.
*/

#ifdef _WIN32

#include <direct.h>
#include <io.h>
#include <sys/stat.h>
#include <sys/types.h>
#define ps_mkdir(path, mode) _mkdir(path)
#define IS_DIR_SEP(c) ((c) == '/' || (c) == '\\')

/* mingw's <time.h> already declares gmtime_r and localtime_r, but only when
   _POSIX_C_SOURCE is defined. Define the shims ourselves so the runtime does
   not depend on that macro being set by the build. */
#if defined(_POSIX_C_SOURCE)
#define ps_gmtime_r(t, tm) gmtime_r((t), (tm))
#define ps_localtime_r(t, tm) localtime_r((t), (tm))
#else
#define ps_gmtime_r(t, tm) (gmtime_s((tm), (t)) == 0 ? (tm) : NULL)
#define ps_localtime_r(t, tm) (localtime_s((tm), (t)) == 0 ? (tm) : NULL)
#endif

/* Windows has no lstat and no S_ISLNK. islink() is therefore always false
   there; see docs/divergences.md for the numbered entry. */
#define ps_lstat(path, st) stat((path), (st))
#define S_ISLNK(mode) (0)

/* _fullpath resolves to an absolute path but does not resolve symlinks and
   returns NULL on failure, like POSIX realpath. It writes into the caller's
   buffer when one is given, and allocates with malloc when it is NULL. */
#define ps_realpath(path, resolved) _fullpath((resolved), (path), _MAX_PATH)

/* Windows has no setenv. PutEnvironmentVariableA is the Win32 spelling of
   the same thing, and it takes UTF-16, so the value is converted here. The
   runtime only ever sets ASCII names and values (a dotenv binding), so the
   narrow conversion is enough. */
#define ps_setenv(name, value, overwrite) ps_win_setenv((name), (value), (overwrite))
int ps_win_setenv(const char *name, const char *value, int overwrite);

/* Windows has no mkstemp: the portable version in ps_compat.c opens with
   _O_CREAT|_O_EXCL on a randomized suffix. */
int ps_mkstemp(char *template_path);

/* symlink is CreateSymbolicLinkA (a file link: the runtime carries no
   directory flag) and readlink has no CRT spelling at all; ps_compat.c
   answers both. */
int ps_win_symlink(const char *target, const char *linkpath);
int64_t ps_win_readlink(const char *path, char *buf, int64_t cap);
#define ps_symlink(target, linkpath) ps_win_symlink((target), (linkpath))
#define ps_readlink(path, buf, cap) ps_win_readlink((path), (buf), (cap))

/* mingw has setjmp/longjmp but no sigsetjmp/siglongjmp. The runtime only
   ever uses the savemask-0 form, which saves no signal mask, so the plain
   pair is the same thing there. */
#define ps_sigjmp_buf jmp_buf
#define ps_sigsetjmp(buf, savemask) setjmp((buf))
#define ps_siglongjmp(buf, code) longjmp((buf), (code))

#else

#include <sys/stat.h>
#include <sys/types.h>

#define ps_mkdir(path, mode) mkdir((path), (mode))
#define IS_DIR_SEP(c) ((c) == '/')
#define ps_gmtime_r(t, tm) gmtime_r((t), (tm))
#define ps_localtime_r(t, tm) localtime_r((t), (tm))
#define ps_lstat(path, st) lstat((path), (st))
#define ps_realpath(path, resolved) realpath((path), (resolved))
#define ps_setenv(name, value, overwrite) setenv((name), (value), (overwrite))
#if defined(__wasi__)
/* wasi-libc declares no sigsetjmp/siglongjmp. The runtime only ever uses
   the savemask-0 form, which saves no signal mask, so the plain pair is
   the same thing there. */
#define ps_sigjmp_buf jmp_buf
#define ps_sigsetjmp(buf, savemask) setjmp((buf))
#define ps_siglongjmp(buf, code) longjmp((buf), (code))
#else
#define ps_sigjmp_buf sigjmp_buf
#define ps_sigsetjmp(buf, savemask) sigsetjmp((buf), (savemask))
#define ps_siglongjmp(buf, code) siglongjmp((buf), (code))
#endif

/* wasi-libc declares no mkstemp: the portable version in ps_compat.c opens
   with O_CREAT|O_EXCL on a randomized suffix. Every target that has the
   native mkstemp keeps it. */
#ifdef __wasi__
int ps_mkstemp(char *template_path);
/* The wasm driver refuses os.symlink/os.readlink at link time, so these
   stubs only need to keep ps_path.c compiling on the wasi sysroot. */
int ps_wasi_symlink(const char *target, const char *linkpath);
int64_t ps_wasi_readlink(const char *path, char *buf, int64_t cap);
#define ps_symlink(target, linkpath) ps_wasi_symlink((target), (linkpath))
#define ps_readlink(path, buf, cap) ps_wasi_readlink((path), (buf), (cap))
#else
#define ps_mkstemp(template_path) mkstemp((template_path))
#define ps_symlink(target, linkpath) symlink((target), (linkpath))
#define ps_readlink(path, buf, cap) ((int64_t)readlink((path), (buf), (size_t)(cap)))
#endif

#endif

#endif
