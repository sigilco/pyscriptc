%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

@ps.str.0 = private unnamed_addr constant [2 x i8] c"k\00"

declare ptr @ps_map_new(i32, i64, i64, i64)
declare i64 @ps_map_len(ptr)
declare void @ps_map_put(ptr, ptr, ptr)
declare void @ps_map_get(ptr, ptr, ptr)
declare i32 @ps_map_has(ptr, ptr)

define void @dict_expr() {
entry:
  %t1 = alloca ptr
  %t2 = alloca i32
  %t3 = insertvalue %ps.str poison, i64 1, 0
  %t4 = insertvalue %ps.str %t3, ptr @ps.str.0, 1
  %t5 = insertvalue %ps.str %t4, i64 1, 2
  %t6 = insertvalue %ps.str %t5, i32 1, 3
  %t7 = call ptr @ps_map_new(i32 2, i64 16, i64 8, i64 1)
  %t8 = alloca %ps.buf
  %t9 = extractvalue %ps.str %t6, 0
  %t10 = extractvalue %ps.str %t6, 1
  %t11 = getelementptr %ps.buf, ptr %t8, i32 0, i32 0
  store i64 %t9, ptr %t11
  %t12 = getelementptr %ps.buf, ptr %t8, i32 0, i32 1
  store ptr %t10, ptr %t12
  %t13 = alloca i64
  store i64 1, ptr %t13
  call void @ps_map_put(ptr %t7, ptr %t8, ptr %t13)
  store ptr %t7, ptr %t1
  ret void
}
