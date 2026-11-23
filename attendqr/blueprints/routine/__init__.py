"""The class routine, on one 'routine' blueprint (endpoint names stay routine.<name>):

- schedules.py: the Calendar page, with bell schedules and holidays
- drafts.py: each class's routine, its draft editor, publishing, printing and importing
- views.py: dated classes to look at: my routine, a class's week
- changes.py: acting on a dated class: starting its session, one-date changes, extra classes
- common.py: who may see or edit what, shared by all of them
"""
from flask import Blueprint

bp = Blueprint('routine', __name__)

from . import changes, drafts, schedules, views  # noqa: E402, F401 - importing registers their routes on bp
