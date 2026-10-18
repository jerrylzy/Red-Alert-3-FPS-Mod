"""End-to-end test of ra3fps/install.ps1 on temporary copies of the original game file.

The game folder is only read: the original comes from ra3_1.12.game.bak (or the game file while it is unmodified).
Every scenario runs install.ps1 in Windows PowerShell with -NoElevate and -GameDir pointing into a temporary folder:
  * -Fps 90 on a fresh folder creates the .bak and installs exactly the patcher's 90 FPS output; running it again
    changes nothing; -Fps 60 switches; -Restore brings back the original; the .bak stays the original throughout;
  * -GameDir may be the Data folder, the game folder above it or the file itself;
  * -Build installs a given file;
  * a modified game file without a .bak, a modified .bak, and a game running from that folder are refused without
    any change;
  * the temporary work folder is removed after every run.
With --with-download, one more run uses -PortablePython: it downloads the pinned portable Python from python.org
(about 12 MB), builds and installs with it, and removes it again.

usage: python tests/test_install.py [--game PATH] [--with-download]
Windows only; needs no extra packages.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'tools'))
sys.path.insert(0, os.path.join(REPO, 'ra3fps'))
from common import DEFAULT_GAME, ORIG_SHA256, find_original, sha256  # noqa: E402
import ra3_fps_patch  # noqa: E402

INSTALL = os.path.join(REPO, 'ra3fps', 'install.ps1')
GAME = 'ra3_1.12.game'
WORK_GLOB = os.path.join(tempfile.gettempdir(), 'RA3-FPS-Mod-*')


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def file_sha(path):
    if not os.path.exists(path):
        return None
    with open(path, 'rb') as f:
        return sha256(f.read())


class Folder:
    """A fake install: <root>/Data/ra3_1.12.game (+ .bak)."""

    def __init__(self, base, name, live, bak=None):
        self.root = os.path.join(base, name)
        self.data = os.path.join(self.root, 'Data')
        os.makedirs(self.data)
        self.game = os.path.join(self.data, GAME)
        self.bak = self.game + '.bak'
        with open(self.game, 'wb') as f:
            f.write(live)
        if bak is not None:
            with open(self.bak, 'wb') as f:
                f.write(bak)

    def state(self):
        return file_sha(self.game), file_sha(self.bak)


def run(folder, args, python, expect=0, needle=None):
    cmd = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', INSTALL] + args + ['-NoElevate']
    if python:
        cmd += ['-Python', python]
    before = set(glob.glob(WORK_GLOB))
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    out = p.stdout + p.stderr
    check(p.returncode == expect, f'{args}: exit code {p.returncode}, expected {expect}\n{out}')
    check(folder.game.lower() in out.lower(), f'{args}: the output does not name the test game file\n{out}')
    check(DEFAULT_GAME.lower() not in out.lower(), f'{args}: the output names the real game file\n{out}')
    check(set(glob.glob(WORK_GLOB)) <= before, f'{args}: a temporary work folder was left behind')
    if needle:
        check(needle.lower() in out.lower(), f'{args}: expected "{needle}" in the output\n{out}')
    return out


def start_fake_game(folder):
    """Runs a copy of cmd.exe as <folder>/Data/ra3_1.12.game, so a process with that name and path exists."""
    shutil.copyfile(os.path.join(os.environ['SystemRoot'], 'System32', 'cmd.exe'), folder.game)
    proc = subprocess.Popen([folder.game, '/c', 'ping -n 120 127.0.0.1 >nul'], executable=folder.game,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    query = ("(Get-CimInstance Win32_Process -Filter \"ProcessId = %d\").ExecutablePath" % proc.pid)
    for _ in range(20):
        path = subprocess.run(['powershell.exe', '-NoProfile', '-Command', query], capture_output=True,
                              text=True).stdout.strip()
        if path.lower() == folder.game.lower():
            return proc
        time.sleep(0.25)
    proc.kill()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--game', default=DEFAULT_GAME, help='ra3_1.12.game whose original is read (never written)')
    ap.add_argument('--with-download', action='store_true', help='also test the portable Python download')
    args = ap.parse_args()
    _, orig = find_original(args.game)
    exp = {fps: sha256(ra3_fps_patch.build_patch(orig, fps)) for fps in (90, 60)}
    py = sys.executable
    base = tempfile.mkdtemp(prefix='ra3fps-test-')
    try:
        f = Folder(base, 'fresh', orig)
        run(f, ['-Fps', '90', '-GameDir', f.data], py, needle='the 90 FPS mod is installed')
        check(f.state() == (exp[90], ORIG_SHA256), '90 FPS from a fresh folder')
        run(f, ['-Fps', '90', '-GameDir', f.data], py, needle='already matches')
        check(f.state() == (exp[90], ORIG_SHA256), 'repeated 90 FPS install')
        run(f, ['-Fps', '60', '-GameDir', f.data], py, needle='the 60 FPS mod is installed')
        check(f.state() == (exp[60], ORIG_SHA256), 'switch to 60 FPS')
        run(f, ['-Restore', '-GameDir', f.data], py, needle='original game file is in place')
        check(f.state() == (ORIG_SHA256, ORIG_SHA256), 'restore')
        run(f, ['-Restore', '-GameDir', f.data], py, needle='already matches')
        print('install 90 -> again -> 60 -> restore -> again: ok')

        f = Folder(base, 'paths', orig)
        run(f, ['-Fps', '90', '-GameDir', f.root], py)
        check(f.state() == (exp[90], ORIG_SHA256), '-GameDir = game folder')
        run(f, ['-Restore', '-GameDir', f.game], py)
        check(f.state() == (ORIG_SHA256, ORIG_SHA256), '-GameDir = game file')
        print('-GameDir as the game folder and as the file: ok')

        f = Folder(base, 'build', orig)
        built = os.path.join(base, 'ra3_1.12_60fps.game')
        with open(built, 'wb') as out:
            out.write(ra3_fps_patch.build_patch(orig, 60))
        run(f, ['-Build', built, '-GameDir', f.data], py, needle='installed')
        check(f.state() == (exp[60], ORIG_SHA256), '-Build')
        print('-Build: ok')

        modified = bytearray(orig)
        modified[0x1000] ^= 0xFF
        f = Folder(base, 'modified', bytes(modified))
        before = f.state()
        run(f, ['-Fps', '90', '-GameDir', f.data], py, expect=1, needle='not the original v1.12 file')
        check(f.state() == before and before[1] is None, 'modified game file without a .bak must stay untouched')
        f = Folder(base, 'badbak', orig, bak=bytes(modified))
        before = f.state()
        run(f, ['-Fps', '90', '-GameDir', f.data], py, expect=1, needle='is not the original v1.12 file')
        check(f.state() == before, 'modified .bak must stay untouched')
        print('modified game file / modified backup refused: ok')

        f = Folder(base, 'running', orig, bak=orig)
        proc = start_fake_game(f)
        if proc is None:
            print('game running: skipped (could not start a stand-in process)')
        else:
            try:
                before = f.state()
                run(f, ['-Restore', '-GameDir', f.data], py, expect=1, needle='running from this folder')
                check(f.state() == before, 'files must stay untouched while the game runs')
            finally:
                proc.kill()
                proc.wait()
            print('game running from the folder refused: ok')

        if args.with_download:
            f = Folder(base, 'portable', orig)
            run(f, ['-Fps', '90', '-GameDir', f.data, '-PortablePython'], None, needle='portable Python')
            check(f.state() == (exp[90], ORIG_SHA256), 'portable Python')
            print('portable Python download, build, install and cleanup: ok')
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print('ALL OK')


if __name__ == '__main__':
    main()
