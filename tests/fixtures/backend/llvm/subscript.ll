%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

declare ptr @ps_seq_new(i64, i64)
declare i64 @ps_seq_len(ptr)
declare void @ps_seq_get(ptr, i64, ptr)
declare void @ps_seq_set(ptr, i64, ptr)
declare i32 @ps_seq_has_i64(ptr, i64)
declare i32 @ps_seq_has_f64(ptr, double)
declare i32 @ps_seq_has_buf(ptr, i64, ptr)

define void @subscript_expr() {
entry:
  %t1 = alloca ptr
  %t2 = alloca i64
  %t3 = alloca i32
  %t4 = load ptr, ptr %t1
  %t5 = alloca i64
  call void @ps_seq_get(ptr %t4, i64 0, ptr %t5)
  %t6 = load i64, ptr %t5
  store i64 %t6, ptr %t2
  ret void
}
