%ps.str = type { i64, ptr }

%cls.MyError = type { i8 }
@ps.str.0 = private unnamed_addr constant [8 x i8] c"MyError\00"

declare i32 @ps_try_call(ptr, ptr, ptr)
declare void @ps_raise(i32) noreturn
declare void @ps_raise_code(i32) noreturn
declare void @ps_raise_msg(i32, i64, ptr) noreturn
declare void @ps_reraise() noreturn
declare i32 @ps_exc_code()
declare i32 @ps_exc_isa(i32, i32)
declare void @ps_argv_init(i32, ptr)
declare void @ps_exc_register(i32, i32, ptr)

define void @raises_custom() {
entry:
  %t1 = alloca i32
  call void @ps_raise_code(i32 900) noreturn
  unreachable
}

define i32 @main(i32 %argc, ptr %argv) {
entry:
  call void @ps_argv_init(i32 %argc, ptr %argv)
  %t1 = insertvalue %ps.str poison, i64 7, 0
  %t2 = insertvalue %ps.str %t1, ptr @ps.str.0, 1
  %t3 = extractvalue %ps.str %t2, 0
  %t4 = extractvalue %ps.str %t2, 1
  call void @ps_exc_register(i32 900, i32 0, ptr %t4)
  call void @raises_custom()
  ret i32 0
}
