%ps.str = type { i64, ptr }

@ps.str.0 = private unnamed_addr constant [2 x i8] c"a\00"
@ps.str.1 = private unnamed_addr constant [2 x i8] c"b\00"

declare void @ps_raise_code(i32) noreturn
declare void @ps_raise_zdi(i32) noreturn
declare { i64, i1 } @llvm.sadd.with.overflow.i64(i64, i64)
declare { i64, i1 } @llvm.ssub.with.overflow.i64(i64, i64)
declare { i64, i1 } @llvm.smul.with.overflow.i64(i64, i64)
declare ptr @malloc(i64)
declare void @llvm.memcpy.p0.p0.i64(ptr, ptr, i64, i1)

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

define internal i64 @ps.isub(i64 %a, i64 %b) {
entry:
  %r = call { i64, i1 } @llvm.ssub.with.overflow.i64(i64 %a, i64 %b)
  %ov = extractvalue { i64, i1 } %r, 1
  br i1 %ov, label %trap, label %ok
ok:
  %v = extractvalue { i64, i1 } %r, 0
  ret i64 %v
trap:
  call void @ps_raise_code(i32 10) noreturn ; OverflowError
  unreachable
}

define internal i64 @ps.imul(i64 %a, i64 %b) {
entry:
  %r = call { i64, i1 } @llvm.smul.with.overflow.i64(i64 %a, i64 %b)
  %ov = extractvalue { i64, i1 } %r, 1
  br i1 %ov, label %trap, label %ok
ok:
  %v = extractvalue { i64, i1 } %r, 0
  ret i64 %v
trap:
  call void @ps_raise_code(i32 10) noreturn ; OverflowError
  unreachable
}

define internal i64 @ps.floordiv(i64 %a, i64 %b) {
entry:
  %zero = icmp eq i64 %b, 0
  br i1 %zero, label %zdiv, label %nonzero
nonzero:
  %min = icmp eq i64 %a, -9223372036854775808
  %neg1 = icmp eq i64 %b, -1
  %bad = and i1 %min, %neg1
  br i1 %bad, label %ovf, label %divide
divide:
  %q = sdiv i64 %a, %b
  %r = srem i64 %a, %b
  %rnz = icmp ne i64 %r, 0
  %aneg = icmp slt i64 %a, 0
  %bneg = icmp slt i64 %b, 0
  %diff = icmp ne i1 %aneg, %bneg
  %adjust = and i1 %rnz, %diff
  br i1 %adjust, label %dec, label %keep
dec:
  %q2 = sub i64 %q, 1
  ret i64 %q2
keep:
  ret i64 %q
zdiv:
  call void @ps_raise_zdi(i32 7) noreturn ; ZeroDivisionError
  unreachable
ovf:
  call void @ps_raise_code(i32 10) noreturn ; OverflowError
  unreachable
}

define internal i64 @ps.imod(i64 %a, i64 %b) {
entry:
  %zero = icmp eq i64 %b, 0
  br i1 %zero, label %trap, label %nonzero
nonzero:
  %min = icmp eq i64 %a, -9223372036854775808
  %neg1 = icmp eq i64 %b, -1
  %bad = and i1 %min, %neg1
  br i1 %bad, label %zeroret, label %divide
zeroret:
  ret i64 0
divide:
  %r = srem i64 %a, %b
  %rnz = icmp ne i64 %r, 0
  %rneg = icmp slt i64 %r, 0
  %bneg = icmp slt i64 %b, 0
  %diff = icmp ne i1 %rneg, %bneg
  %adjust = and i1 %rnz, %diff
  %r2 = add i64 %r, %b
  %out = select i1 %adjust, i64 %r2, i64 %r
  ret i64 %out
trap:
  call void @ps_raise_zdi(i32 7) noreturn ; ZeroDivisionError
  unreachable
}

define internal i64 @ps.ishl(i64 %a, i64 %b) {
entry:
  %neg = icmp slt i64 %b, 0
  br i1 %neg, label %negshift, label %ok
ok:
  %big = icmp uge i64 %b, 64
  br i1 %big, label %wide, label %shift
wide:
  %is0 = icmp eq i64 %a, 0
  br i1 %is0, label %zret, label %ovf
zret:
  ret i64 0
shift:
  %r = shl i64 %a, %b
  %back = ashr i64 %r, %b
  %fit = icmp eq i64 %back, %a
  br i1 %fit, label %good, label %ovf
good:
  ret i64 %r
negshift:
  call void @ps_raise_code(i32 51) noreturn ; ValueError
  unreachable
ovf:
  call void @ps_raise_code(i32 10) noreturn ; OverflowError
  unreachable
}

