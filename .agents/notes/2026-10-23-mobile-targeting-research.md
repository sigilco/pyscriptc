# Mobile targeting research: iOS and Android for pyscriptc

Date: 2026-10-23. Status: research note, no ticket opened from this file alone. The plan that consumes it is `.agents/plans/2026-10-23-mobile-targeting.md` (local, gitignored).

Scope: what it would take to let an iOS (Swift) or Android (Kotlin) app call pyscriptc-compiled Python, what the platform rules allow, what the options are, and how they rank on maintainer effort, integrator effort and runtime performance.

Sources: Apple App Review Guidelines and the Apple Developer Program License Agreement (retrieved 2026-10-23), Google Play Device and Network Abuse and Malware policies, PEP 730 (iOS) and PEP 738 (Android), the CPython iOS and Android docs, the Vercel Labs scriptc repository and docs (v0.0.29 release, platforms page), the Wasmtime, WAMR, wasm3, wasmi and WasmEdge platform docs, the Pyodide and componentize-py repositories, the UniFFI, gomobile, Chaquopy, BeeWare, Kivy and KMP documentation, and our own tree (runtime, emit driver, CLI, FFI manifest).

## 1. What we already have

The repository audit matters more than any external fact for this decision.

Our runtime is 17.4k lines of plain portable C under `src/pyscriptc/runtime/`. It uses `malloc`/`calloc`/`realloc`/`free` only, no GC, no C++ runtime, no platform framework. Per-thread interpreter state (`ps_top`, `ps_current` in `ps_rt.c:22-23`) is already `_Thread_local`, which means a mobile host can call into compiled code from any thread without a global lock.

Cross-compilation machinery exists. `link_binary` in `src/pyscriptc/emit/llvm/_driver.py:73` already accepts a zig triple, compiles every runtime `.c` to a per-target object through `rtcache`, and links. The zig driver is a first-class option (`--driver zig`, `PYSC_CC`), and `zig cc` is already a required CI check.

Debug info exists. `--debug-info` emits DWARF with Python source line tables (`src/pyscriptc/dwarf.py`), and the link step runs `dsymutil` on Darwin. Mobile source-level breakpoints are therefore mostly "already done", unlike most AOT compilers.

A typed extern surface exists. `src/pyscriptc/ffi_manifest.py` parses a TOML manifest that binds a Python-visible module surface to C ABI symbols, with a `ps_abi` epoch and handles with `frees`. This is the natural seed for generated Swift and Kotlin bindings.

Entry synthesis exists. The lower stage builds an entry function from module-level code (`__entry__`), and `config.entry` selects one.

Nothing mobile exists yet. A repository-wide search for `wasm`, `ios`, `android`, `mobile`, `xcode` and `gradle` over the source, tests, docs and CI returns no hits. Issue #312 (`feat: wasm32-wasi target`) is open in the v0.6.0 public milestone and is the only adjacent work.

## 2. Platform rules that decide the shape

### iOS

No JIT. App Store apps may not create writable-and-executable memory. `mmap` with `MAP_JIT` needs the `com.apple.security.cs.allow-jit` entitlement, which is macOS-only and not available to App Store iOS apps. So every code-generating backend must be AOT-at-build-time or interpreter-only. This is a hard architectural constraint, not a policy preference.

Downloaded code. Guideline 2.5.2 says apps "may not download, install, or execute code which introduces or changes features or functionality of the app". The Apple Developer Program License Agreement section 3.3.1 B ("Executable Code") is the operative clause; the old "3.3.2" that vendors cite is now "Regulatory Compliance" and says nothing about code. Interpreted code may be downloaded if it does not change the app's primary purpose, does not bypass sandbox or signing, and creates no storefront. Guideline 4.7 is a narrow permission for HTML5 and JavaScript mini apps, streaming games, chatbots, plug-ins and retro console emulator games. It is not a general scripting-runtime permission.

The good news: bundling AOT-compiled code in the app is the most compliant artifact possible. No download, no JIT, no embedded third-party dylib. CPython itself needed a patch (`Mac/Resources/app-store-compliance.patch`, auto-applied on iOS builds) because Apple's automated validation rejects on stdlib strings such as `itms-services`. We do not ship a stdlib, so we do not inherit that class of problem.

