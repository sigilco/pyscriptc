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
    const char *venv = getenv("VIRTUAL_ENV");
    if (venv != NULL) {
        PyObject *site = PyImport_ImportModule("site");
        if (site != NULL) {
            char path[1024];
#ifdef _WIN32
            snprintf(path, sizeof path, "%s\\Lib\\site-packages", venv);
#else
            snprintf(path, sizeof path, "%s/lib/python%d.%d/site-packages", venv, PY_MAJOR_VERSION, PY_MINOR_VERSION);
#endif
            PyObject *res = PyObject_CallMethod(site, "addsitedir", "s", path);
            Py_XDECREF(res);
            Py_DECREF(site);
            PyErr_Clear();
        }
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
    }
    PyErr_Format(PyExc_TypeError, "island cannot marshal argument kind %d", (int)arg->kind);
    return NULL;
}

static void ps_island_set_cell(char *cell, PyObject *obj, int32_t pad) {
    if (pad == 1) {
        int64_t v = PyLong_AsLongLong(obj);
        if (v == -1 && PyErr_Occurred()) {
            ps_island_fail();
        }
        memcpy(cell, &v, sizeof v);
        return;
    }
    if (pad == 2) {
        double v = PyFloat_AsDouble(obj);
        if (v == -1.0 && PyErr_Occurred()) {
            ps_island_fail();
        }
        memcpy(cell, &v, sizeof v);
        return;
    }
    if (pad == 3 || pad == 6) {
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
        int truth = PyObject_IsTrue(obj);
        if (truth < 0) {
            ps_island_fail();
        }
        *(unsigned char *)cell = truth != 0;
        return;
    }
    PyErr_Format(PyExc_TypeError, "island cannot marshal list element kind %d", (int)pad);
    ps_island_fail();
}

static void ps_island_set_arg(PyObject *obj, int32_t want, int32_t pad, PsArg *out) {
    memset(out, 0, sizeof *out);
    out->kind = want;
    out->pad = pad;
    if (want == 1) {
        int64_t v = PyLong_AsLongLong(obj);
        if (v == -1 && PyErr_Occurred()) {
            ps_island_fail();
        }
        out->i = v;
        return;
    }
    if (want == 2) {
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
        int truth = PyObject_IsTrue(obj);
        if (truth < 0) {
            ps_island_fail();
        }
        out->i = truth != 0;
        return;
    }
    if (want == 7) {
        out->i = (int64_t)(uintptr_t)obj;
        return;
    }
    if (want == 5) {
        PyObject *seq_obj = PySequence_Fast(obj, "island value is not a list");
        Py_ssize_t count;
        int64_t elem;
        int64_t i;
        PsSeq *seq;
        if (seq_obj == NULL) {
            ps_island_fail();
        }
        count = PySequence_Fast_GET_SIZE(seq_obj);
        elem = ps_island_elem_size(pad);
        if (elem == 0) {
            Py_DECREF(seq_obj);
            PyErr_Format(PyExc_TypeError, "island cannot marshal list element kind %d", (int)pad);
            ps_island_fail();
        }
        seq = ps_seq_new(elem, (int64_t)count);
        for (i = 0; i < count; i++) {
            char cell[16];
            PyObject *item = PySequence_Fast_GET_ITEM(seq_obj, (Py_ssize_t)i);
            ps_island_set_cell(cell, item, pad);
            ps_seq_push(seq, cell);
        }
        Py_DECREF(seq_obj);
        out->i = (int64_t)(uintptr_t)seq;
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

void *ps_island_call(void *handle, int64_t n, const char *name, int64_t nargs, const PsArg *args) {
    PyObject *target = (PyObject *)handle;
    PyObject *name_obj;
    PyObject *fn;
    PyObject *tuple;
    PyObject *result;
    int64_t i;
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
    tuple = PyTuple_New((Py_ssize_t)nargs);
    if (tuple == NULL) {
        Py_DECREF(fn);
        ps_island_fail();
    }
    for (i = 0; i < nargs; i++) {
        PyObject *item = ps_island_from_arg(&args[i]);
        if (item == NULL || PyTuple_SetItem(tuple, (Py_ssize_t)i, item) < 0) {
            Py_XDECREF(item);
            Py_DECREF(tuple);
            Py_DECREF(fn);
            ps_island_fail();
        }
    }
    result = PyObject_Call(fn, tuple, NULL);
    Py_DECREF(fn);
    Py_DECREF(tuple);
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

void ps_island_free(void *handle) {
    if (handle == NULL) {
        return;
    }
    ps_island_ensure();
    Py_DECREF((PyObject *)handle);
    ps_island_done();
}
