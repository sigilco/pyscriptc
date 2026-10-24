%ps.str = type { i64, ptr, i64, i32 }
%ps.buf = type { i64, ptr }

define void @cast_int_to_float() {
entry:
  %t1 = alloca double
  %t2 = alloca i32
  %t3 = sitofp i64 2 to double
  store double %t3, ptr %t1
  ret void
}
