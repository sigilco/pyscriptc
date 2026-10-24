%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

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
  %t21 = insertvalue %ps.str %t20, i64 1, 2
  %t22 = insertvalue %ps.str %t21, i32 1, 3
  %t23 = insertvalue %ps.str poison, i64 1, 0
  %t24 = insertvalue %ps.str %t23, ptr @ps.str.1, 1
  %t25 = insertvalue %ps.str %t24, i64 1, 2
  %t26 = insertvalue %ps.str %t25, i32 1, 3
  %t28 = extractvalue %ps.str %t22, 0
  %t29 = extractvalue %ps.str %t22, 1
  %t30 = extractvalue %ps.str %t26, 0
  %t31 = extractvalue %ps.str %t26, 1
  %t32 = call i32 @ps_str_cmp(i64 %t28, ptr %t29, i64 %t30, ptr %t31)
  %t27 = icmp eq i32 %t32, 0
  store i1 %t27, ptr %t8
  %t33 = load ptr, ptr %t10
  %t34 = load ptr, ptr %t10
  %t35 = icmp eq ptr %t33, %t34
  store i1 %t35, ptr %t9
  ret void
}
