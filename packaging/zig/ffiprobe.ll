%ps.str = type { i64, ptr }

%cls.TextIO = type { i8 }
%cls.BytesIO = type { i8 }
@ps.fmt.int = private unnamed_addr constant [6 x i8] c"%lld\0A\00"

declare i32 @printf(ptr, ...)
declare i64 @ffi_add3(i64, i64, i64)
declare void @ps_argv_init(i32, ptr)

define i64 @total() {
entry:
  %t1 = alloca i64
  %t2 = alloca i32
  %t3 = call i64 @ffi_add3(i64 20, i64 21, i64 22)
  ret i64 %t3
}

define i32 @main(i32 %argc, ptr %argv) {
entry:
  call void @ps_argv_init(i32 %argc, ptr %argv)
  %t1 = call i64 @total()
  call i32 (ptr, ...) @printf(ptr @ps.fmt.int, i64 %t1)
  ret i32 0
}
