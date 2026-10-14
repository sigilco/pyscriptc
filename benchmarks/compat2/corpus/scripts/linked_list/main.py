class Node:
    def __init__(self, value: int) -> None:
        self.value = value
        self.next: Node | None = None


def build(n: int) -> Node:
    head = Node(0)
    cur = head
    for i in range(1, n):
        nxt = Node(i)
        cur.next = nxt
        cur = nxt
    return head


def reverse(head: Node) -> Node:
    prev: Node | None = None
    cur: Node | None = head
    while cur is not None:
        nxt = cur.next
        cur.next = prev
        prev = cur
        cur = nxt
    assert prev is not None
    return prev


h = reverse(build(1000))
total = 0
first = h.value
node: Node | None = h
while node is not None:
    total += node.value
    node = node.next
print(first, total)
