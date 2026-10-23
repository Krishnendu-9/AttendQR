"""Run AttendQR.

Normal use serves the app on the LAN with waitress, a production WSGI server.
Set FLASK_DEBUG=1 for development; the debugger is then bound to this machine
only, because Werkzeug's debugger console can run arbitrary code.
"""
import os

from attendqr import create_app
from attendqr.services.network import describe_scan_networks, parse_scan_networks

LOOPBACK_HOSTS = {'127.0.0.1', 'localhost', '::1'}


def main():
    app = create_app()
    port = int(os.environ.get('FLASK_RUN_PORT', '5000'))
    debug = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')
    print(f"QR scans accepted from: {describe_scan_networks(parse_scan_networks(app.config['SCAN_ALLOWED_NETWORKS']))}")

    if debug:
        host = os.environ.get('FLASK_RUN_HOST', '127.0.0.1')
        if host not in LOOPBACK_HOSTS:
            print(f"Refusing to expose the debugger on {host}; using 127.0.0.1. Run without FLASK_DEBUG to serve the LAN.")
            host = '127.0.0.1'
        app.run(host=host, port=port, debug=True)
        return

    from waitress import serve

    # Phones on the LAN must reach the server, so listen on every interface by default.
    host = os.environ.get('FLASK_RUN_HOST', '0.0.0.0')  # noqa: S104
    print(f"AttendQR is serving on http://{host}:{port} (press Ctrl+C to stop)")
    serve(app, host=host, port=port, threads=int(os.environ.get('WAITRESS_THREADS', '8')))


if __name__ == '__main__':
    main()
