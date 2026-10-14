#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include "mix.h"

/* Python extension over libmix. The only defined export a loader needs
   is PyInit_nativeprobe. mix_crc32 stays in libmix.so. */

static PyObject *py_crc32(PyObject *self, PyObject *arg) {
    char *data = NULL;
    Py_ssize_t n = 0;
    (void)self;
    if (PyBytes_AsStringAndSize(arg, &data, &n) < 0) {
        return NULL;
    }
    return PyLong_FromLongLong(mix_crc32(data, (int64_t)n));
}

static PyObject *py_crc32_n(PyObject *self, PyObject *args) {
    char *data = NULL;
    Py_ssize_t n = 0;
    long long times = 0;
    (void)self;
    if (!PyArg_ParseTuple(args, "y#L", &data, &n, &times)) {
        return NULL;
    }
    return PyLong_FromLongLong(mix_crc32_n(data, (int64_t)n, (int64_t)times));
}

static PyMethodDef methods[] = {
    {"crc32", py_crc32, METH_O, "CRC-32 of a bytes value"},
    {"crc32_n", py_crc32_n, METH_VARARGS, "CRC-32, repeated, summed"},
    {"crc32_island", py_crc32, METH_O, "same as crc32; pyscriptc routes this through the island"},
    {"crc32_n_island", py_crc32_n, METH_VARARGS, "same as crc32_n; pyscriptc routes this through the island"},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "nativeprobe",
    "Benchmark extension. Not a supported pyscriptc module.",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit_nativeprobe(void) { return PyModule_Create(&module); }
