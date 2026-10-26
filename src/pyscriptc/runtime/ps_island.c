/* Embedded CPython for the island modules.

Linked only when the LLVM text calls one of these symbols. Py_InitializeEx
runs on the first call, not at process start. Arguments and results stay in
the scalar, str, bytes, and list set the rest of the runtime already uses.
*/

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "ps_rt.h"

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#if defined(_WIN32)
#include <windows.h>
#elif defined(__APPLE__)
#include <mach-o/dyld.h>
#else
#include <unistd.h>
#endif
#if !defined(_WIN32)
#include <limits.h>
#endif

PsSeq *ps_argv_get(void);

/* Process-wide module slots (ps_osmore.c); the island top level stores the
   once-unboxed shadow values into them (issue #613). */
void ps_global_set_str(int64_t nn, const char *name, int64_t vn, const char *val);
void ps_global_set_i64(int64_t n, const char *name, int64_t value);
void ps_global_set_ptr(int64_t n, const char *name, void *value);

/* The GIL: Py_InitializeEx leaves the calling thread holding it, so every
   other native thread would block forever. SaveThread releases it after the
   one-time init, and each entry point re-enters through PyGILState_Ensure.
   The once-guard stops two threads from racing the init itself (the second
   Py_InitializeEx aborts with "global import state already initialized"). */
static pthread_once_t ps_island_once = PTHREAD_ONCE_INIT;

static void ps_island_init(void) {
    Py_InitializeEx(0);
    if (!Py_IsInitialized()) {
        /* No exception state to report from here: abort loudly. */
        fprintf(stderr, "CPython island failed to initialize\n");
        abort();
    }
    PyObject *site = PyImport_ImportModule("site");
    PyObject *sys = PyImport_ImportModule("sys");
    if (sys != NULL) {
        /* Issue #688: `Py_InitializeEx(0)` leaves `sys.executable` empty, and
           every `multiprocessing` helper spawn (the resource tracker, the
           forkserver) execs it, fails ENOENT, and the parent dies of SIGPIPE
           (exit 141) on the first Pool/Queue/Process use. Set it from the
           running image, then argv[0] - the first that yields a real file. */
        PyObject *executable = NULL;
        char argv0[PATH_MAX];
#if defined(_WIN32)
        char image[MAX_PATH];
        DWORD image_n = GetModuleFileNameA(NULL, image, sizeof image);
        if (image_n > 0 && image_n < sizeof image) {
            image[image_n] = '\0';
            executable = PyUnicode_DecodeFSDefault(image);
        }
#elif defined(__APPLE__)
        char image[PATH_MAX];
        uint32_t image_size = sizeof image;
        if (_NSGetExecutablePath(image, &image_size) == 0) {
            image[sizeof image - 1] = '\0';
            executable = PyUnicode_DecodeFSDefault(image);
        }
#else
        char image[PATH_MAX];
        ssize_t image_n = readlink("/proc/self/exe", image, sizeof image - 1);
        if (image_n > 0) {
            image[image_n] = '\0';
            executable = PyUnicode_DecodeFSDefaultAndSize(image, (Py_ssize_t)image_n);
        }
#endif
        if (executable == NULL) {
            PyErr_Clear();
            PsSeq *argv_seq = ps_argv_get();
            if (argv_seq != NULL && argv_seq->len > 0) {
                PsBuf *b = (PsBuf *)argv_seq->data;
                size_t n0 = b->len < sizeof argv0 - 1 ? b->len : sizeof argv0 - 1;
                if (n0 > 0) {
                    memcpy(argv0, b->data, n0);
                    argv0[n0] = '\0';
                    executable = PyUnicode_DecodeFSDefault(argv0);
                }
            }
        }
        if (executable != NULL) {
            PyObject_SetAttrString(sys, "executable", executable);
            Py_DECREF(executable);
            PyErr_Clear();
        }
        PyObject *out = PyObject_GetAttrString(sys, "stdout");
        if (out != NULL && out != Py_None) {
            PyObject *detach = PyObject_GetAttrString(out, "detach");
            if (detach != NULL) {
                PyObject *raw = PyObject_CallObject(detach, NULL);
                if (raw != NULL) {
                    PyObject *io = PyImport_ImportModule("io");
                    if (io != NULL) {
                        PyObject *maker = PyObject_GetAttrString(io, "TextIOWrapper");
                        if (maker != NULL) {
                            /* buffer, encoding, errors, newline, line_buffering,
                               write_through: line-buffered, UTF-8. */
                            PyObject *wrapper =
                                PyObject_CallFunction(maker, "Osssii", raw, "utf-8", "strict", "\n", 1, 0);
                            if (wrapper != NULL) {
                                PyObject_SetAttrString(sys, "stdout", wrapper);
                                Py_DECREF(wrapper);
                            }
                            Py_DECREF(maker);
                        }
                        Py_DECREF(io);
                    }
                    Py_DECREF(raw);
                }
                Py_DECREF(detach);
            }
            PyErr_Clear();
            Py_DECREF(out);
        }
        Py_DECREF(sys);
        PyErr_Clear();
    }
    const char *venv = getenv("VIRTUAL_ENV");
    if (venv != NULL && site != NULL) {
        char path[1024];
#ifdef _WIN32
        snprintf(path, sizeof path, "%s\\Lib\\site-packages", venv);
#else
        snprintf(path, sizeof path, "%s/lib/python%d.%d/site-packages", venv, PY_MAJOR_VERSION, PY_MINOR_VERSION);
#endif
        PyObject *res = PyObject_CallMethod(site, "addsitedir", "s", path);
        Py_XDECREF(res);
        PyErr_Clear();
    }
    const char *pythonpath = getenv("PYTHONPATH");
    if (pythonpath != NULL && pythonpath[0] != '\0' && site != NULL) {
        char *copy = strdup(pythonpath);
        if (copy != NULL) {
#ifdef _WIN32
            const char *delim = ";";
#else
            const char *delim = ":";
#endif
            char *tok = strtok(copy, delim);
            while (tok != NULL) {
                if (tok[0] != '\0') {
                    PyObject *res = PyObject_CallMethod(site, "addsitedir", "s", tok);
                    Py_XDECREF(res);
                    PyErr_Clear();
                }
                tok = strtok(NULL, delim);
            }
            free(copy);
        }
    }
    if (sys != NULL) {
        PyObject *sys_path = PyObject_GetAttrString(sys, "path");
        if (sys_path != NULL && PyList_Check(sys_path)) {
            PyObject *dot = PyUnicode_FromString("");
            if (dot != NULL) {
                PyList_Insert(sys_path, 0, dot);
                Py_DECREF(dot);
            }
            const char *prog_dir = getenv("PYSC_PROGRAM_DIR");
            if (prog_dir != NULL && prog_dir[0] != '\0') {
                if (site != NULL) {
                    PyObject *res = PyObject_CallMethod(site, "addsitedir", "s", prog_dir);
                    Py_XDECREF(res);
                    PyErr_Clear();
                }
                PyObject *pdir = PyUnicode_FromString(prog_dir);
                if (pdir != NULL) {
                    PyList_Insert(sys_path, 0, pdir);
                    Py_DECREF(pdir);
                }
            } else {
                /* Issue #665: the argv[0] fallback exists for the bare
                   single-script binary, where CPython's sys.path[0] is the
                   script's directory. In a relocated program bundle argv[0]
                   is the binary inside bin/, and putting bin/ on the path
                   would shadow stdlib names with whatever sits beside the
                   binary. A directory is also what `python SCRIPT` records,
                   so anything else (a directory-shaped argv[0] would be
                   `python dir`, and that puts nothing on sys.path) falls
                   back to the '' cwd entry, CPython's own. */
                PsSeq *argv_seq = ps_argv_get();
                if (argv_seq != NULL && argv_seq->len > 0) {
                    PsBuf *b = (PsBuf *)argv_seq->data;
                    if (b != NULL && b->data != NULL && b->len > 0) {
                        char buf[1024];
                        size_t len = (size_t)b->len < sizeof(buf) - 1 ? (size_t)b->len : sizeof(buf) - 1;
                        memcpy(buf, b->data, len);
                        buf[len] = '\0';
                        char *last_slash = strrchr(buf, '/');
#ifdef _WIN32
                        char *last_bslash = strrchr(buf, '\\');
                        if (last_bslash != NULL && (last_slash == NULL || last_bslash > last_slash)) {
                            last_slash = last_bslash;
                        }
#endif
                        if (last_slash != NULL) {
                            *last_slash = '\0';
                            /* Only an absolute directory joins the path: a
                               relative argv[0] (`./name`) would pin a path
                               that breaks on the first working-directory
                               change, and CPython records the script's
                               directory absolute. */
                            int absolute = buf[0] == '/';
#ifdef _WIN32
                            if (!absolute && buf[0] != '\0' && buf[1] == ':') {
                                absolute = 1;
                            }
#endif
                            struct stat st;
                            if (absolute && stat(buf, &st) == 0 && S_ISDIR(st.st_mode)) {
                                if (site != NULL) {
                                    PyObject *res = PyObject_CallMethod(site, "addsitedir", "s", buf);
                                    Py_XDECREF(res);
                                    PyErr_Clear();
                                }
                                PyObject *pdir = PyUnicode_FromString(buf);
                                if (pdir != NULL) {
                                    PyList_Insert(sys_path, 0, pdir);
                                    Py_DECREF(pdir);
                                }
                            }
                        }
                    }
                }
            }
            Py_DECREF(sys_path);
        }
        Py_DECREF(sys);
        PyErr_Clear();
    }
    if (site != NULL) {
        Py_DECREF(site);
        PyErr_Clear();
    }
    PyEval_SaveThread();
}

