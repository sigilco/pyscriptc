%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

@ps.fmt.int = private unnamed_addr constant [5 x i8] c"%lld\00"
@ps.fmt.nl = private unnamed_addr constant [2 x i8] c"\0A\00"

declare i32 @printf(ptr, ...)
declare void @ps_argv_init(i32, ptr)

define void @ps.user.main() {
entry:
  %t1 = alloca i32
  call i32 (ptr, ...) @printf(ptr @ps.fmt.int, i64 1)
  call i32 (ptr, ...) @printf(ptr @ps.fmt.nl)
  ret void
}

define i32 @main(i32 %argc, ptr %argv) {
entry:
  call void @ps_argv_init(i32 %argc, ptr %argv)
  call void @ps.user.main()
  ret i32 0
}
