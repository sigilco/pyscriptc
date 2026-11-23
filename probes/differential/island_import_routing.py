# Issue #1308 (N2): an import outside the closed import table routes to the
# island lane by default, with no opt-out flag. The probe drives one such
# import (`wave`, a stdlib module the closed table does not carry) through the
# embedded interpreter and byte-compares the result with the pinned CPython:
# stdout, stderr and the exit code.
#
# The routed module is the stdlib `wave` reader, so the probe writes a small
# WAV file first and reads its header back through the island-routed module.
# The values that cross the seam are scalars, so the printed output is the
# same on both sides.
import wave

CHANNELS = 1
WIDTH = 2
RATE = 8000
PATH = "island_import_routing_probe.wav"


def write_wav() -> None:
    writer = wave.open(PATH, "wb")
    writer.setnchannels(CHANNELS)
    writer.setsampwidth(WIDTH)
    writer.setframerate(RATE)
    writer.writeframes(b"\x00\x00")
    writer.close()


def read_wav() -> None:
    reader = wave.open(PATH, "rb")
    channels: int = reader.getnchannels()
    width: int = reader.getsampwidth()
    rate: int = reader.getframerate()
    frames: int = reader.getnframes()
    reader.close()
    print(channels, width, rate, frames)


def main() -> None:
    write_wav()
    read_wav()


main()
