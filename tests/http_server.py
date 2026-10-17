"""Start the RA service's HTTP layer inside a test: the standard-library server or the FastAPI app.

`Running(jobs, kind)` serves `jobs` on a free 127.0.0.1 port and is shaped like ThreadingHTTPServer (`server_port`,
`shutdown()`, `server_close()`) so tests written for that server only change the line that creates it. `serve()` is the
same as a context manager yielding the port. Tests that must hold for both implementations take the kind as a
parameter (tests/test_http_contract.py); RA_TEST_SERVER=fastapi switches the default for all the others.
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


class Running:
    """A running server over `jobs`; `extend(app)` may add routes to the FastAPI app before it starts (fixtures only)."""
    def __init__(self, jobs, kind=None, extend=None):
        self.kind = kind or default_kind()
        self.stopped = False
        if self.kind == 'stdlib':
            from rag_service.__main__ import handler
            self._httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler(jobs))
            self.server_port = self._httpd.server_port
            self.thread = Thread(target=self._httpd.serve_forever, daemon=True)
            self.thread.start()
            return
        import uvicorn
        from rag_service.app import create_app
        self._socket = socket.socket()
        self._socket.bind(('127.0.0.1', 0))
        self.server_port = self._socket.getsockname()[1]
        app = create_app(jobs, self.server_port)
        if extend:
            extend(app, self)
        self._uvicorn = uvicorn.Server(uvicorn.Config(app, log_level='warning', access_log=False, lifespan='off'))
        self.thread = Thread(target=self._uvicorn.run, kwargs={'sockets': [self._socket]}, daemon=True)
        self.thread.start()
        deadline = time.time() + 10
        while not self._uvicorn.started and time.time() < deadline:
            time.sleep(0.01)
        if not self._uvicorn.started:
            raise RuntimeError('the FastAPI server did not start')

    def request_stop(self):
        """Ask the server to stop without waiting (safe from inside a request)."""
        if self.kind == 'fastapi':
            self._uvicorn.should_exit = True
        else:
            Thread(target=self._httpd.shutdown, daemon=True).start()

    def shutdown(self):
        if self.stopped:
            return
        self.stopped = True
        if self.kind == 'stdlib':
            self._httpd.shutdown()
            self._httpd.server_close()
        else:
            self._uvicorn.should_exit = True
        self.thread.join(10)
        if self.kind == 'fastapi':
            self._socket.close()  # after the server loop ended; uvicorn closes it too, a second close is harmless

    def server_close(self):
        pass  # done by shutdown()


@contextmanager
def serve(jobs, kind=None):
    """Port of a running server over `jobs`; stopped on exit. The caller closes `jobs`."""
    server = Running(jobs, kind)
    try:
        yield server.server_port
    finally:
        server.shutdown()
