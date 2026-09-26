#!/usr/bin/env python3
"""ApplyPilot UI – tiny Flask backend that streams CLI output to the browser."""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, Response, jsonify, request, send_from_directory

# ── Paths ──────────────────────────────────────────────────────────────────────
UI_DIR   = Path(__file__).parent.resolve()
ROOT_DIR = UI_DIR.parent.resolve()
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

try:
    from applypilot.database import get_connection, get_stats
    from applypilot.config import DB_PATH, LOG_DIR
except ImportError:
    get_connection = None
    get_stats = None
    DB_PATH = Path.home() / ".applypilot" / "applypilot.db"
    LOG_DIR = Path.home() / ".applypilot" / "logs"

# Find the applypilot executable or module command
def _find_applypilot_cmd():
    exe_name = 'applypilot.exe' if sys.platform == 'win32' else 'applypilot'
    # 1. Workspace .venv
    v_dir = ROOT_DIR / ".venv" / ("Scripts" if sys.platform == 'win32' else "bin")
    c1 = v_dir / exe_name
    if c1.exists():
        return [str(c1)]
    # 2. Current python venv
    c2 = Path(sys.executable).parent / exe_name
    if c2.exists():
        return [str(c2)]
    # 3. PATH
    found = shutil.which('applypilot')
    if found:
        return [found]
    # 4. Fallback to python module execution
    py_bin = str(v_dir / ("python.exe" if sys.platform == 'win32' else "python"))
    if not Path(py_bin).exists():
        py_bin = sys.executable
    return [py_bin, "-m", "applypilot"]

AP_CMD = _find_applypilot_cmd()

ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
def strip_ansi(s): return ANSI.sub('', s)

# ── App ────────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=str(UI_DIR), static_url_path='')
CURRENT_PROC   = None
PROC_INFO      = {}
OUTPUT_HISTORY = []
SUBSCRIBERS    = set()
PROC_LOCK      = threading.Lock()

def _reader_thread(proc):
    global CURRENT_PROC
    try:
        while True:
            line_str = proc.stdout.readline()
            if not line_str:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            line = strip_ansi(line_str.rstrip())
            with PROC_LOCK:
                OUTPUT_HISTORY.append(line)
                for q in list(SUBSCRIBERS):
                    try:
                        q.put_nowait(line)
                    except Exception:
                        pass
    except Exception as exc:
        err_line = f"[ERROR] {exc}"
        with PROC_LOCK:
            OUTPUT_HISTORY.append(err_line)
            for q in list(SUBSCRIBERS):
                try:
                    q.put_nowait(err_line)
                except Exception:
                    pass
    finally:
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        with PROC_LOCK:
            if CURRENT_PROC == proc:
                CURRENT_PROC = None
            for q in list(SUBSCRIBERS):
                try:
                    q.put_nowait('__DONE__')
                except Exception:
                    pass

@app.after_request
def add_no_cache(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/')
def index():
    return send_from_directory(str(UI_DIR), 'index.html')

@app.route('/run', methods=['POST'])
def run_cmd():
    global CURRENT_PROC, PROC_INFO, OUTPUT_HISTORY
    data     = request.json or {}
    command  = data.get('command', 'doctor')
    args     = data.get('args', [])
    stage_id = data.get('stage_id')
    cmd      = AP_CMD + [command] + [str(a) for a in args]

    q = queue.Queue()
    with PROC_LOCK:
        if CURRENT_PROC and CURRENT_PROC.poll() is None:
            SUBSCRIBERS.add(q)
        else:
            CURRENT_PROC = None
            OUTPUT_HISTORY = []
            PROC_INFO = {
                'command':  command,
                'args':     args,
                'stage_id': stage_id,
            }
            sub_env = os.environ.copy()
            sub_env['PYTHONUNBUFFERED'] = '1'
            sub_env['PYTHONUTF8'] = '1'
            sub_env['PYTHONIOENCODING'] = 'utf-8'
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
                    env=sub_env,
                )
                CURRENT_PROC = proc
                SUBSCRIBERS.add(q)
                threading.Thread(target=_reader_thread, args=(proc,), daemon=True).start()
            except Exception as exc:
                def err_gen():
                    yield f'data: {json.dumps("[ERROR] " + str(exc))}\n\n'
                    yield f'data: {json.dumps("__DONE__")}\n\n'
                return Response(err_gen(), headers={
                    'Cache-Control':    'no-cache',
                    'X-Accel-Buffering':'no',
                    'Content-Type':     'text/event-stream',
                })

    def generate():
        try:
            while True:
                item = q.get()
                yield f'data: {json.dumps(item)}\n\n'
                if item == '__DONE__':
                    break
        finally:
            with PROC_LOCK:
                SUBSCRIBERS.discard(q)

    headers = {
        'Cache-Control':    'no-cache',
        'X-Accel-Buffering':'no',
        'Content-Type':     'text/event-stream',
    }
    return Response(generate(), headers=headers)

