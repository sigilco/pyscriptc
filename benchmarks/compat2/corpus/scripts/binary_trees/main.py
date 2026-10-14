class Tree:
    def __init__(self, left: "Tree | None", right: "Tree | None") -> None:
        self.left = left
        self.right = right


def bottom_up(depth: int) -> Tree:
    if depth == 0:
        return Tree(None, None)
    return Tree(bottom_up(depth - 1), bottom_up(depth - 1))


def check(t: Tree) -> int:
    if t.left is None or t.right is None:
        return 1
    return 1 + check(t.left) + check(t.right)


for d in range(2, 12, 3):
    print(d, check(bottom_up(d)))
