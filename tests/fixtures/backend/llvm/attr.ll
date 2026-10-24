%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

%cls.Point = type { i64 }

define void @attr_expr() {
entry:
  %t1 = alloca ptr
  %t2 = alloca i64
  %t3 = alloca i32
  %t4 = load ptr, ptr %t1
  %t5 = getelementptr %cls.Point, ptr %t4, i32 0, i32 0
  %t6 = load i64, ptr %t5
  store i64 %t6, ptr %t2
  ret void
}
