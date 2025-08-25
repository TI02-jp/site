import os
import re
import csv
from io import StringIO
from datetime import datetime
from flask import render_template, request, Response
from . import logs_bp
from app.controllers.routes import admin_required

LOG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'logs',
    'app.log'
)


def _read_logs(keyword: str = '', level: str = ''):
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        with open(LOG_FILE, 'r', encoding='latin-1', errors='ignore') as f:
            lines = f.readlines()

    entries = []
    pattern = re.compile(r'^(?P<dt>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\s+(?P<level>\w+)\s+\[(?P<module>[^\]]+)\]\s+(?P<msg>.*)$')
    for line in lines:
        match = pattern.match(line.strip())
        if not match:
            continue
        dt = datetime.strptime(match.group('dt'), '%Y-%m-%d %H:%M:%S')
        lvl = match.group('level')
        module = match.group('module')
        msg = match.group('msg')
        if level and lvl != level:
            continue
        if keyword and keyword.lower() not in line.lower():
            continue
        entries.append({
            'time': dt.strftime('%d/%m/%Y %H:%M:%S'),
            'level': lvl,
            'module': module,
            'message': msg,
        })
    return entries[-200:]


@logs_bp.route('/logs')
@admin_required
def view_logs():
    keyword = request.args.get('q', '')
    level = request.args.get('level', '')
    logs = _read_logs(keyword, level)
    return render_template('admin/logs.html', logs=logs)


@logs_bp.route('/logs/export')
@admin_required
def export_logs():
    keyword = request.args.get('q', '')
    level = request.args.get('level', '')
    fmt = request.args.get('format', 'txt')
    logs = _read_logs(keyword, level)
    si = StringIO()
    if fmt == 'csv':
        writer = csv.writer(si)
        writer.writerow(['time', 'level', 'module', 'message'])
        for e in logs:
            writer.writerow([e['time'], e['level'], e['module'], e['message']])
        mimetype = 'text/csv'
        filename = 'logs.csv'
    else:
        for e in logs:
            si.write(f"{e['time']} {e['level']} [{e['module']}] {e['message']}\n")
        mimetype = 'text/plain'
        filename = 'logs.txt'
    return Response(
        si.getvalue(),
        mimetype=mimetype,
        headers={'Content-Disposition': f'attachment; filename={filename}'},
    )