@app.route('/stream')
def stream_output():
    after = request.args.get('after', default=None, type=int)
    q = queue.Queue()
    with PROC_LOCK:
        is_running = CURRENT_PROC is not None and CURRENT_PROC.poll() is None
        if after is not None and after < len(OUTPUT_HISTORY):
            for line in OUTPUT_HISTORY[after:]:
                q.put_nowait(line)
        if not is_running:
            q.put_nowait('__DONE__')
        else:
            SUBSCRIBERS.add(q)

    def generate():
        try:
            while True:
                item = q.get()
                yield f'data: {json.dumps(item)}\n\n'
                if item == '__DONE__':
                    break
        finally:
            with PROC_LOCK:
                SUBSCRIBERS.discard(q)

    headers = {
        'Cache-Control':    'no-cache',
        'X-Accel-Buffering':'no',
        'Content-Type':     'text/event-stream',
    }
    return Response(generate(), headers=headers)

@app.route('/proc-status')
def proc_status():
    with PROC_LOCK:
        is_running = CURRENT_PROC is not None and CURRENT_PROC.poll() is None
        return jsonify({
            'running':    is_running,
            'command':    PROC_INFO.get('command'),
            'args':       PROC_INFO.get('args', []),
            'stage_id':   PROC_INFO.get('stage_id'),
            'history':    list(OUTPUT_HISTORY),
            'line_count': len(OUTPUT_HISTORY),
        })