static void ps_island_ensure(void) {
    pthread_once(&ps_island_once, ps_island_init);
    PyGILState_Ensure();
}

/* Leave the island. Release the GIL the entry point took. Failure paths
   release through ps_island_fail before the raise unwinds the thread, so a
   thread that unwinds never leaves the interpreter locked. */
static void ps_island_done(void) {
    PyGILState_Release(PyGILState_UNLOCKED);
}

static int32_t ps_island_exc_code(PyObject *typ) {
    if (typ == NULL) {
        return PS_RUNTIME;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_TypeError)) {
        return PS_TYPEERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_ValueError)) {
        return PS_VALUEERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_KeyError)) {
        return PS_KEYERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_IndexError)) {
        return PS_INDEXERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_FileNotFoundError)) {
        return PS_FILENOTFOUNDERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_PermissionError)) {
        return PS_PERMISSIONERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_TimeoutError)) {
        return PS_TIMEOUTERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_ZeroDivisionError)) {
        return PS_ZERODIVISIONERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_OverflowError)) {
        return PS_OVERFLOWERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_AttributeError)) {
        return PS_ATTRIBUTEERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_NotImplementedError)) {
        return PS_NOTIMPLEMENTEDERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_ModuleNotFoundError)) {
        return PS_MODULENOTFOUNDERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_ImportError)) {
        return PS_IMPORTERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_OSError)) {
        return PS_OSERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_StopIteration)) {
        return PS_STOPITERATION;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_RecursionError)) {
        return PS_RECURSIONERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_AssertionError)) {
        return PS_ASSERTIONERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_MemoryError)) {
        return PS_MEMORYERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_NameError)) {
        return PS_NAMEERROR;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_RuntimeError)) {
        return PS_RUNTIME;
    }
    if (PyErr_GivenExceptionMatches(typ, PyExc_Exception)) {
        return PS_EXCEPTION;
    }
    return PS_RUNTIME;
}

static void ps_island_fail(void) {
    PyObject *typ = NULL;
    PyObject *val = NULL;
    PyObject *tb = NULL;
    PyObject *text = NULL;
    const char *msg = "island call failed";
    int32_t code = PS_RUNTIME;
    PyErr_Fetch(&typ, &val, &tb);
    PyErr_NormalizeException(&typ, &val, &tb);
    if (typ != NULL) {
        code = ps_island_exc_code(typ);
    }
    if (val != NULL) {
        if (PyErr_GivenExceptionMatches(val, PyExc_KeyError)) {
            PyObject *args = PyException_GetArgs(val);
            if (args != NULL && PyTuple_Check(args) && PyTuple_GET_SIZE(args) > 0) {
                text = PyObject_Str(PyTuple_GET_ITEM(args, 0));
            }
        }
        if (text == NULL) {
            text = PyObject_Str(val);
        }
        if (text != NULL) {
            const char *got = PyUnicode_AsUTF8(text);
            if (got != NULL) {
                msg = got;
            }
        }
        /* The island top level (issue #613, Decision E): an island
           `raise SystemExit(status)` or an uncaught `SystemExit` out of the
           seeded body carries the process status through the closed native
           table, exactly as CPython's own uncaught handler would. A str
           value stays a PS_SYSTEMEXIT message; the last-resort handler
           prints it and exits 1. */
        if (PyErr_GivenExceptionMatches(val, PyExc_SystemExit)) {
            /* CPython's rule on `SystemExit.code`: None exits 0, an int
               (bool included) is the status, anything else prints str(code)
               to stderr and exits 1. The message path flags the record so
               the last-resort handler prints it (issue #642). */
            PyObject *payload = PyObject_GetAttrString(val, "code");
            if (payload == NULL) {
                PyErr_Clear();
            } else if (payload == Py_None) {
                Py_DECREF(payload);
                Py_XDECREF(text);
                Py_XDECREF(typ);
                Py_XDECREF(val);
                Py_XDECREF(tb);
                ps_island_done();
                ps_raise_system_exit(PS_SYSTEMEXIT, 0);
            } else if (PyLong_Check(payload)) {
                int overflow = 0;
                long status = PyLong_AsLongAndOverflow(payload, &overflow);
                if (!overflow && PyErr_Occurred() == NULL) {
                    Py_DECREF(payload);
                    Py_XDECREF(text);
                    Py_XDECREF(typ);
                    Py_XDECREF(val);
                    Py_XDECREF(tb);
                    ps_island_done();
                    ps_raise_system_exit(PS_SYSTEMEXIT, (int64_t)status);
                }
                PyErr_Clear();
                Py_DECREF(payload);
            } else {
                PyObject *shown = PyObject_Str(payload);
                Py_DECREF(payload);
                if (shown != NULL) {
                    Py_XDECREF(text);
                    text = shown;
                    const char *got = PyUnicode_AsUTF8(text);
                    msg = got != NULL ? got : msg;
                } else {
                    PyErr_Clear();
                }
            }
            code = PS_SYSTEMEXIT;
        }
    }
    /* The GIL goes back before the raise unwinds the thread. */
    ps_island_done();
    if (code == PS_SYSTEMEXIT) {
        ps_raise_msg(code, (int64_t)strnlen(msg, 256), msg);
    }
    ps_raise_cstr(code, msg);
}

