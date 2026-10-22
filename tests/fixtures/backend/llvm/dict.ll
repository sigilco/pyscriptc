%ps.str = type { i64, ptr }

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
  %t5 = call ptr @ps_map_new(i32 2, i64 16, i64 8, i64 1)
  %t6 = alloca %ps.str
  store %ps.str %t4, ptr %t6
  %t7 = alloca i64
  store i64 1, ptr %t7
  call void @ps_map_put(ptr %t5, ptr %t6, ptr %t7)
  store ptr %t5, ptr %t1
  ret void
}
