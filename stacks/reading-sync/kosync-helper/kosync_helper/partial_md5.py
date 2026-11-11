"""KOReader's "Binary" document hash (partial MD5).

Port of `util.partialMD5` in koreader/frontend/util.lua:

    local step, size = 1024, 1024
    for i = -1, 10 do
        file:seek("set", lshift(step, 2*i))   -- i = -1 -> offset 0 (LuaJIT masks the shift)
        local sample = file:read(size)
        if sample then update(sample) else break end
    end

So it hashes 1 KiB samples at offsets 0, 1 KiB, 4 KiB, 16 KiB, ... 1 GiB and stops at
the first offset past the end of the file. Checked against KOReader's own spec vector
(spec/unit/util_spec.lua: test-data tall.pdf -> 41cce710f34e5ec21315e19c99821415).
"""
from __future__ import annotations

import hashlib
from typing import BinaryIO

STEP = 1024
SIZE = 1024


def offsets() -> list[int]:
    return [0] + [STEP << (2 * i) for i in range(0, 11)]


def partial_md5_fileobj(f: BinaryIO) -> str:
    md5 = hashlib.md5(usedforsecurity=False)
    for offset in offsets():
        f.seek(offset)
        sample = f.read(SIZE)
        if not sample:
            break
        md5.update(sample)
    return md5.hexdigest()


def partial_md5(path: str) -> str:
    with open(path, "rb") as f:
        return partial_md5_fileobj(f)
