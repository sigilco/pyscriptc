%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

@ps.str.0 = private unnamed_addr constant [4 x i8] c"bad\00"

declare i32 @ps_try_call(ptr, ptr, ptr)
declare void @ps_raise(i32) noreturn
declare void @ps_raise_code(i32) noreturn
declare void @ps_raise_msg(i32, i64, ptr) noreturn
declare void @ps_reraise() noreturn
declare i32 @ps_exc_code()
declare i32 @ps_exc_isa(i32, i32)
declare ptr @ps_exc_make(i32, i64, ptr)
declare void @ps_exc_set_cause(ptr, ptr)
declare void @ps_raise_exc(ptr)

define void @raise_cause() {
entry:
  %t1 = alloca i32
  %t2 = call ptr @ps_exc_make(i32 51, i64 0, ptr null)
  %t3 = insertvalue %ps.str poison, i64 3, 0
  %t4 = insertvalue %ps.str %t3, ptr @ps.str.0, 1
  %t5 = insertvalue %ps.str %t4, i64 3, 2
  %t6 = insertvalue %ps.str %t5, i32 1, 3
  %t7 = extractvalue %ps.str %t6, 0
  %t8 = extractvalue %ps.str %t6, 1
  %t9 = call ptr @ps_exc_make(i32 51, i64 %t7, ptr %t8)
  %t10 = call ptr @ps_exc_make(i32 50, i64 0, ptr null)
  call void @ps_exc_set_cause(ptr %t9, ptr %t10)
  call void @ps_raise_exc(ptr %t9) noreturn
  unreachable
}
