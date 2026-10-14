"""Conversations: ordered question turns that point to stored job reports.

Reports and review records keep their own files; a conversation only records
which jobs belong together, the user's wording and how each follow-up was read.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4

ID = re.compile(r'[0-9a-f]{32}')
MAX_TURNS = 50


class ConversationStore:
    def __init__(self, directory=None):
        self.directory = Path(directory).resolve() if directory is not None else None
        self.memory = {}
        self.lock = RLock()
        if self.directory is not None: self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, identity):
        if not isinstance(identity, str) or not ID.fullmatch(identity): raise ValueError('Invalid conversation ID')
        return self.directory/(identity+'.json') if self.directory is not None else None

    def create(self):
        identity = uuid4().hex
        value = {'schema_version': 1, 'id': identity, 'created_at': datetime.now(timezone.utc).isoformat(), 'turns': []}
        with self.lock: self._write(value)
        return identity

    def get(self, identity):
        path = self._path(identity)
        with self.lock:
            if path is None:
                if identity not in self.memory: raise KeyError(identity)
                return json.loads(json.dumps(self.memory[identity]))
            if not path.is_file(): raise KeyError(identity)
            value = json.loads(path.read_text(encoding='utf-8'))
        if value.get('schema_version') != 1 or value.get('id') != identity or not isinstance(value.get('turns'), list):
            raise ValueError('Invalid conversation record')
        return value

    def append(self, identity, turn):
        with self.lock:
            value = self.get(identity)
            if len(value['turns']) >= MAX_TURNS: raise ValueError(f'한 대화는 {MAX_TURNS}턴까지 저장됩니다. 새 대화를 시작하세요.')
            value['turns'].append({'turn': len(value['turns'])+1, **turn})
            self._write(value)
            return value

    def update_turn(self, identity, job_id, fields):
        with self.lock:
            value = self.get(identity)
            for turn in value['turns']:
                if turn.get('job_id') == job_id: turn.update(fields)
            self._write(value)

    def _write(self, value):
        path = self._path(value['id'])
        if path is None:
            self.memory[value['id']] = json.loads(json.dumps(value)); return
        temp = path.with_suffix('.'+uuid4().hex+'.tmp')
        try:
            with temp.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False, allow_nan=False); stream.flush(); os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
