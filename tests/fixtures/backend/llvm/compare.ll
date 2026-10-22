%ps.str = type { i64, ptr }

define void @compare_lt(i64 %a, i64 %b) {
entry:
  %t1 = alloca i64
  %t2 = alloca i64
  %t3 = alloca i1
  %t4 = alloca i32
  store i64 %a, ptr %t1
  store i64 %b, ptr %t2
  %t5 = load i64, ptr %t1
  %t6 = load i64, ptr %t2
  %t7 = icmp slt i64 %t5, %t6
  store i1 %t7, ptr %t3
  ret void
}