static PyObject *ps_import(const char *name) {
    PyObject *mod = PyImport_ImportModule(name);
    if (mod == NULL) {
        ps_island_fail();
    }
    return mod;
}

static void ps_take_unicode(PyObject *obj, PsBuf *out) {
    Py_ssize_t n = 0;
    const char *s = PyUnicode_AsUTF8AndSize(obj, &n);
    if (s == NULL) {
        ps_island_fail();
    }
    ps_buf_copy(out, s, (int64_t)n);
}

static void ps_take_bytes(PyObject *obj, PsBuf *out) {
    char *s = NULL;
    Py_ssize_t n = 0;
    if (PyBytes_AsStringAndSize(obj, &s, &n) < 0) {
        ps_island_fail();
    }
    ps_buf_copy(out, s, (int64_t)n);
}

void ps_island_check_output(PsSeq *args, PsBuf *out) {
    PyObject *mod;
    PyObject *fn;
    PyObject *list;
    PyObject *result;
    int64_t i;
    ps_island_ensure();
    list = PyList_New((Py_ssize_t)args->len);
    if (list == NULL) {
        ps_island_fail();
    }
    for (i = 0; i < args->len; i++) {
        PsBuf item;
        PyObject *text;
        memcpy(&item, (char *)args->data + (size_t)i * (size_t)args->elem, sizeof item);
        text = PyUnicode_FromStringAndSize(item.data, (Py_ssize_t)item.len);
        if (text == NULL || PyList_SetItem(list, (Py_ssize_t)i, text) < 0) {
            ps_island_fail();
        }
    }
    mod = ps_import("subprocess");
    fn = PyObject_GetAttrString(mod, "check_output");
    Py_DECREF(mod);
    if (fn == NULL) {
        Py_DECREF(list);
        ps_island_fail();
    }
    result = PyObject_CallFunctionObjArgs(fn, list, NULL);
    Py_DECREF(fn);
    Py_DECREF(list);
    if (result == NULL) {
        ps_island_fail();
    }
    ps_take_bytes(result, out);
    Py_DECREF(result);
    ps_island_done();
}

void ps_island_hostname(PsBuf *out) {
    PyObject *mod;
    PyObject *result;
    ps_island_ensure();
    mod = ps_import("socket");
    result = PyObject_CallMethod(mod, "gethostname", NULL);
    Py_DECREF(mod);
    if (result == NULL) {
        ps_island_fail();
    }
    ps_take_unicode(result, out);
    Py_DECREF(result);
    ps_island_done();
}

int64_t ps_island_threads(void) {
    PyObject *mod;
    PyObject *result;
    long value;
    ps_island_ensure();
    mod = ps_import("threading");
    result = PyObject_CallMethod(mod, "active_count", NULL);
    Py_DECREF(mod);
    if (result == NULL) {
        ps_island_fail();
    }
    value = PyLong_AsLong(result);
    Py_DECREF(result);
    if (value == -1 && PyErr_Occurred()) {
        ps_island_fail();
    }
    ps_island_done();
    return (int64_t)value;
}

void ps_island_thread_name(PsBuf *out) {
    PyObject *mod;
    PyObject *thread;
    PyObject *name;
    ps_island_ensure();
    mod = ps_import("threading");
    thread = PyObject_CallMethod(mod, "current_thread", NULL);
    Py_DECREF(mod);
    if (thread == NULL) {
        ps_island_fail();
    }
    name = PyObject_GetAttrString(thread, "name");
    Py_DECREF(thread);
    if (name == NULL) {
        ps_island_fail();
    }
    ps_take_unicode(name, out);
    Py_DECREF(name);
    ps_island_done();
}

void ps_island_cleandoc(int64_t n, const char *s, PsBuf *out) {
    PyObject *mod;
    PyObject *fn;
    PyObject *text;
    PyObject *result;
    ps_island_ensure();
    mod = ps_import("inspect");
    fn = PyObject_GetAttrString(mod, "cleandoc");
    Py_DECREF(mod);
    if (fn == NULL) {
        ps_island_fail();
    }
    text = PyUnicode_FromStringAndSize(s, (Py_ssize_t)n);
    if (text == NULL) {
        Py_DECREF(fn);
        ps_island_fail();
    }
    result = PyObject_CallFunctionObjArgs(fn, text, NULL);
    Py_DECREF(fn);
    Py_DECREF(text);
    if (result == NULL) {
        ps_island_fail();
    }
    ps_take_unicode(result, out);
    Py_DECREF(result);
    ps_island_done();
}

static void ps_island_log(const char *level, int64_t n, const char *s) {
    PyObject *mod;
    PyObject *fn;
    PyObject *text;
    PyObject *result;
    ps_island_ensure();
    mod = ps_import("logging");
    fn = PyObject_GetAttrString(mod, level);
    Py_DECREF(mod);
    if (fn == NULL) {
        ps_island_fail();
    }
    text = PyUnicode_FromStringAndSize(s, (Py_ssize_t)n);
    if (text == NULL) {
        Py_DECREF(fn);
        ps_island_fail();
    }
    result = PyObject_CallFunctionObjArgs(fn, text, NULL);
    Py_DECREF(fn);
    Py_DECREF(text);
    Py_XDECREF(result);
    if (result == NULL) {
        ps_island_fail();
    }
    ps_island_done();
}

void ps_island_log_info(int64_t n, const char *s) { ps_island_log("info", n, s); }

void ps_island_log_warning(int64_t n, const char *s) { ps_island_log("warning", n, s); }

void ps_island_log_debug(int64_t n, const char *s) { ps_island_log("debug", n, s); }

void ps_island_log_error(int64_t n, const char *s) { ps_island_log("error", n, s); }

