"""Cross-platform local launcher. Uses only Python's standard library to bootstrap."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parent


def environment_python(root=ROOT, windows=None):
    windows = os.name == 'nt' if windows is None else windows
    return root / '.venv' / ('Scripts/python.exe' if windows else 'bin/python')


def prepare(root=ROOT):
    if sys.version_info < (3, 11):
        raise RuntimeError('Python 3.11 or newer is required. Install it from https://www.python.org/downloads/ then try again.')
    python = environment_python(root)
    if not python.exists():
        if (root / '.venv').exists():
            raise RuntimeError('The .venv folder is from another system or is incomplete. Rename/remove only .venv and try again. Keep data/.')
        subprocess.run([sys.executable, '-m', 'venv', str(root / '.venv')], check=True)
    requirements = root / 'requirements.txt'
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = root / '.venv/.requirements-sha256'
    if not stamp.exists() or stamp.read_text().strip() != digest:
        print('Installing dependencies (internet connection required on first run)...', flush=True)
        pip_check = subprocess.run([str(python), '-c', 'import pip'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if pip_check.returncode:
            # Environments made by uv can legitimately have no pip installed.
            subprocess.run([str(python), '-m', 'ensurepip', '--upgrade'], check=True, cwd=root)
        subprocess.run([str(python), '-m', 'pip', 'install', '-r', str(requirements)], check=True, cwd=root)
        stamp.write_text(digest)
    subprocess.run([str(python), '-c', 'import fastapi,uvicorn,pymupdf,pptx,docx,PIL,httpx,multipart'], check=True, cwd=root)
    return python


def healthy(port):
    # System proxies must never receive localhost health checks.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open('http://127.0.0.1:%d/api/health' % port, timeout=1) as response:
            payload = json.load(response)
            return (isinstance(payload, dict) and payload.get('ok') is True
                    and payload.get('service', 'zhixue-notes') == 'zhixue-notes')
    except (OSError, ValueError):
        return False


def free_port(start):
    for port in range(start, min(start + 31, 65536)):
        with socket.socket() as probe:
            if os.name != 'nt':
                # Match uvicorn's POSIX bind semantics after a recent shutdown.
                # TIME_WAIT sockets must not move returning users to a new URL.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
                return port
            except OSError:
                pass
    raise RuntimeError('No free local port found. Set PORT to another port and try again.')


@contextmanager
def startup_lock(root=ROOT, timeout=180):
    """The OS releases this lock even when a launcher is interrupted or crashes."""
    runtime = root / 'data/runtime'
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / 'startup.lock').open('a+b') as handle:
        handle.seek(0, os.SEEK_END)
        if not handle.tell():
            handle.write(b'\0')
            handle.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise RuntimeError('Another startup is still in progress. Wait and try again.')
                time.sleep(.25)
        try:
            yield runtime
        finally:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_UN)


def running_port(runtime, requested):
    """Only probe local ports; never trust a URL or PID in the runtime file."""
    candidates = [requested]
    try:
        recorded = json.loads((runtime / 'server.json').read_text())
        port = recorded.get('port')
        if isinstance(port, int) and not isinstance(port, bool) and 1024 <= port <= 65535:
            candidates.append(port)
    except (OSError, ValueError, AttributeError):
        pass
    return next((port for port in dict.fromkeys(candidates) if healthy(port)), None)


def background_options(windows=None):
    windows = os.name == 'nt' if windows is None else windows
    if windows:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP; no parent console or Ctrl+C.
        return {'creationflags': 0x00000008 | 0x00000200, 'close_fds': True}
    return {'start_new_session': True, 'close_fds': True}


def start_background(command, port, runtime, root=ROOT):
    log_path = runtime / 'server.log'
    # Rotate the previous log only on a fresh server start, retaining diagnostics.
    if log_path.exists() and log_path.stat().st_size > 5 * 1024 * 1024:
        log_path.replace(runtime / 'server.previous.log')
    with log_path.open('ab', buffering=0) as log:
        process = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   **background_options())
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('The local server exited before it was ready. See ' + str(log_path))
        if healthy(port):
            record = runtime / 'server.json'
            temporary = runtime / 'server.json.tmp'
            temporary.write_text(json.dumps({'port': port, 'pid': process.pid}), encoding='utf-8')
            temporary.replace(record)
            return process
        time.sleep(.25)
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    raise RuntimeError('The local server did not become ready. See ' + str(log_path))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Start Zhixue Notes locally.')
    parser.add_argument('--check', action='store_true', help='Install/check dependencies and exit without opening a browser.')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--foreground', action='store_true', help='Keep the server attached to this terminal (also used by DEV=1).')
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    if args.check:
        prepare()
        print('Startup check passed. Your data/ folder has not been changed.')
        return 0
    port = int(os.environ.get('PORT', '8765'))
    if not 1024 <= port <= 65535:
        raise RuntimeError('PORT must be between 1024 and 65535.')
    foreground = args.foreground or os.environ.get('DEV') == '1'
    with startup_lock() as runtime:
        existing = running_port(runtime, port)
        if existing:
            url = 'http://127.0.0.1:%d' % existing
            print('Already running: ' + url)
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        python = prepare()
        port = free_port(port)
        url = 'http://127.0.0.1:%d' % port
        command = [str(python), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)]
        if os.environ.get('DEV') == '1':
            command.append('--reload')
        if not foreground:
            start_background(command, port, runtime)
            print('Zhixue Notes is running in the background: ' + url
                  + '\nYou may close this launcher. Reopen it to reconnect. Log: '
                  + str(runtime / 'server.log'), flush=True)
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        process = subprocess.Popen(command, cwd=ROOT)
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError('The local server exited before it was ready.')
            if healthy(port):
                break
            time.sleep(.5)
        else:
            process.terminate()
            raise RuntimeError('The local server did not become ready.')
        (runtime / 'server.json').write_text(json.dumps({'port': port, 'pid': process.pid}), encoding='utf-8')
    print('Starting Zhixue Notes in this terminal: ' + url + '\nPress Ctrl+C to stop.', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        print('\nStartup failed: ' + str(error), file=sys.stderr)
        sys.exit(1)
