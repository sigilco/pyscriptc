%ps.str = type { i64, ptr, i64, i32 }

@ps.str.0 = private unnamed_addr constant [3 x i8] c"hi\00"
@ps.str.1 = private unnamed_addr constant [3 x i8] c"ab\00"

define void @const_types() {
entry:
  %t1 = alloca i64
  %t2 = alloca i1
  %t3 = alloca double
  %t4 = alloca %ps.str
  %t5 = alloca %ps.str
  %t6 = alloca i32
  store i64 7, ptr %t1
  store i1 true, ptr %t2
  store double 0x3FF8000000000000, ptr %t3
  %t7 = insertvalue %ps.str poison, i64 2, 0
  %t8 = insertvalue %ps.str %t7, ptr @ps.str.0, 1
  %t9 = insertvalue %ps.str %t8, i64 2, 2
  %t10 = insertvalue %ps.str %t9, i32 1, 3
  store %ps.str %t10, ptr %t4
  %t11 = insertvalue %ps.str poison, i64 2, 0
  %t12 = insertvalue %ps.str %t11, ptr @ps.str.1, 1
  %t13 = insertvalue %ps.str %t12, i64 2, 2
  %t14 = insertvalue %ps.str %t13, i32 1, 3
  store %ps.str %t14, ptr %t5
  ret void
}
