"""Start the RA service's HTTP layer (the FastAPI app) inside a test.

`Running(jobs)` serves `jobs` on a free 127.0.0.1 port with the shape tests use (`server_port`, `shutdown()`,
`server_close()`). `serve()` is the same as a context manager yielding the port.
"""
import socket
import time
from contextlib import contextmanager
from threading import Thread


class Running:
    """A running server over `jobs`; `extend(app)` may add routes to the FastAPI app before it starts (fixtures only)."""
    def __init__(self, jobs, extend=None):
        import uvicorn
        from rag_service.app import create_app
        self.stopped = False
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
        self._uvicorn.should_exit = True

    def shutdown(self):
        if self.stopped:
            return
        self.stopped = True
        self._uvicorn.should_exit = True
        self.thread.join(10)
        self._socket.close()  # after the server loop ended; uvicorn closes it too, a second close is harmless

    def server_close(self):
        pass  # done by shutdown()


@contextmanager
def serve(jobs):
    """Port of a running server over `jobs`; stopped on exit. The caller closes `jobs`."""
    server = Running(jobs)
    try:
        yield server.server_port
    finally:
        server.shutdown()
