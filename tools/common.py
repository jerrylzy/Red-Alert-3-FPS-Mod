"""Shared paths and helpers for the developer tools in this folder (the patcher itself does not use them)."""
import hashlib
import os
import pickle

DEFAULT_GAME = r'C:\Program Files\EA Games\Command and Conquer Red Alert 3\Red Alert 3\Data\ra3_1.12.game'
ORIG_SHA256 = '07951DA773B7A6D77ACA5ABD7F27EF8FC9647623C0BC92BB7DDF367455CDBB28'
IB = 0x400000
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_DIR = os.path.join(REPO_DIR, 'build')
DEFAULT_CACHE = os.path.join(BUILD_DIR, 'text_insns.pkl')
TEXT_END = 0x7C641A                       # file offset of the end of .text's original VirtualSize
CAVE_START, CAVE_END = 0x7C6420, 0x7C7000  # file offsets of the code-cave area in the zero-filled .text tail


def sha256(data):
    return hashlib.sha256(data).hexdigest().upper()


def find_original(game=DEFAULT_GAME):
    """Return (path, data) of the pristine v1.12 file: game + '.bak' if present, else the game file itself while it
    is still unmodified. Exits with a message otherwise. Never writes anything."""
    for path in (game + '.bak', game):
        if os.path.exists(path):
            with open(path, 'rb') as f:
                data = f.read()
            if sha256(data) == ORIG_SHA256:
                return path, data
            if path.endswith('.bak'):
                raise SystemExit(f'error: {path} is not the original v1.12 file (SHA-256 mismatch)')
    raise SystemExit(f'error: no original v1.12 file found at {game}(.bak); use --game to point at ra3_1.12.game')


def load_cache(path=DEFAULT_CACHE):
    """Load the cached .text listing: (list of (addr, mnemonic, op_str, size), list of addresses)."""
    if not os.path.exists(path):
        raise SystemExit(f'error: {path} not found; create it with: python tools/build_insn_cache.py')
    with open(path, 'rb') as f:
        ins = pickle.load(f)
    return ins, [i[0] for i in ins]
