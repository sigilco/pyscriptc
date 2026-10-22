%ps.str = type { i64, ptr }

declare ptr @ps_seq_new(i64, i64)
declare i64 @ps_seq_len(ptr)
declare void @ps_seq_get(ptr, i64, ptr)
declare void @ps_seq_set(ptr, i64, ptr)
declare i32 @ps_seq_has_i64(ptr, i64)
declare i32 @ps_seq_has_f64(ptr, double)
declare i32 @ps_seq_has_buf(ptr, i64, ptr)

define void @setitem_stmt() {
entry:
  %t1 = alloca ptr
  %t2 = alloca i32
  %t3 = load ptr, ptr %t1
  %t4 = alloca i64
  store i64 1, ptr %t4
  call void @ps_seq_set(ptr %t3, i64 0, ptr %t4)
  ret void
}