Packaging. Every binary extension module on iOS must be its own signed dynamic framework in the app's `Frameworks` folder, with `.fwork` marker files and an `AppleFrameworkLoader`. That is why PEP 730 rejected static libpython: extension modules rely on `-undefined dynamic_lookup`, which is deprecated on iOS. A pure static archive with no dynamic extension modules sidesteps the whole rule, which is exactly the shape we would ship.

Static archive mechanics that matter to us. ld64 pulls an archive member only when it resolves an undefined symbol, so an object whose only contribution is an initializer is silently dropped, with no link error (the mechanism documented at github.com/mmastrac/linktime/issues/496). Our design avoids it: the emitted code references runtime symbols directly, so members are pulled by reference. We should still avoid constructor-dependent initialization and expose an explicit init entry point. Symbol collisions are the other trap: visibility must be set at our compile time with `-fvisibility=hidden` plus a small exported list, because a consumer cannot retroactively hide symbols in a finished archive. ld64 has no `--exclude-libs,ALL`; the Apple equivalents are `-load_hidden` and `-exported_symbols_list`.

Build flags. Device slice: `clang -target arm64-apple-ios15.0 -isysroot $(xcrun --sdk iphoneos --show-sdk-path)`. Simulator slice: `arm64-apple-ios15.0-simulator` (the `-simulator` suffix is mandatory) and `x86_64-apple-ios15.0-simulator`. A single XCFramework holds both. The deployment target lands in `LC_BUILD_VERSION`, and the linker refuses to mix a device object into a simulator link. Bitcode is dead since Xcode 14.

Debugging. Xcode links DWARF from static-archive members into the app binary, and `dsymutil` produces the dSYM. Line tables are the hard part and we already emit them. Variable inspection in a non-native language needs an LLDB data formatter plugin; `xcode-kotlin` is the precedent (an LLDB plugin plus a language definition, installed per Xcode version, that lets you set breakpoints in Kotlin source inside an iOS app).

### Android

JIT is fine. ART has a first-class JIT and `ASharedMemory_create` can be mapped `PROT_READ|PROT_WRITE|PROT_EXEC`. The real constraint is that executable code must live in the APK: apps targeting Android 10 cannot execute files from the writable app home directory. `dlopen` of a bundled `.so` is fine.

Play policy. Downloading dex, JAR or `.so` files from outside Play is banned outright. Downloading interpreted code is permitted only insofar as it cannot produce policy violations; the policy names JavaScript, Python and Lua explicitly. Bundling an interpreter or a compiled library is unambiguously fine. The failure mode to avoid is a backend that downloads and executes operator-controlled code with device permissions, which is "backdoor" under the Malware policy.

16 KB page sizes. Since 2025-11-01 every new app and update targeting Android 15+ on Play must support 16 KB pages on 64-bit devices, with a hard cutoff of 2027-02-01 for updates that do not. AGP 8.5.1+ and NDK r28+ produce 16 KB-compatible output by default; older toolchains need `-Wl,-z,max-page-size=16384 -Wl,-z,common-page-size=16384`. A static archive has no ELF program headers of its own, so it inherits the consumer's alignment; a prebuilt `.so` must be aligned by us and re-verified on every rebuild. Verify with `llvm-readelf -l` on the `.so` and `zipalign -c -P 16` on the APK.

Static libpython is a dead end on Android. PEP 738 states it explicitly: extension modules must link a shared `libpython3.x.so`, and Android's dlopen semantics do not let a dlopened library satisfy relocations of later-loaded libraries even with `RTLD_GLOBAL`. A static `libpython3.x.a` is not supported. This is why the island tier refuses cross-compilation today (`_driver.py:113`) and why that refusal is correct.

Floors and packaging. minSdk 24 is a defensible modern floor (over 96 percent device coverage, matching the AndroidX default); 26 costs little more reach and matches what scriptc chose. From 2026-08-31 new apps must target API 36. Google's own middleware-vendor guidance for a product SDK is: ship exactly one JNI `.so`, statically link every dependency including libc++, export only `JNI_OnLoad` through a version script, and use `JNI_OnLoad` plus `RegisterNatives` so the ABI surface stays minimal. Prefab (the `prefab/` directory in an AAR) is the supported way for a C consumer to link our static library from Gradle.

## 3. The option ladder

