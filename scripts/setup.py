#!/usr/bin/env python3
"""One entry point: from a fresh clone and your own dump to a runnable native build of the game.

    python scripts/setup.py --dump <folder> [--jobs N] [tool locations ...]
    python scripts/setup.py --target linux --dump <folder> [--jobs N]      a native Linux build, in <repo>/build-linux
                                                                            (BUILDING_LINUX.md; scripts/linux/install_deps.sh first)

<folder> holds what you dumped from YOUR console (see BUILDING.md):
    <folder>/exefs/    the title's ExeFS files: main  sdk  (rtld, subsdk0, main.npdm are not used), update applied
    <folder>/romfs/    the title's RomFS (data.arc ...), update applied
    optional: <folder>/sysdata/fonts (the system's shared fonts), <folder>/sysdata/zoneinfo

Nothing of the game is stored in the repository: every step below derives its files from the dump, into <repo>/build
(ignored by git). Steps run in this order and are resumable (a finished step is skipped; --redo <step> repeats it,
--from <step> repeats it and everything after it; the compilers themselves only rebuild what changed):

    deps      check the tools and the dump                    stage    lay the sources out in build/work
    convert   dump -> main.elf / sdk.elf (tools/extract)      modules  game's script modules out of data.arc
    tables    every generated header and table                lift     lift main (recomp_eh.py)
    sdk       lift the system-library functions (sdklift.py)  compile  main game library (clang, -O1)
    nro       lift + compile the script modules               link     host runtime + game library -> exe
    check     self-tests that need no game run

Other commands:  --status (what is done)   --steps (list)   --only <step>   --run (print the command that starts the game)
Environment variables named RECOMP_* / SSBU_* are removed from the children's environment; tool locations come from the
options below, from LLVM_ROOT / SDL3_ROOT / VULKAN_HEADERS / GLSLANG_ROOT / SUYU_ROOT, or from <repo>/deps/<name>.
libopus (the music) is optional: --opus, OPUS_DLL or <repo>/deps/opus; without it the build ends with a warning and the
game's Opus streams are silent.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STEPS = ['deps', 'stage', 'convert', 'modules', 'tables', 'lift', 'sdk', 'compile', 'nro', 'link', 'check']
GAME_TEXT_SIZE, SDK_TEXT_SIZE = 0x39c7e90, 0x540950          # 13.0.4: the lifter's constants (recomp.py, sdklift.py)
EXE = 'ssbu_native.exe'
LINUX_EXE = 'ssbu_native'


def exe_name(cx):
    return LINUX_EXE if cx.linux else EXE


def game_lib(cx):
    return 'ssbu_game_eh_o1.so' if cx.linux else 'ssbu_game_eh_o1.dll'


# ---------------------------------------------------------------------------------------------- helpers
class Fail(Exception):
    pass


def say(msg=''):
    print(msg.encode('ascii', 'replace').decode('ascii'), flush=True)


def winexe(argv):
    """Off Windows (cross build), a Windows program runs through Wine; scripts named .exe (the clang wrapper) run as they are."""
    if os.name != 'nt' and argv and argv[0].lower().endswith('.exe'):
        with open(argv[0], 'rb') as f:
            if f.read(2) == b'MZ':
                return ['wine'] + list(argv)
    return list(argv)


def fmt(secs):
    return '%dm%02ds' % divmod(int(secs), 60) if secs >= 60 else '%.1fs' % secs


def disk_mb(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1048576.0


def default_jobs():
    """min(24, cpus); off Windows also about 4 GB of RAM per compiler job: the largest lifted files take 3-4 GB each at -O1,
    and a machine that runs out is not slowed down but has processes killed (seen with 22 jobs on 31 GB)."""
    jobs = min(24, os.cpu_count() or 8)
    try:
        with open('/proc/meminfo') as f:
            kb = next(int(l.split()[1]) for l in f if l.startswith('MemTotal:'))
        jobs = max(1, min(jobs, kb // (4 << 20)))
    except (OSError, StopIteration, ValueError):
        pass
    return jobs


class Ctx:
    def __init__(self, a):
        self.a = a
        self.linux = getattr(a, 'target', 'windows') == 'linux'
        self.build = os.path.abspath(a.build or os.path.join(REPO, 'build-linux' if self.linux else 'build'))
        self.work = os.path.join(self.build, 'work')
        self.bin = os.path.join(self.build, 'binaries', '13.0.4')
        self.logs = os.path.join(self.build, 'logs')
        self.state_file = os.path.join(self.build, 'state.json')
        self.state = {}
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                self.state = json.load(f)
        self.jobs = a.jobs or default_jobs()
        self.tools = {}                          # located tools, filled by deps

    def save(self):
        os.makedirs(self.build, exist_ok=True)
        with open(self.state_file, 'w') as f:
            json.dump(self.state, f, indent=1)

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(('RECOMP_', 'SSBU_'))}
        env['PYTHONIOENCODING'] = 'utf-8'
        env['PYTHONUNBUFFERED'] = '1'
        t = self.tools
        if 'clang' in t:
            env['RECOMP_CLANG'] = t['clang']
        if 'sdl3' in t:
            env['SDL3_ROOT'] = t['sdl3']
        if 'vulkan' in t:
            env['VULKAN_HEADERS'] = t['vulkan']
        if 'glslang' in t:
            env['GLSLANG_ROOT'] = t['glslang']
        if 'suyu' in t:
            env['SUYU_ROOT'] = t['suyu']
        env.update(extra)
        return env

    def run(self, step, argv, cwd=None, env=None, log=None, ok=(0,)):
        """Run a child, its output to logs/<log or step>.log; returns this run's output (its last 256 KB). Raises Fail on a
        bad exit code."""
        os.makedirs(self.logs, exist_ok=True)
        path = os.path.join(self.logs, (log or step) + '.log')
        t0 = time.time()
        with open(path, 'ab') as f:
            f.write(('\n$ %s\n' % ' '.join(argv)).encode())
            f.flush()                            # the header goes above the child's output, not after it
            start = f.tell()                     # the log is appended to: only what this child wrote is returned, or a
            p = subprocess.run(winexe(argv), cwd=cwd or self.work, env=env or self.env(), stdout=f, stderr=subprocess.STDOUT)      # caller's check reads the result of an earlier, failed run
        with open(path, 'rb') as f:
            f.seek(max(start, os.path.getsize(path) - (256 << 10)))
            tail = f.read().decode('utf-8', 'replace')
        if p.returncode not in ok:
            raise Fail('%s failed (exit %s) after %s; see %s\n%s' % (' '.join(argv[:3]), p.returncode, fmt(time.time() - t0), path, tail[-1500:]))
        return tail


def find_first(cands, probe):
    for c in cands:
        if c and os.path.isdir(c) and probe(c):
            return os.path.abspath(c)
    return None


# ---------------------------------------------------------------------------------------------- steps
def step_deps(cx):
    a = cx.a
    say('  python %s' % sys.version.split()[0])
    if cx.linux:
        return deps_linux(cx)
    missing = []
    for mod, pkg in (('numpy', 'numpy'), ('lz4.block', 'lz4'), ('zstandard', 'zstandard'), ('PIL', 'Pillow'), ('unicorn', 'unicorn')):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        raise Fail('missing Python packages: %s   (pip install %s)' % (', '.join(missing), ' '.join(missing)))
    say('  python packages: numpy lz4 zstandard Pillow unicorn: ok')

    deps = os.path.join(REPO, 'deps')
    llvm = find_first([a.llvm, os.environ.get('LLVM_ROOT'), r'C:\llvm-9.0.0', r'C:\Program Files\LLVM', os.path.join(deps, 'llvm')],
                      lambda d: os.path.exists(os.path.join(d, 'bin', 'clang.exe')))
    if not llvm:
        raise Fail('LLVM/clang 9.0.0 not found (--llvm <folder with bin\\clang.exe>)')
    clang = os.path.join(llvm, 'bin', 'clang.exe')
    ver = subprocess.run([clang, '--version'], capture_output=True, text=True).stdout.splitlines()[0]
    say('  clang: %s   [%s]' % (ver, clang))
    if 'version 9.' not in ver and 'version 8.' not in ver:
        say('  WARNING: the lifted code was only ever compiled with clang 8 and 9; %s is untested' % ver)
    cx.tools['clang'] = clang
    probe = os.path.join(cx.build, 'probe')
    os.makedirs(probe, exist_ok=True)
    with open(os.path.join(probe, 'p.c'), 'w') as f:
        f.write('#include <windows.h>\n#include <stdio.h>\nint main(void){puts("ok");return 0;}\n')
    r = subprocess.run([clang, os.path.join(probe, 'p.c'), '-o', os.path.join(probe, 'p.exe'), '-luser32'], capture_output=True, text=True)
    if r.returncode or subprocess.run(winexe([os.path.join(probe, 'p.exe')]), capture_output=True, text=True).stdout.strip() != 'ok':
        raise Fail('clang cannot compile and link a Windows program: install the MSVC Build Tools (C++ x64 tools) and a Windows 10/11 SDK\n' + r.stderr[-800:])
    say('  clang + MSVC linker + Windows SDK: ok')

    for key, opt, envv, sub, probe_rel in (
            ('sdl3', a.sdl3, 'SDL3_ROOT', 'SDL3', os.path.join('include', 'SDL3', 'SDL.h')),
            ('vulkan', a.vulkan_headers, 'VULKAN_HEADERS', 'Vulkan-Headers', os.path.join('include', 'vulkan', 'vulkan.h')),
            ('glslang', a.glslang, 'GLSLANG_ROOT', 'glslang', os.path.join('include', 'glslang', 'Include', 'glslang_c_interface.h')),
            ('suyu', a.suyu, 'SUYU_ROOT', 'suyu', os.path.join('src', 'core', 'hle', 'service', 'mii', 'types', 'raw_data.cpp'))):
        found = find_first([opt, os.environ.get(envv), os.path.join(deps, sub)], lambda d, p=probe_rel: os.path.exists(os.path.join(d, p)))
        if not found:
            raise Fail('%s not found (--%s <folder containing %s>)' % (sub, {'sdl3': 'sdl3', 'vulkan': 'vulkan-headers', 'glslang': 'glslang', 'suyu': 'suyu'}[key], probe_rel))
        cx.tools[key] = found
        say('  %-8s %s' % (sub, found))
    if not os.path.exists(os.path.join(cx.tools['sdl3'], 'lib', 'x64', 'SDL3.lib')):
        raise Fail('SDL3 folder lacks lib\\x64\\SDL3.lib (use the "VC" development package)')
    if not os.path.exists(os.path.join(cx.tools['glslang'], 'lib', 'glslang.lib')):
        raise Fail('glslang folder lacks lib\\glslang.lib (use the windows-x86_64 release package)')
    if os.name != 'nt':
        say('  MSVC: not needed off Windows (the glslang shim is built with the clang wrapper)')
    else:
        check_msvc()

    # libopus: nnaudio.c loads it at run time to decode the game's Opus streams (the music among them). Optional: without it
    # the game runs and those streams are silent, which is easy to miss, so the deps step says so.
    def opus_in(p):
        if p and os.path.isfile(p):
            return os.path.abspath(p)
        for rel in ('opus.dll', 'libopus-0.dll', os.path.join('bin', 'opus.dll'), os.path.join('bin', 'libopus-0.dll'),
                    os.path.join('Release', 'opus.dll'), os.path.join('build', 'Release', 'opus.dll')):
            if p and os.path.isfile(os.path.join(p, rel)):
                return os.path.abspath(os.path.join(p, rel))
        return None
    opus = next((f for f in map(opus_in, [a.opus, os.environ.get('OPUS_DLL'), os.path.join(deps, 'opus')]) if f), None)
    msys = next((p for p in (r'C:\msys64\ucrt64\bin\libopus-0.dll', r'C:\msys64\mingw64\bin\libopus-0.dll', r'C:\msys64\clang64\bin\libopus-0.dll')
                 if os.path.isfile(p)), None)
    if opus:
        cx.tools['opus'] = opus
        say('  libopus  %s (the link step copies it beside the exe)' % opus)
    elif msys:
        say('  libopus  %s (MSYS2: the runtime loads it from there)' % msys)
    else:
        say('  libopus  NOT FOUND: the game will run, but its music and other Opus sounds will be silent (--opus <opus.dll or its folder>, BUILDING.md)')

    # the dump
    check_dump(cx)


LINUX_PKGS = (('sdl3', 'SDL3'), ('freetype2', 'FreeType'), ('openssl', 'OpenSSL'), ('libavformat', 'FFmpeg'), ('libavcodec', 'FFmpeg'),
              ('libswscale', 'FFmpeg'), ('libswresample', 'FFmpeg'), ('libavutil', 'FFmpeg'))
GLSLANG_LIBS = (['-lglslang', '-lglslang-default-resource-limits'],          # shared glslang (Arch, Fedora)
                ['-lglslang', '-lglslang-default-resource-limits', '-lSPIRV', '-lMachineIndependent', '-lGenericCodeGen',
                 '-lOSDependent', '-lSPIRV-Tools-opt', '-lSPIRV-Tools', '-lstdc++', '-lpthread'])      # static glslang (Debian)
LINUX_HINT = '\n(scripts/linux/install_deps.sh installs everything the Linux build needs; see BUILDING_LINUX.md)'


def deps_linux(cx):
    """--target linux: the system's clang and the development packages of SDL3, FreeType, OpenSSL, FFmpeg, glslang, Vulkan
    and OpenGL; the Opus and Vulkan libraries the game loads at run time. scripts/linux/install_deps.sh installs them."""
    a = cx.a
    missing = []
    for mod, pkg in (('numpy', 'numpy'), ('lz4.block', 'lz4'), ('zstandard', 'zstandard'), ('PIL', 'Pillow'), ('unicorn', 'unicorn')):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        raise Fail('missing Python packages: %s (run setup.py with deps/venv/bin/python)%s' % (', '.join(missing), LINUX_HINT))
    say('  python packages: numpy lz4 zstandard Pillow unicorn: ok')
    clang = os.path.join(a.llvm, 'bin', 'clang') if a.llvm else shutil.which('clang')
    if not clang or not os.path.exists(clang):
        raise Fail('clang not found (install clang, or --llvm <folder with bin/clang>)' + LINUX_HINT)
    ver = subprocess.run([clang, '--version'], capture_output=True, text=True).stdout.splitlines()[0]
    say('  clang: %s   [%s]' % (ver, clang))
    try:
        major = int(ver.split('version ')[1].split('.')[0])
    except (IndexError, ValueError):
        major = 0
    if major and major < 19:
        raise Fail('clang %d is too old: the Linux build needs clang 19 or newer (C23 typeof_unqual)' % major)
    cx.tools['clang'] = clang
    if not shutil.which('pkg-config'):
        raise Fail('pkg-config not found' + LINUX_HINT)
    for pc, name in LINUX_PKGS:
        r = subprocess.run(['pkg-config', '--modversion', pc], capture_output=True, text=True)
        if r.returncode:
            raise Fail('development files of %s (%s) not found by pkg-config%s' % (name, pc, LINUX_HINT))
        say('  %-13s %s' % (pc, r.stdout.strip()))
    for h, name in (('/usr/include/glslang/Include/glslang_c_interface.h', 'glslang'), ('/usr/include/GL/gl.h', 'OpenGL (libGL)')):
        if not os.path.exists(h):
            raise Fail('%s headers not found (%s)%s' % (name, h, LINUX_HINT))
    # one program linked against every library of the build, and run: a missing library shows here, not after an hour
    probe = os.path.join(cx.build, 'probe')
    os.makedirs(probe, exist_ok=True)
    with open(os.path.join(probe, 'p.c'), 'w') as f:
        f.write('#include <stdio.h>\n#include <SDL3/SDL.h>\n#include <ft2build.h>\n#include FT_FREETYPE_H\n#include <openssl/evp.h>\n'
                '#include <libavcodec/avcodec.h>\n#include <glslang/Include/glslang_c_interface.h>\n#include <GL/gl.h>\n'
                'int main(void) {\n    FT_Library ft;\n    if (FT_Init_FreeType(&ft)) return 1;\n'
                '    if (!avcodec_find_decoder(AV_CODEC_ID_VP9)) { puts("noVP9"); return 0; }\n'
                '    if (!avcodec_find_decoder(AV_CODEC_ID_VORBIS)) { puts("noVorbis"); return 0; }\n'
                '    printf("%d %p %p %p\\n", SDL_GetVersion(), (void *)EVP_sha256, (void *)glslang_initialize_process, (void *)glGetString);\n'
                '    puts("ok");\n    return 0;\n}\n')
    flags = subprocess.run(['pkg-config', '--cflags', '--libs'] + [p for p, _ in LINUX_PKGS], capture_output=True, text=True).stdout.split()
    err = ''
    for libs in GLSLANG_LIBS:
        r = subprocess.run([clang, os.path.join(probe, 'p.c'), '-o', os.path.join(probe, 'p')] + flags + libs + ['-lGL', '-lm'], capture_output=True, text=True)
        if not r.returncode:
            cx.tools['glslang_libs'] = libs
            break
        err = r.stderr
    else:
        raise Fail('a test program using SDL3, FreeType, OpenSSL, FFmpeg, glslang and OpenGL does not build:\n%s%s' % (err[-1200:], LINUX_HINT))
    out = subprocess.run([os.path.join(probe, 'p')], capture_output=True, text=True).stdout.strip().splitlines()
    if out and out[-1].startswith('no'):
        raise Fail('this FFmpeg has no %s decoder: the game\'s movies are VP9 video with Vorbis audio (install the full FFmpeg of your distribution)' % out[-1][2:])
    if not out or out[-1] != 'ok':
        raise Fail('the test program built against the libraries does not run (output %r)' % out)
    say('  test program with SDL3, FreeType, OpenSSL, FFmpeg (VP9 + Vorbis), glslang, OpenGL: builds and runs')
    import ctypes.util
    for lib, name in (('vulkan', 'the Vulkan loader (libvulkan.so.1)'), ('opus', 'the Opus library (libopus.so.0)')):
        if not ctypes.util.find_library(lib):
            raise Fail('%s not found: the game loads it at run time%s' % (name, LINUX_HINT))
    say('  run-time libraries: libvulkan, libopus: ok')
    deps = os.path.join(REPO, 'deps')
    for key, opt, envv, sub, probe_rel in (
            ('vulkan', a.vulkan_headers, 'VULKAN_HEADERS', 'Vulkan-Headers', os.path.join('include', 'vulkan', 'vulkan.h')),
            ('suyu', a.suyu, 'SUYU_ROOT', 'suyu', os.path.join('src', 'core', 'hle', 'service', 'mii', 'types', 'raw_data.cpp'))):
        found = find_first([opt, os.environ.get(envv), os.path.join(deps, sub), '/usr' if key == 'vulkan' else None], lambda d, p=probe_rel: os.path.exists(os.path.join(d, p)))
        if not found:
            raise Fail('%s not found (--%s <folder containing %s>)%s' % (sub, {'vulkan': 'vulkan-headers', 'suyu': 'suyu'}[key], probe_rel, LINUX_HINT))
        cx.tools[key] = found
        say('  %-13s %s' % (sub, found))
    check_dump(cx)


def check_msvc():
    vsw = os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'), 'Microsoft Visual Studio', 'Installer', 'vswhere.exe')
    vs = subprocess.run([vsw, '-latest', '-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64', '-property', 'installationPath'],
                        capture_output=True, text=True).stdout.strip() if os.path.exists(vsw) else ''
    if not vs:
        raise Fail('Visual Studio / Build Tools with "MSVC v14x x64/x86 build tools" not found (needed for the glslang shim)')
    say('  MSVC (glslang shim): %s' % vs)


def check_dump(cx):
    a = cx.a
    dump = a.dump
    exefs = a.exefs or (os.path.join(dump, 'exefs') if dump else None)
    romfs = a.romfs or (os.path.join(dump, 'romfs') if dump else None)
    if not exefs or not romfs:
        raise Fail('--dump <folder> is required (or --exefs and --romfs)')
    for f in ('main', 'sdk'):
        if not os.path.isfile(os.path.join(exefs, f)):
            raise Fail('%s not found: the ExeFS folder needs the files main and sdk' % os.path.join(exefs, f))
    if not os.path.isfile(os.path.join(romfs, 'data.arc')):
        raise Fail('%s not found: the RomFS folder needs data.arc' % os.path.join(romfs, 'data.arc'))
    cx.state['dump'] = {'exefs': os.path.abspath(exefs), 'romfs': os.path.abspath(romfs)}
    sysdata = a.sysdata or (os.path.join(dump, 'sysdata') if dump else None)
    if sysdata and os.path.isdir(os.path.join(sysdata, 'fonts')):
        cx.state['dump']['sysdata'] = os.path.abspath(sysdata)
        say('  dump: exefs, romfs, sysdata (fonts%s)' % (', zoneinfo' if os.path.isdir(os.path.join(sysdata, 'zoneinfo')) else ''))
    else:
        say('  dump: exefs, romfs   (no sysdata%sfonts: the game needs the console\'s shared fonts; %s)' % (os.sep, 'step stage puts stand-ins from your system fonts in their place' if os.name != 'nt' else 'see BUILDING.md'))
    if a.sdk_roots:
        if not os.path.isfile(a.sdk_roots):
            raise Fail('--sdk-roots file not found')
        cx.state['sdk_roots'] = os.path.abspath(a.sdk_roots)
    elif not cx.state.get('sdk_roots'):
        cx.state['sdk_roots'] = os.path.join(REPO, 'tools', 'lifter', 'sdklift_roots.txt')
    cx.state['tools'] = cx.tools


def copy_if_changed(src, dst):
    if os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src):
        with open(src, 'rb') as a, open(dst, 'rb') as b:
            if a.read() == b.read():
                return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def step_stage(cx):
    """The sources are laid out the way the tools expect them (one folder, ro_study\\, tools_audit\\, fighter_sdk\\, tests\\),
    next to binaries\\ and romfs: the tools address the dump by relative paths."""
    plan = [('runtime', '', None), ('tools/lifter', '', None), ('tools/nro', 'ro_study', '.py'), ('tools/audit', 'tools_audit', '.py'),
            ('fighter_sdk', 'fighter_sdk', None), ('tests', 'tests', None), ('mods', '', None)]
    n = changed = 0
    for src, dst, ext in plan:
        for root, dirs, files in os.walk(os.path.join(REPO, src)):
            dirs[:] = [d for d in dirs if d != '__pycache__']
            for f in files:
                if ext and not f.endswith(ext):
                    continue
                rel = os.path.relpath(os.path.join(root, f), os.path.join(REPO, src))
                n += 1
                changed += copy_if_changed(os.path.join(root, f), os.path.join(cx.work, dst, rel))
    for d in ('obj_host', os.path.join('tests', 'logs')):
        os.makedirs(os.path.join(cx.work, d), exist_ok=True)
    say('  %d source files, %d copied into %s' % (n, changed, cx.work))
    if not cx.state['dump'].get('sysdata') and not os.path.isdir(os.path.join(cx.build, 'sysdata', 'fonts')) and os.name != 'nt':
        standin_fonts(cx)


def step_convert(cx):
    d = cx.state['dump']
    os.makedirs(cx.bin, exist_ok=True)
    conv = os.path.join(REPO, 'tools', 'extract', 'nso2elf.py')
    for name, want in (('main', GAME_TEXT_SIZE), ('sdk', SDK_TEXT_SIZE)):
        out = cx.run('convert', [sys.executable, conv, os.path.join(d['exefs'], name), os.path.join(cx.bin, name + '.elf')], cwd=REPO, log='convert_' + name)
        seg0 = next((l for l in out.splitlines() if l.startswith('seg 0')), '')
        got = int(seg0.split('size ')[1].split()[0], 16) if 'size ' in seg0 else 0
        if 'MISMATCH' in out or got != want:
            raise Fail('%s is not the expected build (code size %#x, expected %#x for 13.0.4, or a segment hash mismatch): '
                       'this port only handles Super Smash Bros. Ultimate 13.0.4 (update applied)' % (name, got, want))
        say('  %s -> %s.elf   code %.1f MB, segment hashes ok' % (name, name, got / 1048576.0))
    for name in os.listdir(d['exefs']):                 # main.npdm (main thread stack size) and the other ExeFS files sit beside the ELFs
        if os.path.isfile(os.path.join(d['exefs'], name)):
            shutil.copy2(os.path.join(d['exefs'], name), os.path.join(cx.bin, name))
    if not os.path.isfile(os.path.join(cx.bin, 'main.npdm')):
        raise Fail('exefs\\main.npdm is missing: dump the whole ExeFS (main, sdk, rtld, subsdk0, main.npdm)')
    if d.get('sysdata'):
        for sub in ('fonts', 'zoneinfo'):
            if os.path.isdir(os.path.join(d['sysdata'], sub)):
                dst = os.path.join(cx.build, 'sysdata', sub)
                if os.path.exists(os.path.join(dst, STANDIN_MARK)):          # real fonts replace the stand-ins of an earlier run
                    shutil.rmtree(dst)
                if not os.path.isdir(dst):
                    shutil.copytree(os.path.join(d['sysdata'], sub), dst)


# The game's shared-font files (nn::pl) and which stand-in each gets: the system's own TrueType fonts, found with fontconfig.
SHARED_FONTS = (('nintendo_udsg-r_std_003.ttf', 'text'), ('nintendo_udsg-r_org_zh-cn_003.ttf', 'text'),
                ('nintendo_udsg-r_ext_zh-cn_003.ttf', 'text'), ('nintendo_udjxh-db_zh-tw_003.ttf', 'text'),
                ('nintendo_udsg-r_ko_003.ttf', 'text'), ('nintendo_ext_003.ttf', 'symbols'), ('nintendo_ext2_003.ttf', 'symbols'))
STANDIN_MARK = 'STAND-IN FONTS.txt'


def truetype_font(families):
    """The file of the first installed font of these families that is a single TrueType font (.ttf with outlines in a
    'glyf' table: the game's font engine reads no CFF outlines and no collections)."""
    if not shutil.which('fc-match'):
        return None
    for fam in families:
        r = subprocess.run(['fc-match', '-f', '%{file}', fam], capture_output=True, text=True)
        path = r.stdout.strip()
        if r.returncode or not path.lower().endswith('.ttf') or not os.path.isfile(path):
            continue
        with open(path, 'rb') as f:
            head = f.read(12 + 16 * 64)
        if head[:4] == b'\0\1\0\0' and b'glyf' in head[12:]:
            return path
    return None


def standin_fonts(cx):
    """No firmware fonts in the dump: the game cannot start without the console's shared fonts (nn::pl stops it), so
    build/sysdata/fonts is filled with the system's own fonts under the console's file names. Menus are then readable
    (Latin script only; the button icons drawn from the console's symbol font are missing). Fonts from a firmware dump
    (tools/extract/fw_fonts.py, then --sysdata or <dump>/sysdata/fonts) replace them on the next run of this step."""
    pick = {'text': truetype_font(('Noto Sans', 'DejaVu Sans', 'Liberation Sans', 'sans-serif')),
            'symbols': truetype_font(('Noto Sans Symbols2', 'Noto Sans Symbols', 'DejaVu Sans', 'Noto Sans', 'sans-serif'))}
    if not pick['text']:
        say('  WARNING: no firmware fonts in the dump and no TrueType system font found (install Noto Sans or DejaVu Sans):')
        say('           the game stops at start until build/sysdata/fonts holds the shared fonts (BUILDING_LINUX.md, "Fonts")')
        return
    dst = os.path.join(cx.build, 'sysdata', 'fonts')
    os.makedirs(dst, exist_ok=True)
    for name, kind in SHARED_FONTS:
        shutil.copy2(pick[kind] or pick['text'], os.path.join(dst, name))
    with open(os.path.join(dst, STANDIN_MARK), 'w') as f:
        f.write('These are NOT the console\'s fonts: copies of %s and %s under the console\'s file names, made by\n'
                'scripts/setup.py because the dump had no sysdata/fonts. Fonts extracted from your own firmware dump\n'
                '(tools/extract/fw_fonts.py) replace them: setup.py --sysdata <folder with fonts/> --redo convert\n' % (pick['text'], pick['symbols'] or pick['text']))
    say('  no firmware fonts in the dump: STAND-IN fonts in %s (%s; symbols %s)' % (dst, os.path.basename(pick['text']), os.path.basename(pick['symbols'] or pick['text'])))


def step_modules(cx):
    ro = os.path.join(cx.work, 'ro_study')
    env = cx.env(SSBU_ARC=os.path.join(cx.state['dump']['romfs'], 'data.arc'))
    if not os.path.isdir(os.path.join(ro, 'nro')) or not any(f.endswith('.nro') for f in os.listdir(os.path.join(ro, 'nro'))):
        cx.run('modules', [sys.executable, 'arc_extract.py', 'nro', 'nro'], cwd=ro, env=env)
    n = len([f for f in os.listdir(os.path.join(ro, 'nro')) if f.endswith('.nro')])
    cx.run('modules', [sys.executable, 'lift_all.py', 'map'], cwd=ro, env=env)
    cx.run('modules', [sys.executable, 'build_nro.py'], cwd=cx.work)          # writes nro_registry.c from ro_study\nro_map.json
    say('  %d script modules extracted from data.arc, nro_map.json and nro_registry.c written' % n)


STUB_LIST = '/* no audit data: the strict-mode lists are empty (the port runs the same; SSBU_STRICT then reports nothing) */\nstatic const char *const %s[] = {\n    "",\n};\n'


def step_tables(cx):
    w = cx.work
    py = sys.executable
    cx.run('tables', [py, 'gen_imports.py'])
    cx.run('tables', [py, 'gen_sdkdata.py'])
    cx.run('tables', [py, 'gen_sdktest.py'])
    cx.run('tables', [py, 'gen_quad_tests.py'])
    cx.run('tables', [py, 'gen_eh_tests.py'])
    for name, var in (('fake_whitelist.h', 'audit_whitelist'), ('fake_openlist.h', 'audit_openlist')):
        if not os.path.exists(os.path.join(w, name)):
            with open(os.path.join(w, name), 'w', newline='\n') as f:
                f.write(STUB_LIST % var)
    say('  imports_all.txt, sdkdata.c, sdktest_data.h, quad_tests.h, eh_tests.h, strict-mode lists (empty)')


def step_lift(cx):
    out = cx.run('lift', [sys.executable, 'recomp_eh.py', 'gen_eh_o2', str(cx.jobs)])
    n = len([f for f in os.listdir(os.path.join(cx.work, 'gen_eh_o2')) if f.endswith('.c')])
    say('  main lifted into gen_eh_o2: %d C files (%.0f MB)' % (n, disk_mb(os.path.join(cx.work, 'gen_eh_o2'))))


def install_header(roots_text):
    """sdklift_install.h = the names of the managed block of sdklift_roots.txt (what tools/audit/lift_select.py writes)."""
    import re
    blk = re.search(r'# >>> audit block.*?# <<< audit block', roots_text, re.S)
    names = []
    if blk:
        names = [l.strip() for l in blk.group(0).splitlines() if l.strip() and not l.startswith('#') and l.split()[0] not in ('cut', 'lift', 'trap', 'addr')]
    return ('/* generated by scripts/setup.py from the managed block of sdklift_roots.txt: imports whose handler is the real code lifted from sdk.elf,\n'
            ' * installed over the host re-implementation by nnsdk2.c */\nstatic const char *const sdk_install_names[] = {\n' +
            ''.join('    "%s",\n' % n for n in names) + '};\n')


def step_sdk(cx):
    roots = cx.state.get('sdk_roots')
    if not roots or not os.path.isfile(roots):
        raise Fail('the list of SDK functions to lift was not found (tools/lifter/sdklift_roots.txt, or --sdk-roots <file>)')
    shutil.copyfile(roots, os.path.join(cx.work, 'sdklift_roots.txt'))
    with open(roots, encoding='utf-8', errors='replace') as f:
        text = f.read()
    with open(os.path.join(cx.work, 'sdklift_install.h'), 'w', newline='\n') as f:
        f.write(install_header(text))
    out = cx.run('sdk', [sys.executable, 'sdklift.py', 'gen'])
    line = next((l for l in out.splitlines() if l.startswith('functions lifted')), '')
    say('  ' + (line or 'sdklift_gen.c written'))


def build_env(cx):
    if cx.linux:
        return cx.env(RECOMP_GEN='gen_eh_o2', RECOMP_OBJ='obj_eh_o1', RECOMP_DLL=game_lib(cx), RECOMP_NRO_SUFFIX='_eh_o2', RECOMP_TARGET='linux')
    return cx.env(RECOMP_GEN='gen_eh_o2', RECOMP_OBJ='obj_eh_o1', RECOMP_DLL='ssbu_game_eh_o1.dll', RECOMP_NRO_SUFFIX='_eh_o2')


def step_compile(cx):
    cx.run('compile', [sys.executable, 'build_opt.py', 'main', str(cx.jobs)], env=build_env(cx))
    dll = os.path.join(cx.work, game_lib(cx))
    say('  %s %.0f MB; objects %.0f MB' % (game_lib(cx), os.path.getsize(dll) / 1048576.0, disk_mb(os.path.join(cx.work, 'obj_eh_o1'))))


def step_nro(cx):
    mods = [m for m in (cx.a.modules or 'all').split(',') if m]
    argv = [sys.executable, 'build_opt.py', 'nro', str(cx.jobs)] + ([] if mods == ['all'] else mods)
    cx.run('nro', argv, env=build_env(cx))
    d = os.path.join(cx.work, 'nro_bin_eh_o2')
    say('  %d module libraries in nro_bin_eh_o2 (%.0f MB)' % (len([f for f in os.listdir(d) if f.endswith('.so' if cx.linux else '.dll')]), disk_mb(d)))


def step_link(cx):
    if cx.linux:
        return link_linux(cx)
    if os.name != 'nt':      # what build_glslang.bat does with cl, with the clang wrapper
        gl = cx.tools['glslang']
        os.makedirs(os.path.join(cx.work, 'obj_host'), exist_ok=True)
        cx.run('link', [cx.tools['clang'], '-O2', '-shared', '-fms-runtime-lib=dll', '-I', os.path.join(gl, 'include'), 'vk_glslang_shim.c',
                        '-o', 'ssbu_glslang.dll', '-L', os.path.join(gl, 'lib')] +
               ['-l' + l for l in ('glslang', 'glslang-default-resource-limits', 'SPIRV-Tools-opt', 'SPIRV-Tools')])
    else:
        cx.run('link', [os.environ.get('COMSPEC', 'cmd.exe'), '/c', os.path.join(cx.work, 'build_glslang.bat')], env=cx.env())
    cx.run('link', [sys.executable, 'build.py', 'link'], env=cx.env(**{k: v for k, v in build_env(cx).items() if k != 'RECOMP_NRO_SUFFIX'}, RECOMP_EXE=EXE))
    exe = os.path.join(cx.work, EXE)
    for f in (EXE, 'SDL3.dll', 'ssbu_glslang.dll', 'ssbu_game_eh_o1.dll'):
        if not os.path.exists(os.path.join(cx.work, f)):
            raise Fail('%s was not produced' % f)
    opus = cx.tools.get('opus')
    if opus:                                            # under a name nnaudio.c looks for (opus.dll or libopus-0.dll)
        name = os.path.basename(opus) if os.path.basename(opus).lower() in ('opus.dll', 'libopus-0.dll') else 'opus.dll'
        shutil.copy2(opus, os.path.join(cx.work, name))
    say('  %s %.1f MB, SDL3.dll, ssbu_glslang.dll, ssbu_game_eh_o1.dll%s beside it' % (EXE, os.path.getsize(exe) / 1048576.0,
                                                                                         ', ' + name if opus else ''))
    if not opus:
        say('  WARNING: no libopus: music and the other Opus sounds will be silent (deps step above; BUILDING.md)')


def link_linux(cx):
    cx.run('link', [cx.tools['clang'], '-O2', '-shared', '-fPIC', 'vk_glslang_shim.c', '-o', 'ssbu_glslang.so'] + cx.tools.get('glslang_libs', GLSLANG_LIBS[0]))
    cx.run('link', [sys.executable, 'build.py', 'link'], env=cx.env(**{k: v for k, v in build_env(cx).items() if k != 'RECOMP_NRO_SUFFIX'}, RECOMP_EXE=LINUX_EXE))
    for f in (LINUX_EXE, 'ssbu_glslang.so', game_lib(cx)):
        if not os.path.exists(os.path.join(cx.work, f)):
            raise Fail('%s was not produced' % f)
    say('  %s %.1f MB, ssbu_glslang.so, %s beside it' % (LINUX_EXE, os.path.getsize(os.path.join(cx.work, LINUX_EXE)) / 1048576.0, game_lib(cx)))


def game_env(cx, **extra):
    d = cx.state['dump']
    e = cx.env(SSBU_ROMFS=d['romfs'], **extra)
    if os.path.isdir(os.path.join(cx.build, 'sysdata', 'fonts')):
        e['SSBU_FONTS'] = os.path.join(cx.build, 'sysdata', 'fonts')
    if os.path.isdir(os.path.join(cx.build, 'sysdata', 'zoneinfo')):
        e['SSBU_TZDIR'] = os.path.join(cx.build, 'sysdata', 'zoneinfo')
    return e


def step_check(cx):
    exe = os.path.join(cx.work, exe_name(cx))
    main_elf = os.path.join(cx.bin, 'main.elf')
    scratch = os.path.join(cx.build, 'selftest_userdata')
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch)
    # the self-test exits with its number of failures (a crash is a negative exit code: a signal)
    out = cx.run('check', [exe, main_elf, '60'], env=game_env(cx, SSBU_SDK_SELFTEST='1', SSBU_USERDATA=scratch), log='check_sdk', ok=tuple(range(256)))
    line = next((l for l in reversed(out.splitlines()) if l.startswith('SDK self-test:')), '')
    failed = [l.strip() for l in out.splitlines() if l.rstrip().endswith('FAILED')]
    known = [l for l in failed if cx.linux and l.startswith('album:')]     # recording movies to the album: not on Linux yet
    if '0 failure(s)' not in line and not (failed and len(known) == len(failed) and ('%d failure(s)' % len(failed)) in line):
        raise Fail('SSBU_SDK_SELFTEST: ' + (line or 'no result line'))
    say('  SSBU_SDK_SELFTEST: ' + line.strip())
    if known:
        say('  (the %d failures are all album movie recording, which the Linux build does not have yet: accepted)' % len(known))
    out = cx.run('check', [exe, main_elf, '5'], env=game_env(cx, SSBU_PAD_SELFTEST='1', SSBU_USERDATA=scratch), log='check_pad')
    line = next((l for l in reversed(out.splitlines()) if 'pad' in l.lower() and '/' in l), out.strip().splitlines()[-1] if out.strip() else '')
    say('  SSBU_PAD_SELFTEST: ' + line.strip())
    # the lifter's differential test against the Unicorn emulator, on a sample of the instruction shapes of main
    py, w = sys.executable, cx.work
    cx.run('check', [py, 'difftest.py', 'sample'], log='check_difftest')
    cx.run('check', [py, 'difftest.py', 'gen'], log='check_difftest')
    dt = 'difftest' if cx.linux else 'difftest.exe'
    cx.run('check', [cx.tools['clang'], '-O0', '-w', '-fno-strict-aliasing', '-I', '.', 'difftest_gen.c', 'difftest_host.c', '-o', dt] + (['-lm'] if cx.linux else []), log='check_difftest')
    out = cx.run('check', [py, 'difftest.py', 'check'], env=cx.env(DIFFTEST_EXE=os.path.join(w, dt)), log='check_difftest')
    line = next((l for l in out.splitlines() if l.startswith('tests:')), '')
    if not line:
        raise Fail('difftest printed no result')
    say('  differential test: ' + line.strip())
    if 'different: 0' not in line:
        say('  WARNING: the differential test reports differences (see logs/check_difftest.log)')


