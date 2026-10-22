%ps.str = type { i64, ptr }

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

define void @while_stmt(i64 %n) {
entry:
  %t1 = alloca i64
  %t2 = alloca i64
  %t3 = alloca i32
  store i64 %n, ptr %t1
  br label %L1.while.cond
L1.while.cond:
  %t4 = load i64, ptr %t2
  %t5 = load i64, ptr %t1
  %t6 = icmp slt i64 %t4, %t5
  br i1 %t6, label %L2.while.body, label %L3.while.end
L2.while.body:
  %t7 = load i64, ptr %t2
  %t8 = call i64 @ps.iadd(i64 %t7, i64 1)
  store i64 %t8, ptr %t2
  br label %L1.while.cond
L3.while.end:
  ret void
}
