from ftmarkets import api


def test_smoke():
    print("Smoke test starting...")
    assert api.FTDataSource is not None
    print("Smoke test passed.")


if __name__ == "__main__":
    test_smoke()
