%ps.str = type { i64, ptr }

%cls.BytesIO = type { i8 }
%cls.TextIO = type { i8 }
@ps.fmt.int = private unnamed_addr constant [6 x i8] c"%lld\0A\00"

declare i32 @printf(ptr, ...)
declare void @llvm.trap() noreturn nounwind
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
  call void @llvm.trap()
  unreachable
}

define i64 @add(i64 %a, i64 %b) {
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
  ret i64 %t7
}

define i32 @main() {
entry:
  %t1 = call i64 @add(i64 20, i64 22)
  call i32 (ptr, ...) @printf(ptr @ps.fmt.int, i64 %t1)
  ret i32 0
}
