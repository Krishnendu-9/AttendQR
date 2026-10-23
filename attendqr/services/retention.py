"""Clearing scan IP addresses after SCAN_IP_RETENTION_DAYS.

The attendance entry itself is kept; only its scan_ip is set to NULL. The app
does this itself, at most once an hour on an incoming request, so the limit
holds without a separate scheduled job. `flask purge-scan-ips` runs it on demand.
"""
import time
from datetime import timedelta

from flask import current_app
from sqlalchemy import or_, update
from sqlalchemy.exc import SQLAlchemyError

from ..extensions import db
from ..models import Attendance
from ..utils import ist_now

PURGE_INTERVAL_SECONDS = 3600


def scan_ip_recording_enabled():
    return current_app.config['SCAN_IP_RETENTION_DAYS'] > 0


def purge_expired_scan_ips():
    """Clear scan IPs older than the retention period (all of them when it is 0). Returns the row count."""
    days = current_app.config['SCAN_IP_RETENTION_DAYS']
    stmt = update(Attendance).where(Attendance.scan_ip.is_not(None))
    if days > 0:
        cutoff = ist_now() - timedelta(days=days)
        stmt = stmt.where(or_(Attendance.timestamp.is_(None), Attendance.timestamp < cutoff))
    # Its own connection, so it never commits whatever the request's session holds.
    with db.engine.begin() as connection:
        return connection.execute(stmt.values(scan_ip=None)).rowcount


def register_scan_ip_purge(app):
    # Parsed strictly: a value like "off" must not quietly fall back to keeping addresses.
    raw = app.config['SCAN_IP_RETENTION_DAYS']
    try:
        days = int(str(raw).strip())
    except ValueError:
        days = -1
    if days < 0:
        raise ValueError(f"SCAN_IP_RETENTION_DAYS must be a whole number of days, 0 or more (got {raw!r}).")
    app.config['SCAN_IP_RETENTION_DAYS'] = days

    state = {'next_run': 0.0}

    @app.before_request
    def purge_scan_ips_periodically():
        now = time.monotonic()
        if now < state['next_run']:
            return
        state['next_run'] = now + PURGE_INTERVAL_SECONDS
        try:
            cleared = purge_expired_scan_ips()
        except SQLAlchemyError:
            app.logger.exception("Could not clear expired scan IP addresses; will retry in an hour")
            return
        if cleared:
            app.logger.info("Cleared %d scan IP addresses past the %d-day retention period", cleared, days)
