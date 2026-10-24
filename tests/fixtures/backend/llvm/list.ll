%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

declare ptr @ps_seq_new(i64, i64)
declare i64 @ps_seq_len(ptr)
declare void @ps_seq_get(ptr, i64, ptr)
declare void @ps_seq_set(ptr, i64, ptr)
declare i32 @ps_seq_has_i64(ptr, i64)
declare i32 @ps_seq_has_f64(ptr, double)
declare i32 @ps_seq_has_buf(ptr, i64, ptr)

define void @list_expr() {
entry:
  %t1 = alloca ptr
  %t2 = alloca i32
  %t3 = call ptr @ps_seq_new(i64 8, i64 2)
  %t4 = alloca i64
  store i64 1, ptr %t4
  call void @ps_seq_set(ptr %t3, i64 0, ptr %t4)
  %t5 = alloca i64
  store i64 2, ptr %t5
  call void @ps_seq_set(ptr %t3, i64 1, ptr %t5)
  store ptr %t3, ptr %t1
  ret void
}
