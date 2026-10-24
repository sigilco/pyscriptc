%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

define void @name_expr() {
entry:
  %t1 = alloca i64
  %t2 = alloca i64
  %t3 = alloca i32
  %t4 = load i64, ptr %t1
  store i64 %t4, ptr %t2
  ret void
}
