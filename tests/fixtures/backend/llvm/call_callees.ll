%ps.str = type { i64, ptr, i64, i32 }

%cls.Point = type { i64 }
@ps.str.0 = private unnamed_addr constant [2 x i8] c"a\00"

declare ptr @ps_alloc(i64)
declare ptr @ps_seq_new(i64, i64)
declare i64 @ps_seq_len(ptr)
declare void @ps_seq_get(ptr, i64, ptr)
declare void @ps_seq_set(ptr, i64, ptr)
declare i32 @ps_seq_has_i64(ptr, i64)
declare i32 @ps_seq_has_f64(ptr, double)
declare i32 @ps_seq_has_buf(ptr, i64, ptr)
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
declare i64 @ps_float_to_int(double)
declare double @ps_math_sqrt(double)
declare double @ps_math_fabs(double)
declare double @ps_math_sin(double)
declare double @ps_math_cos(double)
declare double @ps_math_tan(double)
declare double @ps_math_exp(double)
declare double @ps_math_log(double)
declare double @ps_math_log2(double)
declare double @ps_math_log10(double)
declare double @ps_math_hypot(double, double)
declare double @ps_math_pow(double, double)
declare double @ps_math_atan2(double, double)
declare i64 @ps_math_floor(double)
declare i64 @ps_math_ceil(double)
declare i64 @ps_math_trunc(double)

define void @call_callees() {
entry:
  %t1 = alloca ptr
  %t2 = alloca i64
  %t3 = alloca i64
  %t4 = alloca i64
  %t5 = alloca %ps.str
  %t6 = alloca ptr
  %t7 = alloca i32
  %t8 = load ptr, ptr %t1
  %t9 = call i64 @ps_seq_len(ptr %t8)
  store i64 %t9, ptr %t2
  %t10 = call i64 @helper(i64 1)
  store i64 %t10, ptr %t3
  %t11 = call double @ps_math_sqrt(double 0x4000000000000000)
  store i64 %t11, ptr %t4
  %t12 = insertvalue %ps.str poison, i64 1, 0
  %t13 = insertvalue %ps.str %t12, ptr @ps.str.0, 1
  %t14 = insertvalue %ps.str %t13, i64 1, 2
  %t15 = insertvalue %ps.str %t14, i32 1, 3
  %t16 = extractvalue %ps.str %t15, 0
  %t17 = extractvalue %ps.str %t15, 1
  %t18 = alloca %ps.str
  %t19 = getelementptr %ps.str, ptr %t18, i32 0, i32 2
  store i64 0, ptr %t19
  %t20 = getelementptr %ps.str, ptr %t18, i32 0, i32 3
  store i32 0, ptr %t20
  call void @ps_str_upper(i64 %t16, ptr %t17, ptr %t18)
  %t21 = load %ps.str, ptr %t18
  store %ps.str %t21, ptr %t5
  %t22 = getelementptr %cls.Point, ptr null, i64 1
  %t23 = ptrtoint ptr %t22 to i64
  %t24 = call ptr @ps_alloc(i64 %t23)
  store ptr %t24, ptr %t6
  ret void
}

define i64 @helper(i64 %x) {
entry:
  %t1 = alloca i64
  %t2 = alloca i64
  %t3 = alloca i32
  store i64 %x, ptr %t1
  ret i64 0
}
