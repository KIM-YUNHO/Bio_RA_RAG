"""Separate, append-only review records bound to immutable source reports."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
from uuid import uuid4


class RevisionConflict(ValueError):
    pass


class ReviewStore:
    def __init__(self, directory):
        self.directory = Path(directory).resolve() if directory is not None else None
        self.lock = RLock()
        if self.directory is not None: self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, identity):
        if not isinstance(identity,str) or not re.fullmatch(r'[0-9a-f]{32}',identity):
            raise ValueError('Invalid question ID')
        if self.directory is None: raise ValueError('검토 기록 저장 경로가 구성되지 않았습니다.')
        return self.directory/(identity+'.json')

    @staticmethod
    def report_hash(job):
        return hashlib.sha256(json.dumps(job['report'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()

    def get(self, job):
        if job.get('state') != 'completed': raise ValueError('완료된 답변에만 검토 의견을 저장할 수 있습니다.')
        with self.lock:
            path=self._path(job['id'])
            if not path.exists():
                return {'schema_version':1,'job_id':job['id'],'question':job['question'],
                    'report_sha256':self.report_hash(job),'storage':'server','revisions':[]}
            value=json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(value,dict) or value.get('schema_version')!=1 or value.get('job_id')!=job['id']
                    or value.get('question')!=job['question'] or value.get('report_sha256')!=self.report_hash(job)
                    or not isinstance(value.get('revisions'),list)
                    or any(not isinstance(r,dict) or r.get('revision')!=n or r.get('status') not in ('unreviewed','checked','needs_work')
                        or not isinstance(r.get('note'),str) or not isinstance(r.get('meeting_questions'),list)
                        for n,r in enumerate(value['revisions'],1))):
                raise ValueError('검토 이력이 원래 답변과 일치하지 않습니다. 기존 파일은 보존했습니다.')
            return value

    def append(self, job, payload):
        if not isinstance(payload,dict) or set(payload)!={'expected_revision','status','note','meeting_questions'}:
            raise ValueError('Invalid review request')
        if (type(payload['expected_revision']) is not int or payload['expected_revision']<0
                or payload['status'] not in ('unreviewed','checked','needs_work')
                or not isinstance(payload['note'],str) or len(payload['note'])>4000):
            raise ValueError('검토 상태와 의견을 확인하세요. 의견은 4,000자까지 저장됩니다.')
        questions=payload['meeting_questions']
        allowed={q['issue_id'] for q in job['report'].get('review_memo',{}).get('meeting_questions',[])}
        if (not isinstance(questions,list) or len(questions)>20 or any(not isinstance(q,dict)
                or set(q)!={'issue_id','question_ko'} or q['issue_id'] not in allowed
                or not isinstance(q['question_ko'],str) or not 1<=len(q['question_ko'])<=3000 for q in questions)
                or len({q['issue_id'] for q in questions})!=len(questions)):
            raise ValueError('미팅 질문의 쟁점과 문안을 확인하세요.')
        with self.lock:
            history=self.get(job)
            if len(history['revisions'])!=payload['expected_revision']:
                raise RevisionConflict('다른 창에서 검토 기록이 바뀌었습니다. 입력을 복사한 뒤 기록을 다시 열어주세요.')
            latest=history['revisions'][-1] if history['revisions'] else None
            if latest and all(latest[k]==payload[k] for k in ('status','note','meeting_questions')):
                return history
            revision={'revision':len(history['revisions'])+1,'saved_at':datetime.now(timezone.utc).isoformat(),
                'status':payload['status'],'note':payload['note'],'meeting_questions':questions,'review_type':'user_review'}
            history={**history,'revisions':[*history['revisions'],revision]}
            path=self._path(job['id']);temp=path.with_suffix('.'+uuid4().hex+'.tmp')
            try:
                with temp.open('x',encoding='utf-8') as stream:
                    json.dump(history,stream,ensure_ascii=False,allow_nan=False);stream.flush();os.fsync(stream.fileno())
                os.replace(temp,path)
            finally:
                temp.unlink(missing_ok=True)
            return history
