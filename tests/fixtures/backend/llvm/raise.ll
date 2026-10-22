%ps.str = type { i64, ptr }

@ps.str.0 = private unnamed_addr constant [4 x i8] c"bad\00"

declare i32 @ps_try_call(ptr, ptr, ptr)
declare void @ps_raise(i32) noreturn
declare void @ps_raise_code(i32) noreturn
declare void @ps_raise_msg(i32, i64, ptr) noreturn
declare void @ps_reraise() noreturn
declare i32 @ps_exc_code()
declare i32 @ps_exc_isa(i32, i32)

define void @raise_stmt() {
entry:
  %t1 = alloca i32
  %t2 = insertvalue %ps.str poison, i64 3, 0
  %t3 = insertvalue %ps.str %t2, ptr @ps.str.0, 1
  %t4 = extractvalue %ps.str %t3, 0
  %t5 = extractvalue %ps.str %t3, 1
  call void @ps_raise_msg(i32 51, i64 %t4, ptr %t5) noreturn
  unreachable
}
