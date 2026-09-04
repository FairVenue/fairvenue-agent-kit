from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fairvenue import NonceManager


def test_nonce_is_unique_and_monotonic_under_concurrency() -> None:
    manager = NonceManager(lambda: 1000)
    with ThreadPoolExecutor(max_workers=8) as pool:
        values = list(pool.map(lambda _: manager.next(), range(100)))

    assert len(set(values)) == 100
    assert sorted(values) == list(range(1000, 1100))
