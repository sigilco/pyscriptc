try:
    assert 1 == 2, "one is not two"
except AssertionError:
    print("AssertionError")
except Exception:
    print("Exception")
