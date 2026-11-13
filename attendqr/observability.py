"""Request IDs, structured logs, an access log, and Prometheus metrics.

- Every request gets an id (a valid incoming X-Request-ID is kept, so a proxy's id
  carries through), returned in the X-Request-ID header and added to every log line.
- LOG_FORMAT=json writes one JSON object per log line, for log collectors.
- The access log records the route pattern (/scan/<token>), never the actual path,
  query string or client address: QR tokens and IP addresses stay out of the logs.
- /metrics serves counters in the Prometheus text format when METRICS_TOKEN is set,
  and only to requests carrying `Authorization: Bearer <METRICS_TOKEN>`.

The counters live in this process. Under several worker processes (gunicorn -w 4)
each worker reports its own; scrape each, or use one process (waitress, the default).
"""
import json
import logging
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict

from flask import Response, abort, current_app, g, has_request_context, request

REQUEST_ID_HEADER = 'X-Request-ID'
_VALID_REQUEST_ID = re.compile(r'^[A-Za-z0-9._-]{8,64}$')
TEXT_FORMAT = '[%(asctime)s] %(levelname)s %(request_id)s %(name)s: %(message)s'

# Seconds; upper bounds for the request duration histogram.
DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

HELP = {
    'attendqr_http_requests_total': ('counter', 'HTTP requests by route pattern, method and status.'),
    'attendqr_http_request_duration_seconds': ('histogram', 'Time to handle a request, by route pattern.'),
    'attendqr_scans_total': ('counter', 'QR scan and attendance confirmation outcomes.'),
    'attendqr_logins_total': ('counter', 'Sign-in attempts by outcome.'),
    'attendqr_live_sessions': ('gauge', 'QR sessions currently open for scanning.'),
}


class Metrics:
    """Thread-safe counters and histograms for one process."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters = defaultdict(float)
        self._histograms = {}

    def inc(self, name, amount=1, **labels):
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] += amount

    def observe(self, name, value, **labels):
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            buckets, total = self._histograms.get(key, ([0] * len(DURATION_BUCKETS), [0.0, 0]))
            for index, bound in enumerate(DURATION_BUCKETS):
                if value <= bound:
                    buckets[index] += 1
            total[0] += value
            total[1] += 1
            self._histograms[key] = (buckets, total)

    def value(self, name, **labels):
        with self._lock:
            return self._counters.get((name, tuple(sorted(labels.items()))), 0)

    def reset(self):
        with self._lock:
            self._counters.clear()
            self._histograms.clear()

    def render(self, gauges=()):
        """The Prometheus text exposition format (version 0.0.4)."""
        lines, described = [], set()

        def describe(name):
            if name in described:
                return
            described.add(name)
            kind, text = HELP.get(name, ('untyped', name))
            lines.append(f'# HELP {name} {text}')
            lines.append(f'# TYPE {name} {kind}')

        with self._lock:
            counters = sorted(self._counters.items())
            histograms = sorted((key, (list(b), list(t))) for key, (b, t) in self._histograms.items())
        for (name, labels), amount in counters:
            describe(name)
            lines.append(f'{name}{_labels(labels)} {_number(amount)}')
        for (name, labels), (buckets, (total, count)) in histograms:
            describe(name)
            for bound, cumulative in zip(DURATION_BUCKETS, buckets, strict=True):
                lines.append(f'{name}_bucket{_labels((*labels, ("le", str(bound))))} {cumulative}')
            lines.append(f'{name}_bucket{_labels((*labels, ("le", "+Inf")))} {count}')
            lines.append(f'{name}_sum{_labels(labels)} {total:.6f}')
            lines.append(f'{name}_count{_labels(labels)} {count}')
        for name, value in gauges:
            describe(name)
            lines.append(f'{name} {_number(value)}')
        return '\n'.join(lines) + '\n'


def _labels(pairs):
    if not pairs:
        return ''
    escaped = (f'{key}="{str(value).replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
               for key, value in pairs)
    return '{' + ','.join(escaped) + '}'


def _number(value):
    return str(int(value)) if float(value).is_integer() else repr(float(value))


metrics = Metrics()


def count_scan(outcome):
    metrics.inc('attendqr_scans_total', outcome=outcome)


def count_login(outcome):
    metrics.inc('attendqr_logins_total', outcome=outcome)


# ── Logging ──────────────────────────────────────────────────

def current_request_id():
    return g.get('request_id', '-') if has_request_context() else '-'


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = current_request_id()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            'time': self.formatTime(record, '%Y-%m-%dT%H:%M:%S%z'),
            'level': record.levelname,
            'logger': record.name,
            'request_id': getattr(record, 'request_id', '-'),
            'message': record.getMessage(),
        }
        for key in ('method', 'route', 'status', 'duration_ms'):
            if hasattr(record, key):
                entry[key] = getattr(record, key)
        if record.exc_info:
            entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def _configure_logging(app):
    formatter = JsonFormatter() if app.config['LOG_FORMAT'] == 'json' else logging.Formatter(TEXT_FORMAT)
    for handler in app.logger.handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RequestIdFilter())
    if not app.debug and not app.testing:
        app.logger.setLevel(logging.INFO)


# ── Hooks ────────────────────────────────────────────────────

def init_app(app):
    _configure_logging(app)

    @app.before_request
    def start_request():
        incoming = request.headers.get(REQUEST_ID_HEADER, '')
        g.request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        g.request_started = time.perf_counter()

    @app.after_request
    def finish_request(response):
        response.headers[REQUEST_ID_HEADER] = g.get('request_id', '')
        started = g.get('request_started')
        if started is None:
            return response
        duration = time.perf_counter() - started
        route = request.url_rule.rule if request.url_rule else 'unmatched'
        if request.endpoint != 'static':
            metrics.inc('attendqr_http_requests_total', route=route, method=request.method,
                        status=str(response.status_code))
            metrics.observe('attendqr_http_request_duration_seconds', duration, route=route)
            if app.config['ACCESS_LOG']:
                app.logger.info('%s %s %s %.1fms', request.method, route, response.status_code, duration * 1000,
                                extra={'method': request.method, 'route': route, 'status': response.status_code,
                                       'duration_ms': round(duration * 1000, 1)})
        return response

    @app.route('/metrics')
    def prometheus_metrics():
        token = current_app.config.get('METRICS_TOKEN') or ''
        if not token:
            abort(404)  # off unless configured
        supplied = request.headers.get('Authorization', '')
        if not secrets.compare_digest(supplied.encode(), f'Bearer {token}'.encode()):
            abort(403)
        return Response(metrics.render(_gauges()), mimetype='text/plain; version=0.0.4')


def _gauges():
    from .extensions import db
    from .models import ClassSession
    from .utils import utc_now

    live = db.session.query(ClassSession.id).filter(
        ClassSession.is_active.is_(True), ClassSession.expires_at >= utc_now()
    ).count()
    return [('attendqr_live_sessions', live)]
