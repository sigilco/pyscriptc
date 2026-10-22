%ps.str = type { i64, ptr }

declare i32 @ps_try_call(ptr, ptr, ptr)
declare void @ps_raise(i32) noreturn
declare void @ps_raise_code(i32) noreturn
declare void @ps_raise_msg(i32, i64, ptr) noreturn
declare void @ps_reraise() noreturn
declare i32 @ps_exc_code()
declare i32 @ps_exc_isa(i32, i32)

define void @try_stmt() {
entry:
  %t1 = alloca i64
  %t2 = alloca i32
  %t3 = alloca [1 x ptr]
  store ptr %t1, ptr %t3
  %t4 = call i32 @ps_try_call(ptr @ps.try.0, ptr %t3, ptr %t2)
  %t5 = icmp ne i32 %t4, 0
  br i1 %t5, label %L1.try.handler, label %L2.try.cont
L1.try.handler:
  %t6 = call i32 @ps_exc_code()
  %t7 = call i32 @ps_exc_isa(i32 %t6, i32 51)
  %t8 = icmp ne i32 %t7, 0
  br i1 %t8, label %L5.try.h0, label %L6.try.miss
L5.try.h0:
  store i64 2, ptr %t1
  br label %L3.try.merge
L6.try.miss:
  call void @ps_reraise() noreturn
  unreachable
L2.try.cont:
  %t9 = load i32, ptr %t2
  %t10 = icmp eq i32 %t9, 1
  br i1 %t10, label %L4.try.ret, label %L3.try.merge
L4.try.ret:
  ret void
L3.try.merge:
  ret void
}

define internal i32 @ps.try.0(ptr %env) {
entry:
  %t1 = load ptr, ptr %env
  %t2 = alloca i32
  store i64 1, ptr %t1
  ret i32 0
}
