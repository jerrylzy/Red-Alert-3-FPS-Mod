"""Regenerate the cached linear disassembly of .text (build/text_insns.pkl) used by rdis.py and verify_build.py.

The cache is a pickled list of (address, mnemonic, op_str, size) tuples from a linear Capstone sweep (32-bit x86,
Intel syntax, no detail) over the whole .text VirtualSize (VA 0x401000..0xBC641A) of the original ra3_1.12.game.
When Capstone stops at an undecodable byte, the sweep skips that one byte and resumes; no '.byte' entries are
recorded, so the listing has small gaps (118 in .text). With capstone 5.0.9 and pickle protocol 4 the result has
2,866,851 entries, SHA-256 BDFA21AF45D0CC5899F0E45BBD2169A76E504C4C23E5BCDEE8D778B5B1C43FFE.

usage: python tools/build_insn_cache.py [--game PATH] [--out build/text_insns.pkl]
"""
import argparse
import os
import pickle
import struct
import time

from capstone import CS_ARCH_X86, CS_MODE_32, Cs

from common import DEFAULT_CACHE, DEFAULT_GAME, IB, find_original, sha256

BATCH = 200000          # instructions per Capstone call; bounds memory without changing the result


def text_bounds(d):
    """(first, end) file offsets of .text's VirtualSize, checking that file offset + IB is the VA."""
    pe = struct.unpack_from('<I', d, 0x3C)[0]
    sec = pe + 24 + struct.unpack_from('<H', d, pe + 20)[0]
    if d[sec:sec + 8].rstrip(b'\0') != b'.text':
        raise SystemExit('error: first section is not .text')
    vsize, rva, raw_size, raw_ptr = struct.unpack_from('<IIII', d, sec + 8)
    if rva != raw_ptr:
        raise SystemExit('error: .text RVA differs from its file offset')
    return raw_ptr, raw_ptr + min(vsize, raw_size)


def sweep(d, start, end):
    md = Cs(CS_ARCH_X86, CS_MODE_32)
    out = []
    off = start
    while off < end:
        n = 0
        for ins in md.disasm(d[off:end], off + IB, BATCH):
            out.append((ins.address, ins.mnemonic, ins.op_str, ins.size))
            off = ins.address - IB + ins.size
            n += 1
        if n < BATCH and off < end:   # Capstone stopped before the end: skip one undecodable byte
            off += 1
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--game', default=DEFAULT_GAME,
                    help='ra3_1.12.game whose .bak (or itself, if unmodified) is the original (default: %(default)s)')
    ap.add_argument('--out', default=DEFAULT_CACHE, help='output pickle (default: %(default)s)')
    args = ap.parse_args()
    path, d = find_original(args.game)
    start, end = text_bounds(d)
    t = time.time()
    ins = sweep(d, start, end)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, 'wb') as f:
        pickle.dump(ins, f, protocol=4)
    with open(args.out, 'rb') as f:
        digest = sha256(f.read())
    gaps = sum(1 for a, b in zip(ins, ins[1:]) if b[0] != a[0] + a[3])
    print(f'{path}: VA {start + IB:#x}..{end + IB:#x} -> {len(ins)} instructions, {gaps} gaps, '
          f'{time.time() - t:.1f}s')
    print(f'wrote {args.out} (sha256 {digest})')


if __name__ == '__main__':
    main()
