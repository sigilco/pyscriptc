%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

declare ptr @ps_alloc(i64)

define void @call_value() {
entry:
  %t1 = alloca i64
  %t2 = alloca ptr
  %t3 = alloca i64
  %t4 = alloca i32
  %t5 = call ptr @ps_alloc(i64 8)
  store ptr %t1, ptr %t5
  %t6 = call ptr @ps_alloc(i64 16)
  store ptr @call_value.inner, ptr %t6
  %t7 = getelementptr ptr, ptr %t6, i64 1
  store ptr %t5, ptr %t7
  store ptr %t6, ptr %t2
  %t8 = load ptr, ptr %t2
  %t9 = load ptr, ptr %t8
  %t10 = getelementptr ptr, ptr %t8, i64 1
  %t11 = load ptr, ptr %t10
  %t12 = call i64 %t9(ptr %t11)
  store i64 %t12, ptr %t3
  ret void
}

define i64 @call_value.inner(ptr %env) {
entry:
  %t1 = alloca i64
  %t2 = load ptr, ptr %env
  %t3 = alloca i64
  %t4 = alloca i32
  %t5 = load i64, ptr %t2
  ret i64 %t5
}
