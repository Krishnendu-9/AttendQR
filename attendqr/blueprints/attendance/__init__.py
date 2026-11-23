"""QR attendance: the teacher's session pages (sessions.py) and the student's scan (scan.py).

Both register on the one 'attendance' blueprint, so endpoint names stay attendance.<name>.
"""
from flask import Blueprint

bp = Blueprint('attendance', __name__)

from . import scan, sessions  # noqa: E402, F401 - importing registers their routes on bp
