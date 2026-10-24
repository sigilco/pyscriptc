%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

define void @pass_stmt() {
entry:
  %t1 = alloca i32
  ret void
}
