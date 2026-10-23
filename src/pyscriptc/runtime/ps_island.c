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

/* The island tier embeds CPython, so every line of this file is boxed code
   (the ledger counts `src/pyscriptc/runtime/ps_island*` whole). */

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

static void ps_island_fail(void) {
    PyObject *typ = NULL;
    PyObject *val = NULL;
    PyObject *tb = NULL;
    PyObject *text = NULL;
    const char *msg = "island call failed";
    PyErr_Fetch(&typ, &val, &tb);
    PyErr_NormalizeException(&typ, &val, &tb);
    if (val != NULL) {
        text = PyObject_Str(val);
        if (text != NULL) {
            const char *got = PyUnicode_AsUTF8(text);
            if (got != NULL) {
                msg = got;
            }
        }
    }
    /* The GIL goes back before the raise unwinds the thread. */
    ps_island_done();
    ps_raise_cstr(PS_RUNTIME, msg);
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
