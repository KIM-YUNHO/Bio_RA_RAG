"""Small file helpers shared by preprocessing, pipeline_h and the RA code: JSON/JSONL I/O, a checked cache and
a lock for one running job per folder. No document processing here."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import time

from preprocess.ingest import stable_hash


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def save_rows(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text("".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in rows), encoding="utf-8")
    temp.replace(path)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows readers/virus scanners can briefly hold the destination open.
    # A private, closed temp file avoids colliding writers and preserves the
    # previous complete checkpoint until an atomic replacement succeeds.
    payload=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
            prefix=path.name+'.',suffix='.tmp',delete=False) as stream:
        stream.write(payload)
        temporary=Path(stream.name)
    try:
        for attempt in range(8):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt==7:raise
                time.sleep(min(.05*(2**attempt),.5))
    finally:
        temporary.unlink(missing_ok=True)


def cache_read(path, key):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value["key"] == key and value["payload_sha256"] == stable_hash(value["payload"]):
            return value["payload"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def cache_write(path, key, payload):
    write_json(path, {"key": key, "payload_sha256": stable_hash(payload), "payload": payload})


@contextmanager
def exclusive(out):
    """OS releases this lock on a crash; a leftover file does not block resume."""
    path = out/'workflow.lock'
    with path.open('a+b') as lock:
        if path.stat().st_size == 0:
            lock.write(b'0');lock.flush()
        lock.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:raise ValueError('This workflow is already running') from exc
        else:
            import fcntl
            try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:raise ValueError('This workflow is already running') from exc
        try:yield
        finally:
            lock.seek(0)
            if os.name == 'nt':msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:fcntl.flock(lock, fcntl.LOCK_UN)