define internal i64 @ps.ishr(i64 %a, i64 %b) {
entry:
  %neg = icmp slt i64 %b, 0
  br i1 %neg, label %trap, label %ok
ok:
  %big = icmp sge i64 %b, 64
  %c = select i1 %big, i64 63, i64 %b
  %r = ashr i64 %a, %c
  ret i64 %r
trap:
  call void @ps_raise_code(i32 51) noreturn ; ValueError
  unreachable
}

define internal double @ps.fdiv(double %a, double %b) {
entry:
  %z = fcmp oeq double %b, 0.0
  br i1 %z, label %trap, label %ok
ok:
  %q = fdiv double %a, %b
  ret double %q
trap:
  call void @ps_raise_zdi(i32 7) noreturn ; ZeroDivisionError
  unreachable
}

define internal %ps.str @ps.strconcat(%ps.str %a, %ps.str %b) {
entry:
  %na = extractvalue %ps.str %a, 0
  %pa = extractvalue %ps.str %a, 1
  %nb = extractvalue %ps.str %b, 0
  %pb = extractvalue %ps.str %b, 1
  %sum = call { i64, i1 } @llvm.sadd.with.overflow.i64(i64 %na, i64 %nb)
  %ov = extractvalue { i64, i1 } %sum, 1
  br i1 %ov, label %trap, label %sized
sized:
  %n = extractvalue { i64, i1 } %sum, 0
  %atmax = icmp eq i64 %n, 9223372036854775807
  br i1 %atmax, label %trap, label %alloc
alloc:
  %n1 = add i64 %n, 1
  %mem = call ptr @malloc(i64 %n1)
  %null = icmp eq ptr %mem, null
  br i1 %null, label %nomem, label %copy
copy:
  call void @llvm.memcpy.p0.p0.i64(ptr %mem, ptr %pa, i64 %na, i1 false)
  %dst2 = getelementptr i8, ptr %mem, i64 %na
  call void @llvm.memcpy.p0.p0.i64(ptr %dst2, ptr %pb, i64 %nb, i1 false)
  %end = getelementptr i8, ptr %mem, i64 %n
  store i8 0, ptr %end
  %s0 = insertvalue %ps.str poison, i64 %n, 0
  %s1 = insertvalue %ps.str %s0, ptr %mem, 1
  ret %ps.str %s1
trap:
  call void @ps_raise_code(i32 10) noreturn ; OverflowError
  unreachable
nomem:
  call void @ps_raise_code(i32 41) noreturn ; RuntimeError
  unreachable
}

define void @binary_ops() {
entry:
  %t1 = alloca i64
  %t2 = alloca i64
  %t3 = alloca i64
  %t4 = alloca i64
  %t5 = alloca i64
  %t6 = alloca i64
  %t7 = alloca i64
  %t8 = alloca i64
  %t9 = alloca i64
  %t10 = alloca i64
  %t11 = alloca double
  %t12 = alloca double
  %t13 = alloca %ps.str
  %t14 = alloca i32
  %t15 = call i64 @ps.iadd(i64 1, i64 2)
  store i64 %t15, ptr %t1
  %t16 = call i64 @ps.isub(i64 1, i64 2)
  store i64 %t16, ptr %t2
  %t17 = call i64 @ps.imul(i64 1, i64 2)
  store i64 %t17, ptr %t3
  %t18 = call i64 @ps.floordiv(i64 1, i64 2)
  store i64 %t18, ptr %t4
  %t19 = call i64 @ps.imod(i64 1, i64 2)
  store i64 %t19, ptr %t5
  %t20 = and i64 1, 2
  store i64 %t20, ptr %t6
  %t21 = or i64 1, 2
  store i64 %t21, ptr %t7
  %t22 = xor i64 1, 2
  store i64 %t22, ptr %t8
  %t23 = call i64 @ps.ishl(i64 1, i64 2)
  store i64 %t23, ptr %t9
  %t24 = call i64 @ps.ishr(i64 1, i64 2)
  store i64 %t24, ptr %t10
  %t25 = fadd double 0x3FF8000000000000, 0x4004000000000000
  store double %t25, ptr %t11
  %t26 = call double @ps.fdiv(double 0x3FF8000000000000, double 0x4004000000000000)
  store double %t26, ptr %t12
  %t27 = insertvalue %ps.str poison, i64 1, 0
  %t28 = insertvalue %ps.str %t27, ptr @ps.str.0, 1
  %t29 = insertvalue %ps.str poison, i64 1, 0
  %t30 = insertvalue %ps.str %t29, ptr @ps.str.1, 1
  %t31 = call %ps.str @ps.strconcat(%ps.str %t28, %ps.str %t30)
  store %ps.str %t31, ptr %t13
  ret void
}