| Option | Shape | Maintainer effort | Integrator effort | Performance |
| --- | --- | --- | --- | --- |
| A | `--emit lib`: static archive per mobile triple, C ABI only | S | L (raw C ABI) | native, best possible |
| B | A plus generated Swift and Kotlin bindings | M | S to M | native plus nanosecond-scale marshalling |
| C | B plus distribution: XCFramework, AAR, SPM binary target, Gradle plugin | M to L | S | same as B |
| D | wasm32-wasi (issue #312) embedded in-app via wasm3 or WAMR | M | M to L | 2x to 20x slower than native |
| E | Island on mobile: embed CPython, call the C API | L | M | interpreter speed, 70 to 100 MB app growth |
| F | Platform tuning: multi-instance, size builds, 16 KB arenas, per-thread instances | M per item | S | raises the ceiling of B |

### A. Static library mode

The market-proven minimal shape is Vercel Labs scriptc, which shipped exactly this as their entire mobile story in v0.0.29 (2026-08-13): `SCRIPTC_TARGET=aarch64-apple-ios`, `aarch64-apple-ios-simulator` and `aarch64-linux-android` with `--lib --profile app.profile.json`. The profile declares exported functions and host callbacks, plus `abi.localize_runtime` for several independent instances in one app and `abi.instance_per_thread`. Standalone executable builds refuse the mobile triples with a diagnostic pointing at `--lib`. Library builds do not support async functions, promises or timers. iOS builds need a macOS host with the Xcode iPhoneOS SDK; Android builds work from any host with the NDK.

For us the work is: a `--emit lib` mode that stops after compiling the `.ll` to objects and archiving them, an exported header plus `module.modulemap` for Swift, a diagnostic that refuses executable builds for mobile triples, and runtime feature fences for the host-only pieces (`ps_sub.c` with fork and exec, `ps_osfd.c`, signal handling). Zig already targets all three triples; the Apple target additionally needs the Xcode SDK sysroot, which zig cannot supply.

The library-mode restriction to synchronous code is not a limitation for us in the first iteration: our async runtime (`coro.py`, 40.5k) is desktop-only work today, and sync exports are what a mobile SDK needs first anyway.

### B. Generated bindings

This is where mobile developers decide whether the product is usable. The proven pattern is Mozilla's UniFFI, used in production by Bitwarden, Stadia Maps' Ferrostar and Turso: declare the surface once, generate idiomatic Swift and Kotlin, freeze a C ABI underneath. The components we need:

A generated header and `module.modulemap`. Swift imports a C library only through a clang module; there is no `.swiftmodule` for a C library, and a bridging header works only inside an app target, not from a distributed package.

A Swift wrapper. `str` to `String`, `bytes` to `Data`, and exceptions to typed `Error` enums. Our exception code table in `ps_rt.h` is already a parent-walk ISA table with reserved ranges, which maps cleanly onto an error enum with a catch-all case.

A Kotlin/JNI wrapper following Google's middleware-vendor recipe: one `.so`, `JNI_OnLoad` plus `RegisterNatives`, a version script exporting only `JNI_OnLoad`, direct `ByteBuffer` for bulk data. The rule of thumb from the JNI literature is "chunky over chatty": a fixed toll per crossing plus a per-byte copy cost, so the boundary carries whole buffers, not tokens. A thin Kotlin facade should convert load failures into a Kotlin exception instead of surfacing `UnsatisfiedLinkError`.

The UniFFI detail worth copying: embed the API description inside the compiled artifact and read it back with a `bindgen --library` step, so generated bindings always match the exact binary and version skew becomes impossible. Our `ps_abi` epoch in the FFI manifest is the seed for that.

Async across the boundary comes later. The pattern that works without requiring a runtime on our side is "foreign side owns the executor, core exposes a future handle": Swift wraps it in a `Task` with a `CheckedContinuation`, Kotlin in `suspend` with `suspendCancellableCoroutine`. Until then, sync exports called from any thread are enough, because our per-thread runtime state is already `_Thread_local`.

Host callbacks (the host calling back into Python) come after that, and are the scriptc `callbacks` plus `abi.callback_register_symbol` mechanism equivalent. Our FFI manifest already has the shape for declaring host-provided symbols.

### C. Distribution

The bar is "one package dependency, no manual steps". On iOS: a signed `pyscriptc.xcframework.zip` with device and simulator slices, headers and a modulemap, consumed through an SPM `binaryTarget`. Static frameworks need no App Store signing ceremony (Apple DTS: recommended, not required), and a static XCFramework never needs to be embedded and re-signed. On Android: an AAR with `jniLibs/arm64-v8a` and a Prefab module for C consumers, published to Maven Central with Gradle Module Metadata, consumer ProGuard rules and GPG signatures. The higher tier is a Gradle plugin that runs pyscriptc during the app build (the protobuf-gradle-plugin shape: the compiler downloaded from Maven with an OS and arch classifier, per-variant tasks, explicit inputs and outputs for cacheability) and an SPM build tool plugin (the swift-protobuf shape). Both are worth doing only after B has users.

### D. The wasm path

Issue #312 gives one artifact embeddable in iOS, Android, servers and browsers. On iOS the only legal engines are interpreter-only: wasm3, WAMR, wasmi, WasmKit, or Wasmtime's Pulley interpreter (roughly 10x slower than native). Cranelift, LLVM, Singlepass and Winch all need executable memory and are unavailable on iOS. The costs are real: the runtime needs a WASI compatibility pass, mobile wasm bindings are thin to abandoned (WasmKit is the one maintained Swift option, WasmEdge's Java binding was deprecated, wasmer-java still points at dead Bintray), and interpreted wasm costs 2x to 20x. The strategic value is universality and sandboxing, not mobile performance. Build it because #312 is planned; do not sell it as the mobile story.

### E. Why embedding CPython is out

PEP 730 and PEP 738 made CPython tier 3 on both platforms, which is why Chaquopy, BeeWare and Kivy can ship today. The costs that rule it out for us: static libpython is impossible on Android and every binary module needs its own signed framework on iOS; app growth is 70 to 100 MB for a minimal embed (Pythonista is 894 MB, a reported minimal CPython embed adds about 100 MB); startup is about 2 seconds for `Python.start()` on a mid-range device (Chaquopy's own figure) plus seconds per heavy import; and the 150 MB Play Store limit is a recurring complaint for anyone adding scientific packages. This is the incumbent pyscriptc displaces, not a path we take.

### F. Tuning, later

Multi-instance support (scriptc's `abi.localize_runtime` runs the host linker to localize the runtime per instance; on Android it reuses the in-process ELF localization from the Linux cross targets), size-optimized builds, 16 KB-aware allocation (our runtime uses plain `malloc`, so it inherits the consumer's allocator, but any arena or `mmap` we add later must query `getpagesize`), and per-thread instances (cheap for us because the state is already `_Thread_local`).

## 4. The competitive picture

The embedded-scripting options mobile developers actually choose: JavaScriptCore (free on iOS, JIT disabled in third-party apps, about 7.5x slower than with JIT), Hermes (RN default, AOT bytecode, no JIT), QuickJS and quickjs-ng (small pure interpreters, roughly 350 KB per ABI), Lua and Luau (games), Wren, and Rhinos. The measured ordering from the Lua workshop benchmark is C, then LuaJIT, then JSC with JIT, then Lua, then JSC without JIT.

Python on mobile today: Chaquopy (Gradle plugin, Android only, no iOS support, MIT and free since 2022), Briefcase and BeeWare (full app packaging, iOS XCFramework and Android AAR, debugpy breakpoints landed in November 2025), Kivy and python-for-android, Flet on Flutter. Their recurring complaints are size, startup, and the iOS packaging tax. Nobody reaches for Python first because the interpreter tax is 10x to 30x the JS or Lua tax.

Cross-platform frameworks that compile one language to two native apps: Kotlin Multiplatform (native binaries, closest competitor for "one language, two native apps"), Flutter and Dart (AOT snapshot, engine as a shared library), React Native (Hermes bytecode), Tauri v2 (Rust core), .NET MAUI (Mono AOT), Rust with UniFFI and cargo-ndk, Go with gomobile, Zig (a real production precedent: a mobile VPN client ships about 500 KB arm64 with no bindings layer).

Prior art for "compile a language to a native library for mobile embedding": UniFFI (the reference), gomobile, Zig C interop, .NET NativeAOT (class libraries as native shared libraries for iOS since .NET 9), Gobley, WasmEdge's C SDK, PyObjC, and SwiftPython (a closed-source commercial preview). mocl (Common Lisp compiled to C for iOS and Android, per-license pricing since 2013) is the closest precedent for selling this, and it is effectively dormant.

Market reality: no one sells "compile Python to native for mobile" today. Charging for the compiler and bindings is hard because Chaquopy, Briefcase, Kivy, UniFFI, gomobile and Zig are all free. The money in the Rust plus FFI space is in the SDKs built on top of it (Bitwarden, Ferrostar, Turso, bitdrift), not in the toolchain. That is a signal for where a mobile effort should be aimed: not at selling a compiler, but at the developer experience around it.

## 5. Performance expectations

The honest ceiling for an AOT-compiled dynamic language on mobile is visible in Static Hermes: the same benchmark compiled to C with clang -O3 ran 278 ms, through the Static Hermes native path 266 ms, and through the JIT 406 ms. Static Hermes matches C. So a well-compiled Python subset can land within roughly 2x of Swift or Kotlin on logic workloads, against 10x to 30x for the interpreter incumbents.

Our specific advantages on device: native arm64 code with no JIT (so it is legal on iOS where LuaJIT and JSC-with-JIT are not), nanosecond-scale boundary crossings if we keep the calls coarse, and `_Thread_local` runtime state meaning thread-per-call concurrency without a global lock on the hot path.

Our specific disadvantages: reference counting instead of GC (cycles leak, unlike CPython), no JIT tier, and a smaller surface than CPython, so the "run my existing Python" promise is bounded by the compat2 corpus.

## 6. What we do not know yet

Whether zig cc can consume the Xcode iPhoneOS SDK sysroot directly, or whether the iOS path needs clang with `-isysroot` instead of zig. Whether our runtime compiles cleanly under bionic (it is plain C with pthreads, but `ps_sub.c` and `ps_osfd.c` need attention). The exact size and first-call latency of a compiled library on a real device, which is the number that decides whether the pitch holds. Whether an LLDB formatter plugin is worth the maintenance, or whether line tables alone are enough for mobile developers to accept the debugging story. Whether the Android 16 KB alignment survives our `rtcache` object reuse (cached objects are per-target, so they must key on the alignment flags). None of these need research; they need the three spikes.

## 7. Sources

Apple App Review Guidelines (2.5.2, 3.3.1 B, 4.7), https://developer.apple.com/app-store/review/guidelines/

Apple Developer Program License Agreement section 3.3.1 B, https://developer.apple.com/support/terms/apple-developer-program-license-agreement/

`com.apple.security.cs.allow-jit` entitlement (macOS only), https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.security.cs.allow-jit

Using Python on iOS (CPython docs, section 7 packaging and 7.3.1 App Store compliance), https://docs.python.org/3/using/ios.html

Using Python on Android (CPython docs), https://docs.python.org/3/using/android.html

PEP 730, Adding iOS as a supported platform, https://peps.python.org/pep-0730/

PEP 738, Adding Android as a supported platform, https://peps.python.org/pep-0738/

Google Play Device and Network Abuse policy, https://support.google.com/googleplay/android-developer/answer/9888379

Google Play Malware policy, https://support.google.com/googleplay/android-developer/answer/9888380

Android 16 KB page sizes, https://developer.android.com/guide/practices/page-sizes

Android middleware vendor guide (one JNI `.so`, version script, `JNI_OnLoad`), https://developer.android.com/ndk/guides/middleware-vendors

Google Prefab, https://google.github.io/prefab/

AGP native dependencies, https://developer.android.com/build/native-dependencies

Apple, Creating a multiplatform binary framework bundle (XCFramework), https://developer.apple.com/documentation/xcode/creating-a-multi-platform-binary-framework-bundle

Apple, Creating a static framework, https://developer.apple.com/documentation/xcode/creating-a-static-framework

ld(1) man page (archive member selection, `-force_load`, `-load_hidden`, `-exported_symbols_list`), https://keith.github.io/xcode-man-pages/ld.1.html

Static initializer loss in a Darwin static archive, https://github.com/mmastrac/linktime/issues/496

Apple TN2435 (static libraries do not go in Embed Frameworks), https://developer.apple.com/library/archive/technotes/tn2435/_index.html

SwiftPM binary targets (SE-0272), https://github.com/apple/swift-evolution/blob/main/proposals/0272-swiftpm-binary-dependencies.md

SwiftPM plugins (SE-0303, sandbox and PATH rules), https://docs.swift.org/swiftpm/documentation/packagemanagerdocs/plugins/

dsymutil and DWARF in static archives, https://developer.apple.com/videos/play/wwdc2021/10211/

LLDB source maps for mismatched build paths, https://developer.apple.com/videos/play/wwdc2022/110370/

xcode-kotlin (LLDB plugin for a non-native language), https://touchlab.co/xcode-kotlin-2-0

CocoaPods podspec (`vendored_libraries`, `vendored_frameworks`, `static_framework`), https://guides.cocoapods.org/syntax/podspec.html

Carthage (dynamic frameworks only officially), https://github.com/Carthage/Carthage/blob/master/README.md

Wasmtime platform support (Pulley interpreter, Cranelift and Winch unavailable on iOS), https://docs.wasmtime.dev/stability-platform-support.html

Wasmtime Pulley performance, https://docs.wasmtime.dev/examples-pulley.html

Wasmtime iOS memory reservation issue, https://github.com/bytecodealliance/wasmtime/issues/12251

WAMR (interpreter and AOT, sizes), https://github.com/wasm-micro-runtime/wasm-micro-runtime

wasm3 (MIT interpreter, no JIT), https://github.com/wasm3/wasm3

wasmi (MIT or Apache-2.0 interpreter), https://github.com/wasmi-labs/wasmi

WasmEdge backend availability, https://docs.wasmer.io/runtime/features/

Pyodide constraints (no threads, no sockets), https://pyodide.org/en/stable/usage/wasm-constraints.html

Pyodide in a shipped iOS app (5 to 15 s first launch), https://blog.thecoatlessprofessor.com/posts/python-in-your-pocket-with-pyodios/

componentize-py (size of a Python wasm component), https://github.com/bytecodealliance/componentize-py/issues/28

Vercel Labs scriptc, Platform Support (mobile triples, library mode, cross-target limits), https://scriptc.dev/docs/platforms

scriptc v0.0.29 release notes (library archives for iOS and Android), https://github.com/vercel-labs/scriptc/releases/tag/v0.0.29

UniFFI Swift and Kotlin overview, errors and futures, https://mozilla.github.io/uniffi-rs/latest/swift/overview.html

gomobile (Go to Java and Objective-C), https://go.dev/wiki/Mobile

Chaquopy Android documentation and FAQ (no iOS support), https://chaquo.com/chaquopy/doc/current/android.html

Chaquopy startup issue (about 2 s on a mid-range device), https://github.com/chaquo/chaquopy/issues/653

BeeWare Python-Apple-support (XCFramework builds), https://github.com/beeware/Python-Apple-support

Briefcase debugpy support for mobile (November 2025), https://beeware.org/news/buzz/2025/november-2025-status-update/

Kotlin Multiplatform iOS integration, https://kotlinlang.org/docs/multiplatform/multiplatform-ios-integration-overview.html

protobuf-gradle-plugin (the Gradle plugin that drives a compiler), https://github.com/google/protobuf-gradle-plugin

swift-protobuf build plugin (the SPM plugin that drives a compiler), https://github.com/apple/swift-protobuf/blob/main/Plugins/SwiftProtobufPlugin/plugin.swift

Google, Write Gradle plugins, https://developer.android.com/build/extend-agp

Gradle incremental build requirements, https://docs.gradle.org/current/userguide/incremental_build.html

Google, Upload your library (Maven Central is best practice), https://developer.android.com/build/publish-library/upload-library

Maven Central publishing requirements, https://central.sonatype.org/publish/requirements/

Ferrostar, building a cross-platform navigation SDK in Rust, part 1 and part 2, https://stadiamaps.com/blog/ferrostar-building-a-cross-platform-navigation-sdk-in-rust-part-1/

Bitwarden SDK language bindings, https://github.com/bitwarden/contributing-docs/blob/main/docs/architecture/sdk/language-bindings.md

Turso mobile SDKs for iOS and Android, https://turso.tech/blog/turso-goes-mobile-with-official-ios-and-android-sdks

Hermes issue 429 (Static Hermes native path matches clang -O3), https://github.com/facebook/hermes/issues/429

Hermes as the RN default (bundle and TTI numbers), https://reactnative.dev/blog/2021/10/26/toward-hermes-being-the-default

QuickJS-ng, https://github.com/quickjs-ng/quickjs

Lua workshop benchmark (C, LuaJIT, JSC with and without JIT), https://www.lua.org/wshop16/Wing2.pdf

Zig platform support (aarch64-ios, aarch64-linux-android), https://ziglang.org/learn/platform-support/

mocl (Common Lisp to C for iOS and Android), https://web.archive.org/web/20121117183137/http:/wukix.com/mocl

Nuitka Android support status, https://github.com/Nuitka/Nuitka/issues/3254
