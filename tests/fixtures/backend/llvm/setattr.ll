%ps.str = type { i64, ptr }

%cls.Point = type { i64 }

define void @setattr_stmt() {
entry:
  %t1 = alloca ptr
  %t2 = alloca i32
  %t3 = load ptr, ptr %t1
  %t4 = getelementptr %cls.Point, ptr %t3, i32 0, i32 0
  store i64 1, ptr %t4
  ret void
}
