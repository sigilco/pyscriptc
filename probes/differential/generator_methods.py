# SPDX-License-Identifier: Apache-2.0
# ruff: noqa: UP035, UP043, UP045, UP006 - the typing.Iterator / typing.Generator spellings are the constructs under test
"""G3a differential probe: generator methods (issue #441).

A class-based producer/consumer pair exercises the generator-method
lowering end to end: `self` is a copy-in/copy-out frame local (the
consumer sees the producer's writes after the frame finishes), a
generator method delegates to another generator method with
`yield from`, a generator method returns a value through the frame's
stop field, the send protocol drives a method frame, and a generator
method's frame crosses a call boundary into an `Iterator[int]`
parameter (the frame-specialized callee copy). Both sides run this
same program and the lane byte-compares stdout.
"""

from typing import Generator, Iterator, Optional


class Producer:
    vals: list[int]
    idx: int

    def __init__(self, vals: list[int]) -> None:
        self.vals = vals
        self.idx = 0

    def chunk(self) -> Iterator[int]:
        while self.idx < len(self.vals):
            yield self.vals[self.idx]
            self.idx = self.idx + 1

    def doubled(self) -> Iterator[int]:
        yield from self.chunk()
        yield from self.chunk()

    def pairs(self) -> Generator[int, None, int]:
        total: int = 0
        for v in self.chunk():
            total += v
            yield v
        return total

    def echo(self) -> Generator[int, Optional[str], None]:
        while self.idx < 3:
            word: Optional[str] = yield self.idx
            if word is not None:
                print("echo", word)
            self.idx = self.idx + 1


class Consumer:
    total: int

    def __init__(self) -> None:
        self.total = 0

    def drain(self, it: Iterator[int]) -> int:
        for v in it:
            self.total += v
        return self.total


def main() -> None:
    p = Producer([1, 2, 3])

    # `self` is the producer's own record: the consumer's drain sees the
    # writes the frame made, and a second call resumes from the advanced
    # cursor instead of restarting.
    c = Consumer()
    print("first", c.drain(p.chunk()), "idx", p.idx)
    print("second", c.drain(p.chunk()), "idx", p.idx)

    # `yield from` over another generator method of the same self.
    fresh = Producer([4, 5])
    out: list[int] = []
    for v in fresh.doubled():
        out.append(v)
    print("doubled", out)

    # The return value rides the frame's stop field; the for-loop drain
    # still visits every yield before the stop.
    src = Producer([6, 7])
    stopper = src.pairs()
    seen: list[int] = []
    for v in stopper:
        seen.append(v)
    print("pairs", seen)

    # The send protocol on a method frame: the priming send is None, the
    # second send delivers the word at the resume point.
    talker = Producer([0])
    g = talker.echo()
    print("s0", g.send(None))
    print("s1", g.send("hi"))
    try:
        g.send(None)
    except StopIteration:
        print("done")

    # A method frame crosses the call boundary into an Iterator[int]
    # parameter: the callee's specialized copy drives the frame.
    cross = Producer([8, 9])
    print("cross", Consumer().drain(cross.chunk()), "idx", cross.idx)


main()
