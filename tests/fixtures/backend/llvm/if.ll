%ps.str = type { i64, ptr }

define void @if_stmt(i64 %a, i64 %b) {
entry:
  %t1 = alloca i64
  %t2 = alloca i64
  %t3 = alloca i64
  %t4 = alloca i32
  store i64 %a, ptr %t1
  store i64 %b, ptr %t2
  %t5 = load i64, ptr %t1
  %t6 = load i64, ptr %t2
  %t7 = icmp slt i64 %t5, %t6
  br i1 %t7, label %L1.then, label %L2.else
L1.then:
  store i64 1, ptr %t3
  br label %L3.merge
L2.else:
  store i64 2, ptr %t3
  br label %L3.merge
L3.merge:
  ret void
}
