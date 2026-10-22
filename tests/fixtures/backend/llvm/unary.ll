%ps.str = type { i64, ptr }

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

define void @unary_neg() {
entry:
  %t1 = alloca i64
  %t2 = alloca i32
  %t3 = call i64 @ps.ineg(i64 3)
  store i64 %t3, ptr %t1
  ret void
}
