%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

@ps.str.0 = private unnamed_addr constant [2 x i8] c"a\00"
@ps.str.1 = private unnamed_addr constant [3 x i8] c"ab\00"
@ps.str.2 = private unnamed_addr constant [2 x i8] c"k\00"

declare ptr @ps_seq_new(i64, i64)
declare i64 @ps_seq_len(ptr)
declare void @ps_seq_get(ptr, i64, ptr)
declare void @ps_seq_set(ptr, i64, ptr)
declare i32 @ps_seq_has_i64(ptr, i64)
declare i32 @ps_seq_has_f64(ptr, double)
declare i32 @ps_seq_has_buf(ptr, i64, ptr)
declare ptr @ps_map_new(i32, i64, i64, i64)
declare i64 @ps_map_len(ptr)
declare void @ps_map_put(ptr, ptr, ptr)
declare void @ps_map_get(ptr, ptr, ptr)
declare i32 @ps_map_has(ptr, ptr)
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

define void @contains_kinds() {
entry:
  %t1 = alloca ptr
  %t2 = alloca ptr
  %t3 = alloca i1
  %t4 = alloca i1
  %t5 = alloca i1
  %t6 = alloca i32
  %t7 = insertvalue %ps.str poison, i64 1, 0
  %t8 = insertvalue %ps.str %t7, ptr @ps.str.0, 1
  %t9 = insertvalue %ps.str %t8, i64 1, 2
  %t10 = insertvalue %ps.str %t9, i32 1, 3
  %t11 = insertvalue %ps.str poison, i64 2, 0
  %t12 = insertvalue %ps.str %t11, ptr @ps.str.1, 1
  %t13 = insertvalue %ps.str %t12, i64 2, 2
  %t14 = insertvalue %ps.str %t13, i32 1, 3
  %t15 = extractvalue %ps.str %t14, 0
  %t16 = extractvalue %ps.str %t14, 1
  %t17 = extractvalue %ps.str %t10, 0
  %t18 = extractvalue %ps.str %t10, 1
  %t19 = call i64 @ps_str_find(i64 %t15, ptr %t16, i64 %t17, ptr %t18)
  %t20 = icmp sge i64 %t19, 0
  store i1 %t20, ptr %t3
  %t21 = load ptr, ptr %t1
  %t22 = call i32 @ps_seq_has_i64(ptr %t21, i64 1)
  %t23 = icmp ne i32 %t22, 0
  store i1 %t23, ptr %t4
  %t24 = insertvalue %ps.str poison, i64 1, 0
  %t25 = insertvalue %ps.str %t24, ptr @ps.str.2, 1
  %t26 = insertvalue %ps.str %t25, i64 1, 2
  %t27 = insertvalue %ps.str %t26, i32 1, 3
  %t28 = load ptr, ptr %t2
  %t29 = alloca %ps.buf
  %t30 = extractvalue %ps.str %t27, 0
  %t31 = extractvalue %ps.str %t27, 1
  %t32 = getelementptr %ps.buf, ptr %t29, i32 0, i32 0
  store i64 %t30, ptr %t32
  %t33 = getelementptr %ps.buf, ptr %t29, i32 0, i32 1
  store ptr %t31, ptr %t33
  %t34 = call i32 @ps_map_has(ptr %t28, ptr %t29)
  %t35 = icmp ne i32 %t34, 0
  %t36 = xor i1 %t35, true
  store i1 %t36, ptr %t5
  ret void
}
