---
title: Mobile apps
description: Compile Python into a static library and link it into an iOS or Android app.
---

pyscriptc can compile a Python module into a static library that a native iOS or Android app links and calls.
The library mode exists because a phone app has no use for a standalone binary: the app itself is the executable, and the compiled Python rides inside it as `libpyscriptc.a`.

## The three targets

```bash
pyscriptc compile hello.py --emit lib --target aarch64-apple-ios-simulator -o lib
pyscriptc compile hello.py --emit lib --target aarch64-apple-ios -o lib
pyscriptc compile hello.py --emit lib --target aarch64-linux-android -o lib
```

`-o` names a directory that receives three files: `libpyscriptc.a`, `pyscriptc.h` and `module.modulemap`.
The simulator target builds and links from a macOS host with no extra setup.
The device target needs Xcode.
The Android target needs the NDK and refuses to start without one: point `PYSC_ANDROID_NDK` at the NDK root (the directory that contains `toolchains/llvm/prebuilt`), and optionally set `PYSC_ANDROID_API` (24 by default).

## Calling it from the app

The exported surface is exactly two functions, `pyscriptc_init` and `pyscriptc_main`.
Everything else is hidden at compile time (`-fvisibility=hidden`), so the archive cannot leak the runtime namespace into the app's symbol table.

```c
#include "pyscriptc.h"

if (pyscriptc_main() != 0) {
    /* the module raised or exited nonzero */
}
```

`pyscriptc_init()` sets up the per-process runtime state (the argv list is empty in a library); it is idempotent.
`pyscriptc_main()` is the module entry: runtime init, the exception table, the module globals, then the module's own entry. It returns 0 on success.
Call `pyscriptc_init()` yourself when the host drives the runtime without the module entry; `pyscriptc_main()` already calls it.

A Swift app imports the same pair through the `module.modulemap` beside the archive.
Link the archive into the app target, add the header search path, and `import pyscriptc` gives both functions.

## What a mobile target refuses

- `--emit bin` and `--run` fail on every mobile triple with `PS4101` (see `pyscriptc explain PS4101`), and `--emit lib` fails on a desktop triple the same way.
- `--driver zig` is refused for a library build: zig bundles neither bionic nor the Apple SDKs, so the build compiles through clang with an explicit sysroot.
- `subprocess`, `os.open` and the other raw file-descriptor calls stop the compile with the mobile reason (`PS2001`), because the runtime files that implement them are left out of the mobile archive. A call that compiled anyway would only fail later, as an undefined symbol at link.

## Android page-size rule

An Android app links the archive into a shared object, and that link carries the 16 KB alignment flags Google Play requires for Android 15+:

```bash
-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384
```

The archive itself has no program headers, so the flags belong to the consumer's link line.
`pyscriptc` prints this reminder after every Android archive build.

## Not in the library yet

Distribution packaging (XCFramework, AAR, SPM binary target), generated Swift and Kotlin bindings, and host callbacks are separate follow-up work; this page documents the archive and its calling convention.
