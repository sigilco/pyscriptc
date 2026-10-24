%ps.str = type { i64, ptr, i64, i32 }

declare void @ps_raise_code(i32) noreturn
declare void @ps_raise_zdi(i32) noreturn
declare { i64, i1 } @llvm.sadd.with.overflow.i64(i64, i64)

define internal i64 @ps.iadd(i64 %a, i64 %b) {
entry:
  %r = call { i64, i1 } @llvm.sadd.with.overflow.i64(i64 %a, i64 %b)
  %ov = extractvalue { i64, i1 } %r, 1
  br i1 %ov, label %trap, label %ok
ok:
  %v = extractvalue { i64, i1 } %r, 0
  ret i64 %v
trap:
  call void @ps_raise_code(i32 10) noreturn ; OverflowError
  unreachable
}

define void @binary_add(i64 %a, i64 %b) {
entry:
  %t1 = alloca i64
  %t2 = alloca i64
  %t3 = alloca i64
  %t4 = alloca i32
  store i64 %a, ptr %t1
  store i64 %b, ptr %t2
  %t5 = load i64, ptr %t1
  %t6 = load i64, ptr %t2
  %t7 = call i64 @ps.iadd(i64 %t5, i64 %t6)
  store i64 %t7, ptr %t3
  ret void
}
