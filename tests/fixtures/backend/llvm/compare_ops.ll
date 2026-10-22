%ps.str = type { i64, ptr }

@ps.str.0 = private unnamed_addr constant [2 x i8] c"a\00"
@ps.str.1 = private unnamed_addr constant [2 x i8] c"b\00"

declare void @ps_str_get(i64, ptr, i64, ptr)
declare i64 @ps_buf_at(i64, ptr, i64)
declare void @ps_str_strip(i64, ptr, ptr)
declare void @ps_str_lstrip(i64, ptr, ptr)
declare void @ps_str_rstrip(i64, ptr, ptr)
declare i32 @ps_str_startswith(i64, ptr, i64, ptr)
declare i32 @ps_str_endswith(i64, ptr, i64, ptr)
declare i64 @ps_str_find(i64, ptr, i64, ptr)
declare i32 @ps_str_cmp(i64, ptr, i64, ptr)
declare void @ps_str_upper(i64, ptr, ptr)
declare void @ps_str_lower(i64, ptr, ptr)
declare void @ps_str_replace(i64, ptr, i64, ptr, i64, ptr, ptr)

define void @compare_ops() {
entry:
  %t1 = alloca i1
  %t2 = alloca i1
  %t3 = alloca i1
  %t4 = alloca i1
  %t5 = alloca i1
  %t6 = alloca i1
  %t7 = alloca i1
  %t8 = alloca i1
  %t9 = alloca i1
  %t10 = alloca ptr
  %t11 = alloca i32
  %t12 = icmp slt i64 1, 2
  store i1 %t12, ptr %t1
  %t13 = icmp sle i64 1, 2
  store i1 %t13, ptr %t2
  %t14 = icmp sgt i64 1, 2
  store i1 %t14, ptr %t3
  %t15 = icmp sge i64 1, 2
  store i1 %t15, ptr %t4
  %t16 = icmp eq i64 1, 2
  store i1 %t16, ptr %t5
  %t17 = icmp ne i64 1, 2
  store i1 %t17, ptr %t6
  %t18 = fcmp olt double 0x3FF8000000000000, 0x4004000000000000
  store i1 %t18, ptr %t7
  %t19 = insertvalue %ps.str poison, i64 1, 0
  %t20 = insertvalue %ps.str %t19, ptr @ps.str.0, 1
  %t21 = insertvalue %ps.str poison, i64 1, 0
  %t22 = insertvalue %ps.str %t21, ptr @ps.str.1, 1
  %t24 = extractvalue %ps.str %t20, 0
  %t25 = extractvalue %ps.str %t20, 1
  %t26 = extractvalue %ps.str %t22, 0
  %t27 = extractvalue %ps.str %t22, 1
  %t28 = call i32 @ps_str_cmp(i64 %t24, ptr %t25, i64 %t26, ptr %t27)
  %t23 = icmp eq i32 %t28, 0
  store i1 %t23, ptr %t8
  %t29 = load ptr, ptr %t10
  %t30 = load ptr, ptr %t10
  %t31 = icmp eq ptr %t29, %t30
  store i1 %t31, ptr %t9
  ret void
}
