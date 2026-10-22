%ps.str = type { i64, ptr }

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
  %t9 = insertvalue %ps.str poison, i64 2, 0
  %t10 = insertvalue %ps.str %t9, ptr @ps.str.1, 1
  %t11 = extractvalue %ps.str %t10, 0
  %t12 = extractvalue %ps.str %t10, 1
  %t13 = extractvalue %ps.str %t8, 0
  %t14 = extractvalue %ps.str %t8, 1
  %t15 = call i64 @ps_str_find(i64 %t11, ptr %t12, i64 %t13, ptr %t14)
  %t16 = icmp sge i64 %t15, 0
  store i1 %t16, ptr %t3
  %t17 = load ptr, ptr %t1
  %t18 = call i32 @ps_seq_has_i64(ptr %t17, i64 1)
  %t19 = icmp ne i32 %t18, 0
  store i1 %t19, ptr %t4
  %t20 = insertvalue %ps.str poison, i64 1, 0
  %t21 = insertvalue %ps.str %t20, ptr @ps.str.2, 1
  %t22 = load ptr, ptr %t2
  %t23 = alloca %ps.str
  store %ps.str %t21, ptr %t23
  %t24 = call i32 @ps_map_has(ptr %t22, ptr %t23)
  %t25 = icmp ne i32 %t24, 0
  %t26 = xor i1 %t25, true
  store i1 %t26, ptr %t5
  ret void
}
