%ps.str = type { i64, ptr }

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
  store %ps.str %t8, ptr %t4
  %t9 = insertvalue %ps.str poison, i64 2, 0
  %t10 = insertvalue %ps.str %t9, ptr @ps.str.1, 1
  store %ps.str %t10, ptr %t5
  ret void
}