RUNNERS = {'deps': step_deps, 'stage': step_stage, 'convert': step_convert, 'modules': step_modules, 'tables': step_tables,
           'lift': step_lift, 'sdk': step_sdk, 'compile': step_compile, 'nro': step_nro, 'link': step_link, 'check': step_check}
ALWAYS = {'deps', 'stage'}               # cheap, and they fill the in-memory context


def how_to_run(cx):
    d = cx.state.get('dump', {})
    say('')
    say('Start the game (from a shell; the window opens at once, the first run compiles shaders and is slow):')
    if cx.linux:
        say('  deps/venv/bin/python scripts/run.py --linux        (saves in ~/.local/share/ssbu-recomp/userdata)')
        say('  scripts/linux/install_desktop.sh                   adds a launcher to your application menu')
        if os.path.exists(os.path.join(cx.build, 'sysdata', 'fonts', STANDIN_MARK)):
            say('  the fonts are stand-ins from your system (see build-linux/sysdata/fonts/%s)' % STANDIN_MARK)
        say('or by hand:   cd %s   and run   ./%s ../binaries/13.0.4/main.elf 0' % (cx.work, LINUX_EXE))
        say('with the environment variables SSBU_ROMFS=%s  SSBU_USERDATA=<saves>  SSBU_FONTS=%s' % (d.get('romfs', '<romfs>'), os.path.join(cx.build, 'sysdata', 'fonts')))
        return
    say('  python scripts/run.py --userdata <a folder for your saves> [--seconds 0]')
    say('or by hand:   cd build\\work   and run   %s ..\\binaries\\13.0.4\\main.elf 0' % EXE)
    say('with the environment variables SSBU_ROMFS=%s  SSBU_USERDATA=<saves>  (options: BUILDING.md)' % d.get('romfs', '<romfs>'))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dump', help='folder with exefs\\ and romfs\\ (and optionally sysdata\\) dumped from your console')
    ap.add_argument('--exefs'), ap.add_argument('--romfs'), ap.add_argument('--sysdata')
    ap.add_argument('--target', choices=('windows', 'linux'), default='windows', help='windows (default: the Windows exe; off Windows a cross build run through Wine) or linux (a native Linux build)')
    ap.add_argument('--build', help='build folder (default <repo>/build, <repo>/build-linux for --target linux)')
    ap.add_argument('--jobs', type=int, help='parallel compiler jobs (default min(24, cpus); off Windows also at most one per 4 GB of RAM)')
    ap.add_argument('--llvm', help='LLVM 9.0.0 folder (bin\\clang.exe)')
    ap.add_argument('--sdl3', help='SDL3 development package (VC) folder: include\\, lib\\x64\\')
    ap.add_argument('--vulkan-headers', help='Khronos Vulkan-Headers folder (include\\vulkan\\)')
    ap.add_argument('--glslang', help='glslang windows-x86_64 release folder (include\\, lib\\)')
    ap.add_argument('--suyu', help='source checkout of a yuzu-family emulator (only src\\core\\hle\\service\\mii is read)')
    ap.add_argument('--opus', help='libopus DLL (opus.dll or libopus-0.dll) or the folder holding it: the music (optional, BUILDING.md)')
    ap.add_argument('--sdk-roots', help='another list of SDK functions to lift (default: tools/lifter/sdklift_roots.txt)')
    ap.add_argument('--modules', help='script modules to build: all (default) or a comma list, e.g. lua2cpp_common,lua2cpp_mario')
    ap.add_argument('--redo', help='repeat this step')
    ap.add_argument('--from', dest='from_step', help='repeat this step and all after it')
    ap.add_argument('--only', help='run only this step')
    ap.add_argument('--status', action='store_true'), ap.add_argument('--steps', action='store_true'), ap.add_argument('--run', action='store_true')
    a = ap.parse_args()
    if a.steps:
        say(' '.join(STEPS))
        return 0
    cx = Ctx(a)
    if a.status:
        for s in STEPS:
            r = cx.state.get('done', {}).get(s)
            say('%-8s %s' % (s, ('done  %s' % fmt(r['secs'])) if r else '-'))
        return 0
    if a.run:
        how_to_run(cx)
        return 0
    todo = STEPS
    done = cx.state.setdefault('done', {})
    force = set()
    for opt in (a.redo, a.from_step, a.only):
        if opt and opt not in STEPS:
            say('unknown step %s (steps: %s)' % (opt, ' '.join(STEPS)))
            return 2
    if a.redo:
        force.add(a.redo)
    if a.from_step:
        force |= set(STEPS[STEPS.index(a.from_step):])
    if a.only:
        todo = sorted({'deps', 'stage', a.only}, key=STEPS.index)
        force.add(a.only)
    t_all = time.time()
    for i, s in enumerate(STEPS):
        if s not in todo:
            continue
        if s in done and s not in force and s not in ALWAYS:
            say('[%d/%d] %s: done earlier (%s), skipped' % (i + 1, len(STEPS), s, fmt(done[s]['secs'])))
            continue
        say('[%d/%d] %s ...' % (i + 1, len(STEPS), s))
        t0 = time.time()
        try:
            RUNNERS[s](cx)
        except Fail as e:
            say('FAILED at step %s: %s' % (s, e))
            say('fix the problem and run the same command again: finished steps are skipped.')
            cx.save()
            return 1
        secs = time.time() - t0
        if s not in ALWAYS or s not in done:
            done[s] = {'secs': secs, 'at': time.strftime('%Y-%m-%d %H:%M')}
        cx.save()
        say('[%d/%d] %s: ok  %s' % (i + 1, len(STEPS), s, fmt(secs)))
    say('')
    say('all steps finished in %s; build folder %.0f MB' % (fmt(time.time() - t_all), disk_mb(cx.build)))
    how_to_run(cx)
    return 0


if __name__ == '__main__':
    sys.exit(main())
