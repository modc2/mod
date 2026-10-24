"""Ring choice (networks/base.py): a member's ring is fixed, so a claim and
the submission it links to (same key image) show the same keys — no
intersection shrinks the anonymity set below the ring size."""
from ztensor_src.networks.base import partition_ring

MEMBERS = list(range(10))


def test_fits_whole_set():
    assert partition_ring(MEMBERS, 3, 64) == MEMBERS


def test_blocks_are_fixed_and_contain_the_signer():
    for i in MEMBERS:
        r = partition_ring(MEMBERS, i, 4)
        assert i in r and len(r) == 4
        # the claim's ring is the submission's ring: the overlap is everything
        assert set(r) & set(partition_ring(MEMBERS, i, 4)) == set(r)


def test_last_block_borrows_to_stay_full():
    assert partition_ring(MEMBERS, 9, 4) == [6, 7, 8, 9]
    assert partition_ring(MEMBERS, 8, 4) == [6, 7, 8, 9]
    assert partition_ring(MEMBERS, 5, 4) == [4, 5, 6, 7]


def test_every_member_hides_among_at_least_ring_size():
    for size in (1, 3, 4, 7):
        for i in MEMBERS:
            assert len(partition_ring(MEMBERS, i, size)) == size