void ps_island_log_critical(int64_t n, const char *s) { ps_island_log("critical", n, s); }

/* nativeprobe is a PyInit_* extension over libmix. Embedded CPython loads
   it the same way a normal interpreter does. The direct path is ps_mix.c. */

static int64_t ps_island_mix_call(const char *name, int64_t n, const char *data, int64_t times, int with_times) {
    PyObject *mod;
    PyObject *fn;
    PyObject *buf;
    PyObject *result;
    PyObject *count;
    int64_t value;
    ps_island_ensure();
    mod = ps_import("nativeprobe");
    fn = PyObject_GetAttrString(mod, name);
    Py_DECREF(mod);
    if (fn == NULL) {
        ps_island_fail();
    }
    buf = PyBytes_FromStringAndSize(data, (Py_ssize_t)n);
    if (buf == NULL) {
        Py_DECREF(fn);
        ps_island_fail();
    }
    if (with_times) {
        count = PyLong_FromLongLong(times);
        if (count == NULL) {
            Py_DECREF(fn);
            Py_DECREF(buf);
            ps_island_fail();
        }
        result = PyObject_CallFunctionObjArgs(fn, buf, count, NULL);
        Py_DECREF(count);
    } else {
        result = PyObject_CallFunctionObjArgs(fn, buf, NULL);
    }
    Py_DECREF(fn);
    Py_DECREF(buf);
    if (result == NULL) {
        ps_island_fail();
    }
    value = PyLong_AsLongLong(result);
    Py_DECREF(result);
    if (value == -1 && PyErr_Occurred()) {
        ps_island_fail();
    }
    ps_island_done();
    return value;
}

int64_t ps_island_mix_crc32(int64_t n, const char *data) {
    return ps_island_mix_call("crc32", n, data, 0, 0);
}

int64_t ps_island_mix_crc32_n(int64_t n, const char *data, int64_t times) {
    return ps_island_mix_call("crc32_n", n, data, times, 1);
}

/* The generic trampoline (issue #553): one import, one call and one attribute
   entry point over boxed lowered values, so an untyped PyPI dependency needs
   no per-symbol C shim. The kind codes match the emitter's `%ps.arg` store:

     1 int, 2 float, 3 str, 4 bool, 5 list, 6 bytes, 7 handle (a PyObject*).

   `pad` carries the element kind for a list. Handles are opaque and
   unidirectional: the island owns the object, the native side only holds a
   pointer and ends the ownership through `ps_island_free`. */

static int64_t ps_island_elem_size(int32_t pad) {
    switch (pad) {
    case 1:
    case 2:
    case 7:
        return 8;
    case 3:
    case 6:
        return 16;
    case 4:
        return 1;
    }
    return 0;
}

static PyObject *ps_island_from_cell(const void *cell, int32_t pad) {
    if (pad == 1) {
        int64_t v;
        memcpy(&v, cell, sizeof v);
        return PyLong_FromLongLong(v);
    }
    if (pad == 2) {
        double v;
        memcpy(&v, cell, sizeof v);
        return PyFloat_FromDouble(v);
    }
    if (pad == 3 || pad == 6) {
        PsBuf b;
        memcpy(&b, cell, sizeof b);
        if (pad == 3) {
            return PyUnicode_FromStringAndSize(b.data, (Py_ssize_t)b.len);
        }
        return PyBytes_FromStringAndSize(b.data, (Py_ssize_t)b.len);
    }
    if (pad == 4) {
        return PyBool_FromLong(*(unsigned char *)cell != 0);
    }
    PyErr_Format(PyExc_TypeError, "island cannot marshal list element kind %d", (int)pad);
    return NULL;
}

static PyObject *ps_island_from_arg(const PsArg *arg) {
    switch (arg->kind) {
    case 1:
        return PyLong_FromLongLong(arg->i);
    case 2:
        return PyFloat_FromDouble(arg->f);
    case 3:
        return PyUnicode_FromStringAndSize(arg->b.data, (Py_ssize_t)arg->b.len);
    case 4:
        return PyBool_FromLong(arg->i != 0);
    case 6:
        return PyBytes_FromStringAndSize(arg->b.data, (Py_ssize_t)arg->b.len);
    case 7: {
        PyObject *obj = (PyObject *)(uintptr_t)arg->i;
        Py_INCREF(obj);
        return obj;
    }
    case 5: {
        PsSeq *seq = (PsSeq *)(uintptr_t)arg->i;
        PyObject *list = PyList_New((Py_ssize_t)seq->len);
        int64_t i;
        if (list == NULL) {
            return NULL;
        }
        for (i = 0; i < seq->len; i++) {
            PyObject *item = ps_island_from_cell((char *)seq->data + (size_t)i * (size_t)seq->elem, arg->pad);
            if (item == NULL || PyList_SetItem(list, (Py_ssize_t)i, item) < 0) {
                Py_XDECREF(item);
                Py_DECREF(list);
                return NULL;
            }
        }
        return list;
    }
    case 8: {
        PsMap *map = (PsMap *)(uintptr_t)arg->i;
        PyObject *dict = PyDict_New();
        int32_t key_pad = (arg->pad >> 16) & 0xffff;
        int32_t val_pad = arg->pad & 0xffff;
        int64_t i;
        if (dict == NULL) {
            return NULL;
        }
        for (i = 0; i < map->len; i++) {
            char kcell[16];
            char vcell[16];
            ps_map_key_at(map, i, kcell);
            ps_map_val_at(map, i, vcell);
            PyObject *kobj = ps_island_from_cell(kcell, key_pad);
            PyObject *vobj = ps_island_from_cell(vcell, val_pad);
            if (kobj == NULL || vobj == NULL || PyDict_SetItem(dict, kobj, vobj) < 0) {
                Py_XDECREF(kobj);
                Py_XDECREF(vobj);
                Py_DECREF(dict);
                return NULL;
            }
            Py_DECREF(kobj);
            Py_DECREF(vobj);
        }
        return dict;
    }
    }
    PyErr_Format(PyExc_TypeError, "island cannot marshal argument kind %d", (int)arg->kind);
    return NULL;
}

