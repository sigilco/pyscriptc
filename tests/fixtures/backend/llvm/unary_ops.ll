%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

declare void @ps_raise_code(i32) noreturn
declare void @ps_raise_zdi(i32) noreturn
declare { i64, i1 } @llvm.ssub.with.overflow.i64(i64, i64)

define internal i64 @ps.ineg(i64 %a) {
entry:
  %r = call { i64, i1 } @llvm.ssub.with.overflow.i64(i64 0, i64 %a)
  %ov = extractvalue { i64, i1 } %r, 1
  br i1 %ov, label %trap, label %ok
ok:
  %v = extractvalue { i64, i1 } %r, 0
  ret i64 %v
trap:
  call void @ps_raise_code(i32 10) noreturn ; OverflowError
  unreachable
}

define void @unary_ops() {
entry:
  %t1 = alloca i64
  %t2 = alloca i1
  %t3 = alloca i64
  %t4 = alloca i32
  %t5 = call i64 @ps.ineg(i64 3)
  store i64 %t5, ptr %t1
  %t6 = xor i1 true, true
  store i1 %t6, ptr %t2
  %t7 = xor i64 3, -1
  store i64 %t7, ptr %t3
  ret void
}
