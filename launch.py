"""Cross-platform local launcher. Uses only Python's standard library to bootstrap."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

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
            return json.load(response).get('ok') is True
    except (OSError, ValueError):
        return False


def free_port(start):
    for port in range(start, min(start + 31, 65536)):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
                return port
            except OSError:
                pass
    raise RuntimeError('No free local port found. Set PORT to another port and try again.')


def main():
    parser = argparse.ArgumentParser(description='Start Zhixue Notes locally.')
    parser.add_argument('--check', action='store_true', help='Install/check dependencies and exit without opening a browser.')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    python = prepare()
    if args.check:
        print('Startup check passed. Your data/ folder has not been changed.')
        return 0
    port = int(os.environ.get('PORT', '8765'))
    if not 1024 <= port <= 65535:
        raise RuntimeError('PORT must be between 1024 and 65535.')
    if healthy(port):
        url = 'http://127.0.0.1:%d' % port
        print('Already running: ' + url)
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    port = free_port(port)
    url = 'http://127.0.0.1:%d' % port
    command = [str(python), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)]
    if os.environ.get('DEV') == '1':
        command.append('--reload')
    process = subprocess.Popen(command, cwd=ROOT)
    print('Starting Zhixue Notes: ' + url + '\nKeep this window open while using the app.', flush=True)

    def open_when_ready():
        for _ in range(120):
            if process.poll() is not None:
                return
            if healthy(port):
                webbrowser.open(url)
                return
            time.sleep(.5)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()
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
