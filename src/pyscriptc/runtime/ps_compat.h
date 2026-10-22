#ifndef PYSCRIPTC_PS_COMPAT_H
#define PYSCRIPTC_PS_COMPAT_H

#ifdef _WIN32
#include <windows.h>
#include <direct.h>
#include <io.h>

#define ps_mkdir(path, mode) _mkdir(path)
#define gmtime_r(t, tm) (gmtime_s((tm), (t)) == 0 ? (tm) : NULL)
#define localtime_r(t, tm) (localtime_s((tm), (t)) == 0 ? (tm) : NULL)
#define lstat(path, st) stat(path, st)
#define S_ISLNK(mode) (0)
#define realpath(path, resolved) _fullpath(resolved, path, _MAX_PATH)
#define IS_DIR_SEP(c) ((c) == '/' || (c) == '\\')
#else
#define ps_mkdir(path, mode) mkdir(path, mode)
#define IS_DIR_SEP(c) ((c) == '/')
#endif

#endif