@app.route('/stop', methods=['POST'])
def stop_cmd():
    global CURRENT_PROC
    with PROC_LOCK:
        if CURRENT_PROC and CURRENT_PROC.poll() is None:
            proc = CURRENT_PROC
            CURRENT_PROC = None
            try:
                if sys.platform == 'win32':
                    subprocess.call(['taskkill', '/F', '/T', '/PID', str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    proc.terminate()
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            for q in list(SUBSCRIBERS):
                try:
                    q.put_nowait('__DONE__')
                except Exception:
                    pass
            return jsonify({'ok': True,  'message': 'Process stopped.'})
    return jsonify({'ok': False, 'message': 'No running process.'})

@app.route('/env-check')
def env_check():
    ap_dir   = Path.home() / '.applypilot'
    return jsonify({
        'env_exists':       (ap_dir / '.env').exists(),
        'profile_exists':   (ap_dir / 'profile.json').exists(),
        'searches_exists':  (ap_dir / 'searches.yaml').exists(),
        'ap_exe_ok':        len(AP_CMD) > 0,
    })

# ── Dashboard & Jobs Data APIs ──────────────────────────────────────────────────
@app.route('/api/dashboard')
def api_dashboard():
    if not get_connection:
        return jsonify({'ok': False, 'error': 'Database module not loaded'}), 500
    try:
        conn = get_connection()
        stats = get_stats(conn) if get_stats else {}

        # High fit count (fit_score >= 7)
        high_fit_row = conn.execute("SELECT COUNT(*) FROM jobs WHERE fit_score >= 7").fetchone()
        stats['high_fit'] = high_fit_row[0] if high_fit_row else 0

        # In progress count
        in_prog_row = conn.execute("SELECT COUNT(*) FROM jobs WHERE apply_status = 'in_progress'").fetchone()
        stats['in_progress'] = in_prog_row[0] if in_prog_row else 0

        # Average score for scored jobs
        avg_row = conn.execute("SELECT ROUND(AVG(fit_score), 1) FROM jobs WHERE fit_score IS NOT NULL").fetchone()
        stats['avg_score'] = avg_row[0] if avg_row and avg_row[0] is not None else 0

        # Detailed breakdown per site
        site_details = []
        for r in conn.execute("""
            SELECT site,
                   COUNT(*) as total,
                   SUM(CASE WHEN fit_score >= 7 THEN 1 ELSE 0 END) as high_fit,
                   SUM(CASE WHEN tailored_resume_path IS NOT NULL THEN 1 ELSE 0 END) as tailored,
                   SUM(CASE WHEN applied_at IS NOT NULL THEN 1 ELSE 0 END) as applied,
                   ROUND(AVG(fit_score), 1) as avg_score
            FROM jobs
            GROUP BY site
            ORDER BY total DESC
        """).fetchall():
            site_details.append({
                'site': r['site'] or 'unknown',
                'total': r['total'],
                'high_fit': r['high_fit'] or 0,
                'tailored': r['tailored'] or 0,
                'applied': r['applied'] or 0,
                'avg_score': r['avg_score'] or 0,
            })
        stats['site_details'] = site_details

        return jsonify({'ok': True, 'stats': stats})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/jobs')
def api_jobs():
    if not get_connection:
        return jsonify({'ok': False, 'error': 'Database module not loaded'}), 500
    try:
        conn = get_connection()
        q = request.args.get('search', '').strip().lower()
        site = request.args.get('site', '').strip()
        min_score = request.args.get('min_score', type=int)
        stage = request.args.get('stage', 'all').strip()
        limit = min(int(request.args.get('limit', 100)), 200)
        offset = max(int(request.args.get('offset', 0)), 0)
        sort_by = request.args.get('sort', 'score_desc')

        clauses = []
        params = []

        if q:
            clauses.append("(LOWER(title) LIKE ? OR LOWER(COALESCE(company, '')) LIKE ? OR LOWER(description) LIKE ? OR LOWER(location) LIKE ?)")
            wild = f"%{q}%"
            params.extend([wild, wild, wild, wild])

        if site and site != 'all':
            clauses.append("LOWER(site) = LOWER(?)")
            params.append(site)

        if min_score is not None:
            clauses.append("fit_score >= ?")
            params.append(min_score)

        if stage == 'tailored':
            clauses.append("tailored_resume_path IS NOT NULL")
        elif stage == 'cover':
            clauses.append("cover_letter_path IS NOT NULL")
        elif stage == 'applied':
            clauses.append("applied_at IS NOT NULL")
        elif stage == 'in_progress':
            clauses.append("apply_status = 'in_progress'")
        elif stage == 'scored':
            clauses.append("fit_score IS NOT NULL")
        elif stage == 'unscored':
            clauses.append("fit_score IS NULL")
        elif stage == 'enriched':
            clauses.append("full_description IS NOT NULL")

        where_clause = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        order_clause = "ORDER BY COALESCE(fit_score, -1) DESC, discovered_at DESC"
        if sort_by == 'newest':
            order_clause = "ORDER BY discovered_at DESC"
        elif sort_by == 'title':
            order_clause = "ORDER BY title ASC"
        elif sort_by == 'score_asc':
            order_clause = "ORDER BY fit_score ASC"

        count_sql = f"SELECT COUNT(*) FROM jobs {where_clause}"
        total_count = conn.execute(count_sql, params).fetchone()[0]

        data_sql = f"""
            SELECT url, title, company, salary, location, site, strategy, discovered_at,
                   detail_scraped_at, fit_score, score_reasoning, scored_at,
                   tailored_resume_path, tailored_at, cover_letter_path, cover_letter_at,
                   applied_at, apply_status, apply_attempts, last_attempted_at,
                   CASE WHEN full_description IS NOT NULL THEN 1 ELSE 0 END as has_desc
            FROM jobs
            {where_clause}
            {order_clause}
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(data_sql, params + [limit, offset]).fetchall()
        jobs_list = [dict(r) for r in rows]

        return jsonify({
            'ok': True,
            'total': total_count,
            'limit': limit,
            'offset': offset,
            'jobs': jobs_list,
        })
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/job-detail')
def api_job_detail():
    if not get_connection:
        return jsonify({'ok': False, 'error': 'Database module not loaded'}), 500
    try:
        url = request.args.get('url')
        if not url:
            return jsonify({'ok': False, 'error': 'Missing url parameter'}), 400

        conn = get_connection()
        row = conn.execute("SELECT * FROM jobs WHERE url = ?", (url,)).fetchone()
        if not row:
            return jsonify({'ok': False, 'error': 'Job not found'}), 404

        data = dict(row)

        # Load tailored resume content if file exists
        if data.get('tailored_resume_path'):
            p = Path(data['tailored_resume_path'])
            if p.exists() and p.is_file():
                try:
                    data['tailored_resume_text'] = p.read_text(encoding='utf-8', errors='replace')
                except Exception:
                    data['tailored_resume_text'] = None

        # Load cover letter content if file exists
        if data.get('cover_letter_path'):
            p = Path(data['cover_letter_path'])
            if p.exists() and p.is_file():
                try:
                    data['cover_letter_text'] = p.read_text(encoding='utf-8', errors='replace')
                except Exception:
                    data['cover_letter_text'] = None

        return jsonify({'ok': True, 'job': data})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/logs')
def api_logs():
    try:
        if not LOG_DIR or not LOG_DIR.exists():
            return jsonify({'ok': True, 'logs': []})

        log_files = []
        for f in sorted(LOG_DIR.glob('*'), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.is_file():
                st = f.stat()
                log_files.append({
                    'name': f.name,
                    'size': st.st_size,
                    'modified': datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
                })
        return jsonify({'ok': True, 'logs': log_files[:50]})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@app.route('/api/log-file')
def api_log_file():
    try:
        name = request.args.get('name', '').strip()
        clean_name = Path(name).name
        if not LOG_DIR:
            return jsonify({'ok': False, 'error': 'Logs directory not configured'}), 500
        target = LOG_DIR / clean_name
        if not target.exists() or not target.is_file():
            return jsonify({'ok': False, 'error': 'Log file not found'}), 404
        content = target.read_text(encoding='utf-8', errors='replace')
        return jsonify({'ok': True, 'name': clean_name, 'content': content})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


if __name__ == '__main__':
    port = 5000
    threading.Timer(1.5, lambda: webbrowser.open(f'http://localhost:{port}')).start()
    print(f'\n  ApplyPilot UI  →  http://localhost:{port}')
    print('  Press Ctrl+C to stop.\n')
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
