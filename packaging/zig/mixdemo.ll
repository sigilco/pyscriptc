%ps.str = type { i64, ptr }

%cls.TextIO = type { i8 }
%cls.BytesIO = type { i8 }
@ps.str.0 = private unnamed_addr constant [20 x i8] c"the quick brown fox\00"
@ps.str.1 = private unnamed_addr constant [20 x i8] c"the quick brown fox\00"
@ps.fmt.int = private unnamed_addr constant [6 x i8] c"%lld\0A\00"

declare i32 @printf(ptr, ...)
declare i64 @ps_mix_crc32(i64, ptr)

define i64 @digest() {
entry:
  %t1 = alloca i64
  %t2 = alloca i32
  %t3 = insertvalue %ps.str poison, i64 19, 0
  %t4 = insertvalue %ps.str %t3, ptr @ps.str.0, 1
  %t5 = insertvalue %ps.str poison, i64 19, 0
  %t6 = insertvalue %ps.str %t5, ptr @ps.str.1, 1
  %t7 = extractvalue %ps.str %t6, 0
  %t8 = extractvalue %ps.str %t6, 1
  %t9 = call i64 @ps_mix_crc32(i64 %t7, ptr %t8)
  ret i64 %t9
}

define i32 @main() {
entry:
  %t1 = call i64 @digest()
  call i32 (ptr, ...) @printf(ptr @ps.fmt.int, i64 %t1)
  ret i32 0
}