static void ps_island_set_cell(char *cell, PyObject *obj, int32_t pad) {
    if (pad == 1) {
        if (!PyLong_Check(obj) || PyBool_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island element is %s, not int", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        int64_t v = PyLong_AsLongLong(obj);
        if (v == -1 && PyErr_Occurred()) {
            ps_island_fail();
        }
        memcpy(cell, &v, sizeof v);
        return;
    }
    if (pad == 2) {
        if (!PyFloat_Check(obj) && !PyLong_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island element is %s, not float", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        double v = PyFloat_AsDouble(obj);
        if (v == -1.0 && PyErr_Occurred()) {
            ps_island_fail();
        }
        memcpy(cell, &v, sizeof v);
        return;
    }
    if (pad == 3 || pad == 6) {
        if (pad == 3 && !PyUnicode_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island element is %s, not str", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        if (pad == 6 && !PyBytes_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island element is %s, not bytes", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        PsBuf b;
        if (pad == 3) {
            ps_take_unicode(obj, &b);
        } else {
            ps_take_bytes(obj, &b);
        }
        memcpy(cell, &b, sizeof b);
        return;
    }
    if (pad == 4) {
        if (!PyBool_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island element is %s, not bool", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        *(unsigned char *)cell = (obj == Py_True);
        return;
    }
    PyErr_Format(PyExc_TypeError, "island cannot marshal element kind %d", (int)pad);
    ps_island_fail();
}

static void ps_island_set_arg(PyObject *obj, int32_t want, int32_t pad, PsArg *out) {
    memset(out, 0, sizeof *out);
    out->kind = want;
    out->pad = pad;
    if (want == 1) {
        if (!PyLong_Check(obj) || PyBool_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island value is %s, not int", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        int64_t v = PyLong_AsLongLong(obj);
        if (v == -1 && PyErr_Occurred()) {
            ps_island_fail();
        }
        out->i = v;
        return;
    }
    if (want == 2) {
        if (!PyFloat_Check(obj) && !PyLong_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island value is %s, not float", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        double v = PyFloat_AsDouble(obj);
        if (v == -1.0 && PyErr_Occurred()) {
            ps_island_fail();
        }
        out->f = v;
        return;
    }
    if (want == 3) {
        if (!PyUnicode_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island value is %s, not str", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        ps_take_unicode(obj, &out->b);
        return;
    }
    if (want == 6) {
        if (!PyBytes_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island value is %s, not bytes", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        ps_take_bytes(obj, &out->b);
        return;
    }
    if (want == 4) {
        if (!PyBool_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island value is %s, not bool", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        out->i = (obj == Py_True);
        return;
    }
    if (want == 7) {
        out->i = (int64_t)(uintptr_t)obj;
        return;
    }
    if (want == 5) {
        if (!PyList_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island value is %s, not list", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        Py_ssize_t count = PyList_GET_SIZE(obj);
        int64_t elem = ps_island_elem_size(pad);
        if (elem == 0) {
            PyErr_Format(PyExc_TypeError, "island cannot marshal list element kind %d", (int)pad);
            ps_island_fail();
        }
        PsSeq *seq = ps_seq_new(elem, (int64_t)count);
        for (Py_ssize_t i = 0; i < count; i++) {
            char cell[16];
            PyObject *item = PyList_GET_ITEM(obj, i);
            ps_island_set_cell(cell, item, pad);
            ps_seq_set(seq, (int64_t)i, cell);
        }
        out->i = (int64_t)(uintptr_t)seq;
        return;
    }
    if (want == 8) {
        if (!PyDict_Check(obj)) {
            PyErr_Format(PyExc_TypeError, "island value is %s, not dict", Py_TYPE(obj)->tp_name);
            ps_island_fail();
        }
        int32_t key_pad = (pad >> 16) & 0xffff;
        int32_t val_pad = pad & 0xffff;
        int64_t key_size = ps_island_elem_size(key_pad);
        int64_t val_size = ps_island_elem_size(val_pad);
        if (key_size == 0 || val_size == 0) {
            PyErr_Format(PyExc_TypeError, "island cannot marshal dict with key kind %d, val kind %d", (int)key_pad, (int)val_pad);
            ps_island_fail();
        }
        int32_t key_kind = (key_pad == 1 ? 1 : 2);
        Py_ssize_t count = PyDict_Size(obj);
        PsMap *map = ps_map_new(key_kind, key_size, val_size, (int64_t)count);
        PyObject *key_obj;
        PyObject *val_obj;
        Py_ssize_t pos = 0;
        while (PyDict_Next(obj, &pos, &key_obj, &val_obj)) {
            char kcell[16];
            char vcell[16];
            ps_island_set_cell(kcell, key_obj, key_pad);
            ps_island_set_cell(vcell, val_obj, val_pad);
            ps_map_put(map, kcell, vcell);
        }
        out->i = (int64_t)(uintptr_t)map;
        return;
    }
    PyErr_Format(PyExc_TypeError, "island cannot marshal result kind %d", (int)want);
    ps_island_fail();
}

void *ps_island_import(int64_t n, const char *name) {
    PyObject *modname;
    PyObject *mod;
    ps_island_ensure();
    modname = PyUnicode_FromStringAndSize(name, (Py_ssize_t)n);
    if (modname == NULL) {
        ps_island_fail();
    }
    mod = PyImport_Import(modname);
    Py_DECREF(modname);
    if (mod == NULL) {
        ps_island_fail();
    }
    ps_island_done();
    return mod;
}

static PyObject *ps_island_build_args(int64_t nargs, const PsArg *args) {
    int has_starred = 0;
    int64_t i;
    for (i = 0; i < nargs; i++) {
        if ((args[i].kind & 0x100) != 0) {
            has_starred = 1;
            break;
        }
    }
    if (!has_starred) {
        PyObject *tuple = PyTuple_New((Py_ssize_t)nargs);
        if (tuple == NULL) {
            return NULL;
        }
        for (i = 0; i < nargs; i++) {
            PyObject *item = ps_island_from_arg(&args[i]);
            if (item == NULL || PyTuple_SetItem(tuple, (Py_ssize_t)i, item) < 0) {
                Py_XDECREF(item);
                Py_DECREF(tuple);
                return NULL;
            }
        }
        return tuple;
    }
    PyObject *list = PyList_New(0);
    if (list == NULL) {
        return NULL;
    }
    for (i = 0; i < nargs; i++) {
        if ((args[i].kind & 0x100) != 0) {
            PsArg base = args[i];
            base.kind &= ~0x100;
            PyObject *seq = ps_island_from_arg(&base);
            if (seq == NULL) {
                Py_DECREF(list);
                return NULL;
            }
            PyObject *iter = PyObject_GetIter(seq);
            Py_DECREF(seq);
            if (iter == NULL) {
                Py_DECREF(list);
                return NULL;
            }
            PyObject *sub;
            while ((sub = PyIter_Next(iter)) != NULL) {
                if (PyList_Append(list, sub) < 0) {
                    Py_DECREF(sub);
                    Py_DECREF(iter);
                    Py_DECREF(list);
                    return NULL;
                }
                Py_DECREF(sub);
            }
            Py_DECREF(iter);
            if (PyErr_Occurred()) {
                Py_DECREF(list);
                return NULL;
            }
        } else {
            PyObject *item = ps_island_from_arg(&args[i]);
            if (item == NULL) {
                Py_DECREF(list);
                return NULL;
            }
            if (PyList_Append(list, item) < 0) {
                Py_DECREF(item);
                Py_DECREF(list);
                return NULL;
            }
            Py_DECREF(item);
        }
    }
    PyObject *tuple = PyList_AsTuple(list);
    Py_DECREF(list);
    return tuple;
}

static PyObject *ps_island_build_kwargs(int64_t nkw, const PsArg *kws) {
    if (nkw == 0 || kws == NULL) {
        return NULL;
    }
    PyObject *kwdict = PyDict_New();
    if (kwdict == NULL) {
        return NULL;
    }
    int64_t i;
    for (i = 0; i < nkw; i++) {
        const PsArg *k_arg = &kws[2 * i];
        const PsArg *v_arg = &kws[2 * i + 1];
        if (k_arg->kind == 0) {
            PyObject *d = ps_island_from_arg(v_arg);
            if (d == NULL) {
                Py_DECREF(kwdict);
                return NULL;
            }
            if (PyDict_Update(kwdict, d) < 0) {
                Py_DECREF(d);
                Py_DECREF(kwdict);
                return NULL;
            }
            Py_DECREF(d);
        } else {
            PyObject *key = ps_island_from_arg(k_arg);
            PyObject *val = ps_island_from_arg(v_arg);
            if (key == NULL || val == NULL) {
                Py_XDECREF(key);
                Py_XDECREF(val);
                Py_DECREF(kwdict);
                return NULL;
            }
            if (PyDict_SetItem(kwdict, key, val) < 0) {
                Py_DECREF(key);
                Py_DECREF(val);
                Py_DECREF(kwdict);
                return NULL;
            }
            Py_DECREF(key);
            Py_DECREF(val);
        }
    }
    return kwdict;
}

void *ps_island_call(void *handle, int64_t n, const char *name, int64_t nargs, const PsArg *args, int64_t nkw, const PsArg *kws) {
    PyObject *target = (PyObject *)handle;
    PyObject *name_obj;
    PyObject *fn;
    PyObject *tuple;
    PyObject *kwdict;
    PyObject *result;
    ps_island_ensure();
    name_obj = PyUnicode_FromStringAndSize(name, (Py_ssize_t)n);
    if (name_obj == NULL) {
        ps_island_fail();
    }
    fn = PyObject_GetAttr(target, name_obj);
    Py_DECREF(name_obj);
    if (fn == NULL) {
        ps_island_fail();
    }
    tuple = ps_island_build_args(nargs, args);
    if (tuple == NULL) {
        Py_DECREF(fn);
        ps_island_fail();
    }
    kwdict = ps_island_build_kwargs(nkw, kws);
    if (kwdict == NULL && PyErr_Occurred()) {
        Py_DECREF(fn);
        Py_DECREF(tuple);
        ps_island_fail();
    }
    result = PyObject_Call(fn, tuple, kwdict);
    Py_DECREF(fn);
    Py_DECREF(tuple);
    Py_XDECREF(kwdict);
    if (result == NULL) {
        ps_island_fail();
    }
    ps_island_done();
    return result;
}

void ps_island_getattr(void *handle, int64_t n, const char *name, int32_t want, int32_t pad, PsArg *out) {
    PyObject *target = (PyObject *)handle;
    PyObject *name_obj;
    PyObject *value;
    ps_island_ensure();
    name_obj = PyUnicode_FromStringAndSize(name, (Py_ssize_t)n);
    if (name_obj == NULL) {
        ps_island_fail();
    }
    value = PyObject_GetAttr(target, name_obj);
    Py_DECREF(name_obj);
    if (value == NULL) {
        ps_island_fail();
    }
    ps_island_set_arg(value, want, pad, out);
    if (want != 7) {
        Py_DECREF(value);
    }
    ps_island_done();
}

void ps_island_call_value(void *handle, int64_t n, const char *name, int64_t nargs, const PsArg *args, int64_t nkw, const PsArg *kws, int32_t want, int32_t pad, PsArg *out) {
    PyObject *result = (PyObject *)ps_island_call(handle, n, name, nargs, args, nkw, kws);
    ps_island_ensure();
    ps_island_set_arg(result, want, pad, out);
    if (want != 7) {
        Py_DECREF(result);
    }
    ps_island_done();
}

void ps_island_unmarshal(void *handle, int32_t want, int32_t pad, PsArg *out) {
    PyObject *target = (PyObject *)handle;
    ps_island_ensure();
    if (target == NULL) {
        ps_island_fail();
    }
    ps_island_set_arg(target, want, pad, out);
    ps_island_done();
}

void ps_island_free(void *handle) {
    if (handle == NULL) {
        return;
    }
    ps_island_ensure();
    Py_DECREF((PyObject *)handle);
    ps_island_done();
}

/* The boxed per-definition tier (issue #612): the user's own module text is
   exec'd once into a namespace owned here, and every fenced definition is
   called by name through this trampoline beside ps_island_call. The source
   is a NUL-terminated UTF-8 global the emitter formats; `main_guard` is
   false while the seeding exec runs so `if __name__ == "__main__":` inside
   the island text does not fire (the entry shim invokes the entry itself). */

static PyObject *ps_island_ns = NULL;

/* Mirror the process argv the native runtime already holds (ps_argv_init)
   into sys.argv, so `sys.argv` reads inside island functions see the real
   arguments (issue #635). The island top-level seed does the same thing
   inline; this shared helper exists because the per-definition seed used
   here runs after the native main's ps_argv_init, and the embedded
   interpreter otherwise keeps the CPython default `['']`. */
static void ps_island_seed_argv(void) {
    PyObject *sys = PyImport_ImportModule("sys");
    if (sys == NULL) {
        PyErr_Clear();
        return;
    }
    PsSeq *argv_seq = ps_argv_get();
    PyObject *argv_list = PyList_New(0);
    if (argv_list != NULL) {
        int64_t i;
        for (i = 0; argv_seq != NULL && i < argv_seq->len; i++) {
            PsBuf *b = (PsBuf *)argv_seq->data + i;
            PyObject *item = PyUnicode_DecodeUTF8(b->data, (Py_ssize_t)b->len, "strict");
            if (item == NULL || PyList_Append(argv_list, item) < 0) {
                Py_XDECREF(item);
                Py_DECREF(argv_list);
                argv_list = NULL;
                break;
            }
            Py_DECREF(item);
        }
        if (argv_list != NULL) {
            PyObject_SetAttrString(sys, "argv", argv_list);
            Py_DECREF(argv_list);
        }
    }
    Py_DECREF(sys);
    PyErr_Clear();
}

/* The seed namespace is a real module object registered in `sys.modules`
   (issue #689): `pickle` and `multiprocessing` resolve island functions
   through `<module>.__dict__`, so an exec'd plain dict leaves every island
   function unpicklable ("it's not found as __main__.worker"). The module
   carries the seed's `__name__` (`__pyscriptc_island__`), so a failed
   re-import names the island instead of a phantom file. */
static PyObject *ps_island_seed_module(const char *name) {
    PyObject *module = NULL;
    PyObject *sys = NULL;
    PyObject *modules;
    module = PyImport_AddModule(name);
    if (module == NULL) {
        return NULL;
    }
    Py_INCREF(module);
    sys = PyImport_ImportModule("sys");
    modules = sys != NULL ? PyObject_GetAttrString(sys, "modules") : NULL;
    if (modules == NULL || PyDict_SetItemString(modules, name, module) < 0) {
        Py_XDECREF(modules);
        Py_XDECREF(sys);
        Py_DECREF(module);
        return NULL;
    }
    Py_XDECREF(modules);
    Py_XDECREF(sys);
    return module;
}

static int ps_island_seed(int64_t n, const char *source) {
    PyObject *text = PyUnicode_DecodeUTF8(source, (Py_ssize_t)n, "strict");
    PyObject *builtins = NULL;
    PyObject *main_name = NULL;
    PyObject *module = NULL;
    PyObject *result = NULL;
    if (text == NULL) {
        return -1;
    }
    module = ps_island_seed_module("__pyscriptc_island__");
    if (module == NULL) {
        Py_DECREF(text);
        return -1;
    }
    /* Fresh module each seed: a stale `__dict__` from an earlier unit would
       satisfy lookups that should fail. */
    PyDict_Clear(PyModule_GetDict(module));
    ps_island_ns = PyModule_GetDict(module);
    builtins = PyImport_ImportModule("builtins");
    if (builtins == NULL || PyDict_SetItemString(ps_island_ns, "__builtins__", builtins) < 0) {
        Py_XDECREF(builtins);
        Py_DECREF(text);
        Py_DECREF(module);
        return -1;
    }
    Py_DECREF(builtins);
    /* `__name__` is not `"__main__"` while the seeding exec runs, so the
       script's main guard stays closed; `ps_island_fn_name` rewrites it
       before the entry shim runs the entry. */
    main_name = PyUnicode_FromString("__pyscriptc_island__");
    if (main_name == NULL || PyDict_SetItemString(ps_island_ns, "__name__", main_name) < 0) {
        Py_XDECREF(main_name);
        Py_DECREF(text);
        Py_DECREF(module);
        return -1;
    }
    Py_DECREF(main_name);
    /* Mirror the process argv into sys.argv (issue #635) so island
       functions see the same arguments the native side recorded. */
    ps_island_seed_argv();
    result = PyRun_String(PyUnicode_AsUTF8(text) != NULL ? PyUnicode_AsUTF8(text) : "",
                          Py_file_input, ps_island_ns, ps_island_ns);
    Py_DECREF(text);
    Py_DECREF(module);
    if (result == NULL) {
        return -1;
    }
    Py_DECREF(result);
    return 0;
}

void ps_island_fn_seed(int64_t n, const char *source) {
    ps_island_ensure();
    if (ps_island_seed(n, source) < 0) {
        ps_island_fail();
    }
    ps_island_done();
}

/* The island top level (issue #613): the seed is the module's own source,
   so the exec runs with `__name__` set to `__main__` - the script's own
   main guard fires island-side, exactly once, and `raise SystemExit(...)`
   out of the body delivers the process status through ps_island_fail.
   `path` seeds `__file__` (a Path-global script resolves its data files
   through it); it may be NULL. The native entry never runs when this seed
   is present: it IS the program. */
void ps_island_top_seed(int64_t n, const char *source, int64_t path_n, const char *path) {
    PyObject *text;
    PyObject *builtins = NULL;
    PyObject *main_name = NULL;
    PyObject *island_name = NULL;
    PyObject *file_name = NULL;
    PyObject *module = NULL;
    PyObject *main_module = NULL;
    PyObject *result = NULL;
    ps_island_ensure();
    text = PyUnicode_DecodeUTF8(source, (Py_ssize_t)n, "strict");
    if (text == NULL) {
        ps_island_fail();
    }
    /* Issue #689: exec into the real `__main__` module registered in
       `sys.modules`, the way `runpy` seeds a script: the whole program runs
       in this one seed, so pickle must resolve functions out of
       `sys.modules["__main__"]` while it runs. `__pyscriptc_island__`
       aliases the same module object, so a failed re-import names the
       island instead of a phantom `__main__` file. */
    main_module = ps_island_seed_module("__main__");
    if (main_module == NULL) {
        Py_DECREF(text);
        ps_island_fail();
    }
    module = main_module;
    Py_INCREF(module);
    {
        PyObject *sys_mods = NULL;
        PyObject *sysm = PyImport_ImportModule("sys");
        island_name = PyUnicode_FromString("__pyscriptc_island__");
        if (sysm != NULL) {
            sys_mods = PyObject_GetAttrString(sysm, "modules");
        }
        if (island_name == NULL || sys_mods == NULL ||
            PyDict_SetItem(sys_mods, island_name, main_module) < 0) {
            Py_XDECREF(sys_mods);
            Py_XDECREF(sysm);
            Py_XDECREF(island_name);
            Py_DECREF(module);
            Py_DECREF(main_module);
            Py_DECREF(text);
            ps_island_fail();
        }
        Py_DECREF(sys_mods);
        Py_XDECREF(sysm);
        Py_DECREF(island_name);
    }
    PyDict_Clear(PyModule_GetDict(module));
    ps_island_ns = PyModule_GetDict(module);
    builtins = PyImport_ImportModule("builtins");
    if (builtins == NULL || PyDict_SetItemString(ps_island_ns, "__builtins__", builtins) < 0) {
        Py_XDECREF(builtins);
        Py_DECREF(text);
        ps_island_fail();
    }
    Py_DECREF(builtins);
    main_name = PyUnicode_FromString("__main__");
    if (main_name == NULL || PyDict_SetItemString(ps_island_ns, "__name__", main_name) < 0) {
        Py_XDECREF(main_name);
        Py_DECREF(main_module);
        Py_DECREF(module);
        Py_DECREF(text);
        ps_island_fail();
    }
    Py_DECREF(main_name);
    /* multiprocessing's spawn/forkserver bootstrapping reads
       `__main__.__spec__`; a plain script has `None` there, and a missing
       attribute breaks the bootstrap differently. */
    PyDict_SetItemString(ps_island_ns, "__spec__", Py_None);
    if (path != NULL) {
        file_name = PyUnicode_DecodeUTF8(path, (Py_ssize_t)path_n, "strict");
        if (file_name == NULL || PyDict_SetItemString(ps_island_ns, "__file__", file_name) < 0) {
            Py_XDECREF(file_name);
            Py_DECREF(main_module);
            Py_DECREF(module);
            Py_DECREF(text);
            ps_island_fail();
        }
        Py_DECREF(file_name);
    }
    /* sys.argv mirrors the process argv the native runtime already holds
       (ps_argv_init), so `len(sys.argv) != 2` and friends behave as under
       CPython. argv[0] becomes the program path the native side recorded
       first. */
    ps_island_seed_argv();
    result = PyRun_String(PyUnicode_AsUTF8(text) != NULL ? PyUnicode_AsUTF8(text) : "",
                          Py_file_input, ps_island_ns, ps_island_ns);
    Py_DECREF(text);
    if (result == NULL) {
        Py_DECREF(main_module);
        Py_DECREF(module);
        ps_island_fail();
    }
    Py_DECREF(result);
    Py_DECREF(main_module);
    Py_DECREF(module);
    ps_island_done();
}

/* Unbox one annotated scalar global the island top level bound (issue #613,
   Decision E): the value is read out of the island namespace once, after the
   seed ran, and stored into the native process-wide slot the native readers
   of the global use. Later island-side rewrites stay invisible (the
   documented one-directional seam). `want`/`pad` select the unmarshal shape,
   the same encoding `ps_island_set_arg` already speaks. */
void ps_island_top_global(int64_t n, const char *name, int32_t want, int32_t pad) {
    PyObject *name_obj;
    PyObject *value;
    PsArg arg;
    memset(&arg, 0, sizeof(arg));
    ps_island_ensure();
    if (ps_island_ns == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "island module namespace is not seeded");
        ps_island_fail();
    }
    name_obj = PyUnicode_DecodeUTF8(name, (Py_ssize_t)n, "strict");
    if (name_obj == NULL) {
        ps_island_fail();
    }
    value = PyDict_GetItemWithError(ps_island_ns, name_obj);
    Py_DECREF(name_obj);
    if (value == NULL) {
        if (!PyErr_Occurred()) {
            PyErr_Format(PyExc_NameError, "island global %s is not defined", name);
        }
        ps_island_fail();
    }
    Py_INCREF(value);
    ps_island_set_arg(value, want, pad, &arg);
    Py_DECREF(value);
    /* The native slot names carry the island prefix so a program cannot
       collide with its own `global` writes (which fence, issue #613). */
    {
        char full[512];
        snprintf(full, sizeof full, "__island__%.*s", (int)(n > 480 ? 480 : n), name);
        switch (want) {
        case 1: /* int */
            ps_global_set_i64((int64_t)strlen(full), full, arg.i);
            break;
        case 4: /* bool */
            ps_global_set_i64((int64_t)strlen(full), full, arg.i ? 1 : 0);
            break;
        case 3: /* str */
        case 6: /* bytes */
            ps_global_set_str((int64_t)strlen(full), full, arg.b.len, (const char *)arg.b.data);
            break;
        default: /* list of scalars: crosses as the seq pointer */
            ps_global_set_ptr((int64_t)strlen(full), full, (void *)(uintptr_t)arg.i);
            break;
        }
    }
    ps_island_done();
}

/* One object out of the island namespace, as a new reference (issue #628).
   An island class definition is exec'd into this namespace, and compiled
   code reaches the class object itself (a class-level attribute read, a
   class passed as a value) through this trampoline beside
   ps_island_fn_invoke, which handles the call. */
void *ps_island_def_handle(int64_t n, const char *name) {
    PyObject *name_obj;
    PyObject *value;
    ps_island_ensure();
    if (ps_island_ns == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "island module namespace is not seeded");
        ps_island_fail();
    }
    name_obj = PyUnicode_DecodeUTF8(name, (Py_ssize_t)n, "strict");
    if (name_obj == NULL) {
        ps_island_fail();
    }
    value = PyDict_GetItemWithError(ps_island_ns, name_obj);
    Py_DECREF(name_obj);
    if (value == NULL) {
        if (!PyErr_Occurred()) {
            PyErr_Format(PyExc_NameError, "island definition %s is not defined", name);
        }
        ps_island_fail();
    }
    Py_INCREF(value);
    ps_island_done();
    return value;
}

void ps_island_fn_name(int64_t n, const char *name) {
    PyObject *name_obj;
    ps_island_ensure();
    if (ps_island_ns == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "island module namespace is not seeded");
        ps_island_fail();
    }
    name_obj = PyUnicode_DecodeUTF8(name, (Py_ssize_t)n, "strict");
    if (name_obj == NULL || PyDict_SetItemString(ps_island_ns, "__name__", name_obj) < 0) {
        Py_XDECREF(name_obj);
        ps_island_fail();
    }
    Py_DECREF(name_obj);
    ps_island_done();
}

void *ps_island_fn_invoke(int64_t n, const char *name, int64_t nargs, const PsArg *args, int64_t nkw, const PsArg *kws) {
    PyObject *name_obj;
    PyObject *fn;
    PyObject *tuple;
    PyObject *kwdict;
    PyObject *result;
    ps_island_ensure();
    if (ps_island_ns == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "island module namespace is not seeded");
        ps_island_fail();
    }
    name_obj = PyUnicode_DecodeUTF8(name, (Py_ssize_t)n, "strict");
    if (name_obj == NULL) {
        ps_island_fail();
    }
    fn = PyDict_GetItemWithError(ps_island_ns, name_obj);
    Py_DECREF(name_obj);
    if (fn == NULL) {
        if (!PyErr_Occurred()) {
            PyErr_Format(PyExc_NameError, "island function %s is not defined", name);
        }
        ps_island_fail();
    }
    Py_INCREF(fn);
    tuple = ps_island_build_args(nargs, args);
    if (tuple == NULL) {
        Py_DECREF(fn);
        ps_island_fail();
    }
    kwdict = ps_island_build_kwargs(nkw, kws);
    if (kwdict == NULL && PyErr_Occurred()) {
        Py_DECREF(fn);
        Py_DECREF(tuple);
        ps_island_fail();
    }
    result = PyObject_Call(fn, tuple, kwdict);
    Py_DECREF(fn);
    Py_DECREF(tuple);
    Py_XDECREF(kwdict);
    if (result == NULL) {
        ps_island_fail();
    }
    ps_island_done();
    return result;
}

void ps_island_fn_invoke_value(int64_t n, const char *name, int64_t nargs, const PsArg *args, int64_t nkw, const PsArg *kws, int32_t want, int32_t pad, PsArg *out) {
    PyObject *result = (PyObject *)ps_island_fn_invoke(n, name, nargs, args, nkw, kws);
    ps_island_ensure();
    ps_island_set_arg(result, want, pad, out);
    if (want != 7) {
        Py_DECREF(result);
    }
    ps_island_done();
}

void ps_island_getitem(void *handle, int64_t n, const char *key, int32_t want, int32_t pad, PsArg *out) {
    PyObject *target = (PyObject *)handle;
    PyObject *key_obj;
    PyObject *value;
    ps_island_ensure();
    key_obj = PyUnicode_DecodeUTF8(key, (Py_ssize_t)n, "strict");
    if (key_obj == NULL) {
        ps_island_fail();
    }
    value = PyObject_GetItem(target, key_obj);
    Py_DECREF(key_obj);
    if (value == NULL) {
        ps_island_fail();
    }
    ps_island_set_arg(value, want, pad, out);
    if (want != 7) {
        Py_DECREF(value);
    }
    ps_island_done();
}

