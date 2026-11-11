"""KOReader partial MD5 against known vectors.

The synthetic vectors were produced with BookBridge's independent formulation
(offset = 0 if i == -1 else 1024 * 4**i) after that and this code both returned
KOReader's own spec vector for test-data tall.pdf (41cce710f34e5ec21315e19c99821415)
and leaves.epub (59d481d168cca6267322f150c5f6a2a3). Set KOREADER_TEST_DATA to a
checkout of github.com/koreader/test-data to re-run the upstream vectors.
"""
import hashlib
import io
import os

import pytest

from kosync_helper.partial_md5 import offsets, partial_md5, partial_md5_fileobj


def test_offsets_match_koreader_lshift():
    # lshift(1024, 2*i) for i = -1..10; LuaJIT masks the -2 shift to offset 0
    assert offsets() == [0, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304,
                         16777216, 67108864, 268435456, 1073741824]


@pytest.mark.parametrize("data,expected", [
    (bytes((i * 7 + 3) % 256 for i in range(3 * 1024 * 1024)), "6f50fffb89716241b7da5f559c48ac34"),
    (bytes(i % 251 for i in range(5000)), "e77dcca7f22a949ae8492c260ca19f32"),
    (b"%PDF-1.4 tiny", "948f0bf3b78b3a6a9000b60786b201ae"),
    (b"", "d41d8cd98f00b204e9800998ecf8427e"),
])
def test_known_vectors(data, expected):
    assert partial_md5_fileobj(io.BytesIO(data)) == expected


def test_only_sampled_bytes_count():
    data = bytearray(os.urandom(70_000))
    before = partial_md5_fileobj(io.BytesIO(bytes(data)))
    data[2048] ^= 0xFF  # between the 1 KiB samples at 1024 and 4096
    assert partial_md5_fileobj(io.BytesIO(bytes(data))) == before
    data[4096] ^= 0xFF  # inside a sample
    assert partial_md5_fileobj(io.BytesIO(bytes(data))) != before


def test_short_file_is_md5_of_whole_file_plus_tail(tmp_path):
    data = os.urandom(1500)
    p = tmp_path / "x.pdf"
    p.write_bytes(data)
    # samples: [0:1024] and [1024:1500]
    assert partial_md5(str(p)) == hashlib.md5(data).hexdigest()


@pytest.mark.skipif(not os.environ.get("KOREADER_TEST_DATA"), reason="KOREADER_TEST_DATA not set")
def test_upstream_koreader_vectors():
    root = os.environ["KOREADER_TEST_DATA"]
    assert partial_md5(os.path.join(root, "tall.pdf")) == "41cce710f34e5ec21315e19c99821415"
    assert partial_md5(os.path.join(root, "leaves.epub")) == "59d481d168cca6267322f150c5f6a2a3"
