%ps.str = type { i64, ptr, i64, i32 }

define i64 @return_stmt() {
entry:
  %t1 = alloca i64
  %t2 = alloca i32
  ret i64 9
}
