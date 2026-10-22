%ps.str = type { i64, ptr }

declare ptr @ps_alloc(i64)

define void @outer() {
entry:
  %t1 = alloca i64
  %t2 = alloca ptr
  %t3 = alloca i32
  store i64 1, ptr %t1
  %t4 = call ptr @ps_alloc(i64 8)
  store ptr %t1, ptr %t4
  %t5 = call ptr @ps_alloc(i64 16)
  store ptr @outer.inner, ptr %t5
  %t6 = getelementptr ptr, ptr %t5, i64 1
  store ptr %t4, ptr %t6
  store ptr %t5, ptr %t2
  ret void
}

define i64 @outer.inner(ptr %env) {
entry:
  %t1 = alloca i64
  %t2 = load ptr, ptr %env
  %t3 = alloca i64
  %t4 = alloca i32
  %t5 = load i64, ptr %t2
  ret i64 %t5
}
