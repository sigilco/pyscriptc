%ps.str = type { i64, ptr }

declare i32 @ps_try_call(ptr, ptr, ptr)
declare void @ps_raise(i32) noreturn
declare void @ps_raise_code(i32) noreturn
declare void @ps_raise_msg(i32, i64, ptr) noreturn
declare void @ps_reraise() noreturn
declare i32 @ps_exc_code()
declare i32 @ps_exc_isa(i32, i32)
declare ptr @ps_exc_current()
declare void @ps_raise_exc(ptr)

define void @try_finally() {
entry:
  %t1 = alloca i64
  %t2 = alloca i32
  %t3 = alloca [1 x ptr]
  store ptr %t1, ptr %t3
  %t4 = call i32 @ps_try_call(ptr @ps.try.0, ptr %t3, ptr %t2)
  %t5 = icmp ne i32 %t4, 0
  br i1 %t5, label %L1.fin.raise, label %L2.fin.norm
L1.fin.raise:
  %t6 = call ptr @ps_exc_current()
  store i64 3, ptr %t1
  call void @ps_raise_exc(ptr %t6) noreturn
  unreachable
L2.fin.norm:
  %t7 = load i32, ptr %t2
  %t8 = icmp eq i32 %t7, 1
  %t9 = icmp eq i32 %t7, 2
  br i1 %t8, label %L4.fin.ret, label %L7.fin.notret
L7.fin.notret:
  br i1 %t9, label %L6.fin.susp, label %L5.fin.cont
L4.fin.ret:
  store i64 3, ptr %t1
  ret void
L6.fin.susp:
  ret void
L5.fin.cont:
  store i64 3, ptr %t1
  br label %L3.fin.merge
L3.fin.merge:
  ret void
}

define internal i32 @ps.try.1(ptr %env) {
entry:
  %t1 = load ptr, ptr %env
  %t2 = alloca i32
  store i64 1, ptr %t1
  ret i32 0
}

define internal i32 @ps.try.0(ptr %env) {
entry:
  %t1 = load ptr, ptr %env
  %t2 = alloca i32
  %t3 = alloca [1 x ptr]
  store ptr %t1, ptr %t3
  %t4 = call i32 @ps_try_call(ptr @ps.try.1, ptr %t3, ptr %t2)
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
  %t11 = icmp eq i32 %t9, 2
  br i1 %t10, label %L4.try.ret, label %L8.try.notret
L8.try.notret:
  br i1 %t11, label %L7.try.susp, label %L9.try.fell
L7.try.susp:
  ret i32 2
L9.try.fell:
  br label %L3.try.merge
L4.try.ret:
  ret i32 1
L3.try.merge:
  ret i32 0
}
