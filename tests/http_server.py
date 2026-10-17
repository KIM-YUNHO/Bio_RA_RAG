"""Start the RA service's HTTP layer inside a test: the standard-library server or the FastAPI app.

`serve(jobs, kind)` returns the port while the server runs. Tests that must hold for both implementations
(tests/test_http_contract.py) take the kind as a parameter; RA_TEST_SERVER=fastapi switches the default.
"""
import os
import socket
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from threading import Thread

KINDS = ('stdlib', 'fastapi')


def default_kind():
    kind = os.environ.get('RA_TEST_SERVER', 'stdlib')
    if kind not in KINDS:
        raise ValueError(f'RA_TEST_SERVER must be one of {KINDS}')
    return kind


def fastapi_available():
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        import rag_service.app  # noqa: F401
    except ImportError:
        return False
    return True


@contextmanager
def serve(jobs, kind=None):
    """Port of a running server over `jobs` (127.0.0.1, free port); stopped on exit. The caller closes `jobs`."""
    kind = kind or default_kind()
    if kind == 'stdlib':
        from rag_service.__main__ import handler
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(jobs))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
        return
    import uvicorn
    from rag_service.app import create_app
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(jobs, port), log_level='warning', access_log=False, lifespan='off'))
    thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError('the FastAPI server did not start')
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
