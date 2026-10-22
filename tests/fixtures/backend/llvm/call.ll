%ps.str = type { i64, ptr }

define void @call_helper() {
entry:
  %t1 = alloca i64
  %t2 = alloca i32
  %t3 = call i64 @helper()
  store i64 %t3, ptr %t1
  ret void
}

define i64 @helper() {
entry:
  %t1 = alloca i64
  %t2 = alloca i32
  ret i64 0
}
