%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

@ps.str.0 = private unnamed_addr constant [3 x i8] c"hi\00"
@ps.str.1 = private unnamed_addr constant [3 x i8] c"ab\00"

define void @const_types() {
entry:
  %t1 = alloca i64
  %t2 = alloca i1
  %t3 = alloca double
  %t4 = alloca %ps.buf
  %t5 = alloca %ps.buf
  %t6 = alloca i32
  store i64 7, ptr %t1
  store i1 true, ptr %t2
  store double 0x3FF8000000000000, ptr %t3
  %t7 = insertvalue %ps.str poison, i64 2, 0
  %t8 = insertvalue %ps.str %t7, ptr @ps.str.0, 1
  %t9 = insertvalue %ps.str %t8, i64 2, 2
  %t10 = insertvalue %ps.str %t9, i32 1, 3
  %t11 = extractvalue %ps.str %t10, 0
  %t12 = extractvalue %ps.str %t10, 1
  %t13 = getelementptr %ps.buf, ptr %t4, i32 0, i32 0
  store i64 %t11, ptr %t13
  %t14 = getelementptr %ps.buf, ptr %t4, i32 0, i32 1
  store ptr %t12, ptr %t14
  %t15 = insertvalue %ps.str poison, i64 2, 0
  %t16 = insertvalue %ps.str %t15, ptr @ps.str.1, 1
  %t17 = insertvalue %ps.str %t16, i64 2, 2
  %t18 = insertvalue %ps.str %t17, i32 1, 3
  %t19 = extractvalue %ps.str %t18, 0
  %t20 = extractvalue %ps.str %t18, 1
  %t21 = getelementptr %ps.buf, ptr %t5, i32 0, i32 0
  store i64 %t19, ptr %t21
  %t22 = getelementptr %ps.buf, ptr %t5, i32 0, i32 1
  store ptr %t20, ptr %t22
  ret void
}
