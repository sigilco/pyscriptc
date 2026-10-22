%ps.str = type { i64, ptr }

define void @assign_int() {
entry:
  %t1 = alloca i64
  %t2 = alloca i32
  store i64 1, ptr %t1
  ret void
}
