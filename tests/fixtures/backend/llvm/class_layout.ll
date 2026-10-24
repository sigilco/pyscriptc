%ps.str = type { i64, ptr, i64, i32 }

%cls.Point = type { i64 }

declare ptr @ps_alloc(i64)

define void @use_point() {
entry:
  %t1 = alloca ptr
  %t2 = alloca i32
  %t3 = getelementptr %cls.Point, ptr null, i64 1
  %t4 = ptrtoint ptr %t3 to i64
  %t5 = call ptr @ps_alloc(i64 %t4)
  call void @cls.Point.__init__(ptr %t5, i64 1)
  store ptr %t5, ptr %t1
  ret void
}

define void @cls.Point.__init__(ptr %self, i64 %x) {
entry:
  %t1 = alloca ptr
  %t2 = alloca i64
  %t3 = alloca i32
  store ptr %self, ptr %t1
  store i64 %x, ptr %t2
  %t4 = load ptr, ptr %t1
  %t5 = load i64, ptr %t2
  %t6 = getelementptr %cls.Point, ptr %t4, i32 0, i32 0
  store i64 %t5, ptr %t6
  ret void
}
