#!/usr/bin/env python3
"""ApplyPilot UI – tiny Flask backend that streams CLI output to the browser."""
import json
import os
import re
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_from_directory

# ── Paths ──────────────────────────────────────────────────────────────────────
UI_DIR   = Path(__file__).parent.resolve()
ROOT_DIR = UI_DIR.parent.resolve()

# Find the applypilot executable inside the venv
SCRIPTS  = Path(sys.executable).parent          # .venv/Scripts (win) or .venv/bin (unix)
AP_EXE   = SCRIPTS / ('applypilot.exe' if sys.platform == 'win32' else 'applypilot')
if not AP_EXE.exists():
    AP_EXE = SCRIPTS / 'applypilot'            # fallback without .exe

ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
def strip_ansi(s): return ANSI.sub('', s)

# ── App ────────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=str(UI_DIR), static_url_path='')
CURRENT_PROC = None
PROC_LOCK    = threading.Lock()

@app.route('/')
def index():
    return send_from_directory(str(UI_DIR), 'index.html')

@app.route('/run', methods=['POST'])
def run_cmd():
    global CURRENT_PROC
    data    = request.json or {}
    command = data.get('command', 'doctor')
    args    = data.get('args', [])
    cmd     = [str(AP_EXE), command] + [str(a) for a in args]

    def generate():
        global CURRENT_PROC
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(ROOT_DIR),
                text=True,
                bufsize=1,
                encoding='utf-8',
                errors='replace',
            )
            with PROC_LOCK:
                CURRENT_PROC = proc
        except Exception as exc:
            yield f'data: {json.dumps("[ERROR] " + str(exc))}\n\n'
            yield f'data: {json.dumps("__DONE__")}\n\n'
            return

        for raw in proc.stdout:
            line = strip_ansi(raw.rstrip())
            yield f'data: {json.dumps(line)}\n\n'
        proc.wait()
        with PROC_LOCK:
            CURRENT_PROC = None
        yield f'data: {json.dumps("__DONE__")}\n\n'

    headers = {
        'Cache-Control':    'no-cache',
        'X-Accel-Buffering':'no',
        'Content-Type':     'text/event-stream',
    }
    return Response(generate(), headers=headers)

@app.route('/stop', methods=['POST'])
def stop_cmd():
    global CURRENT_PROC
    with PROC_LOCK:
        if CURRENT_PROC and CURRENT_PROC.poll() is None:
            CURRENT_PROC.terminate()
            CURRENT_PROC = None
            return jsonify({'ok': True,  'message': 'Process stopped.'})
    return jsonify({'ok': False, 'message': 'No running process.'})

@app.route('/env-check')
def env_check():
    ap_dir   = Path.home() / '.applypilot'
    return jsonify({
        'env_exists':       (ap_dir / '.env').exists(),
        'profile_exists':   (ap_dir / 'profile.json').exists(),
        'searches_exists':  (ap_dir / 'searches.yaml').exists(),
        'ap_exe_ok':        AP_EXE.exists(),
    })

if __name__ == '__main__':
    port = 5000
    threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    print(f'\n  ApplyPilot UI  →  http://localhost:{port}')
    print('  Press Ctrl+C to stop.\n')
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
