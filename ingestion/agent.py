"""Ingestion request agent (plan P3, section 7): a low-level request chat that runs the ingestion pipeline once a request is complete.

Each user turn: gpt-4o-mini turns the message into TurnParse (one strict-schema call). Everything else is code: names are resolved by
ingestion.registry, licence status comes from the official lists, the plan from product_search.discover, and the request is
validated by IngestionRequest before anything runs. Staging starts after the person confirms the plan once; moving into the project
needs the check table to have been shown and the exact command /approve <PRODUCT_ID>.

Usage: uv run python -m ingestion.agent                       interactive
       uv run python -m ingestion.agent --session <id> --say "Imuldosa 넣어줘"
Commands: /status  /approve <PRODUCT_ID>  /reset  /help  /quit
"""
import argparse
import getpass
import hashlib
import json
import re
import subprocess
import sys
import uuid

from psycopg.types.json import Jsonb

from ingestion import db, licence_status, plan as planning, registry
from ingestion.contracts import ROOT, VERSION, Candidate, TurnParse
from ingestion.sources import norm

MODEL = "gpt-4o-mini"
UNSUPPORTED = {"minItems", "maxItems", "minLength", "maxLength", "pattern", "format", "default", "title"}
JURISDICTION_KO = {"EU": "EU(EMA)", "US": "미국(FDA)", "CA": "캐나다(HC)", "JP": "일본(PMDA)", "KR": "한국(식약처)"}
HELP = ("성분·제품명·개발코드로 물어보세요. 예: '우스테키누맙 제품들 허가 현황', 'Imuldosa 적재해줘', 'SB17 수집됐어?'\n"
        "명령: /status (적재 진행 상황과 확인표), /approve <PRODUCT_ID> (확인표를 본 뒤 자료 저장소에 등록), /reset, /quit")

SYSTEM = """You turn one user message to a biosimilar regulatory-document ingestion assistant into JSON. You never answer the user.

mentions: every drug, ingredient, product, development code or company name the user wrote, copied exactly as written in the message
(same spelling, same script). Never add a name the user did not write, never correct spelling, and give no mention for pronouns
("그거", "이 제품", "it"). guessed_type: inn (ingredient such as ustekinumab), brand, dev_code (letters+digits such as SB17, CT-P43),
proper_name (US name with a suffix such as ustekinumab-auub), company, korean (any name written in Hangul), unknown.
english_guess: if the mention is in Hangul or another non-Latin script, the usual Latin-script name (스텔라라 -> Stelara,
우스테키누맙 -> ustekinumab); otherwise "".
jurisdictions: regions the user names in the message (never infer one from a company's or product's country): 유럽/EU/EMA -> EU, 미국/FDA -> US, 캐나다/Health Canada -> CA,
일본/PMDA -> JP, 한국/식약처/국내 -> KR. Empty when none is named.
intent:
- licence_status: asks about approval or licence status (허가 현황, 승인됐어?, 상호교환, 판매 상태)
- ingest: asks to add/collect/load documents of a product or ingredient into the corpus (적재, 수집, 넣어줘, 추가해줘)
- refresh: asks to update documents of a product already collected (다시 수집, 갱신, 업데이트)
- add_guideline: asks to add a regulatory guideline or guidance document
- coverage: asks what is already collected or searchable (수집됐어?, 뭐가 있어?, 검색돼?)
- select: picks from the numbered list shown in CONTEXT; put the chosen keys (from CONTEXT, never invented) in selected_keys
- confirm: agrees to go ahead with the plan shown in CONTEXT (진행, 네, 좋아, 그렇게 해)
- cancel: stops (취소, 그만)
- other: anything else
selected_keys: only for select: the key text before the colon in CONTEXT (for "2번" give the key of item 2, e.g. "CODE:DMB3115",
not "2"); otherwise []. Names that appear only in CONTEXT are not mentions."""


def strict_schema(model):
    """Pydantic schema without the keywords OpenAI strict mode does not accept; Pydantic still checks them on the reply."""
    def clean(node):
        if isinstance(node, dict):
            return {k: clean(v) for k, v in node.items() if k not in UNSUPPORTED}
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node
    return clean(model.model_json_schema())


def openai_parse(conn, session_id, text, context):
    from rag.llm import chat
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": f"CONTEXT:\n{context}\n\nMESSAGE:\n{text}"}]
    digest = hashlib.sha256(json.dumps(messages, ensure_ascii=False).encode("utf-8")).hexdigest()
    try:
        reply = chat(messages, json_schema={"name": "turn_parse", "schema": strict_schema(TurnParse)}, provider="openai", model=MODEL)
        parsed = TurnParse.model_validate_json(reply["text"])
    except Exception as exc:
        conn.execute("INSERT INTO ingest.model_calls (purpose, session_id, model_requested, request_sha256, ok, error) VALUES ('agent_turn', %s, %s, %s, false, %s)",
                     (session_id, MODEL, digest, f"{type(exc).__name__}: {exc}"[:500]))
        raise
    conn.execute("INSERT INTO ingest.model_calls (purpose, session_id, model_requested, model_reported, prompt_tokens, completion_tokens, request_sha256, ok) "
                 "VALUES ('agent_turn', %s, %s, %s, %s, %s, %s, true)",
                 (session_id, MODEL, reply["model_reported"], reply["prompt_tokens"], reply["completion_tokens"], digest))
    return parsed


REGION_WORDS = {"EU": ["유럽", "eu", "ema", "europe", "chmp"], "US": ["미국", "fda", "us", "usa", "purple"],
                "CA": ["캐나다", "canada", "hc", "health canada"], "JP": ["일본", "pmda", "japan"], "KR": ["한국", "국내", "식약처", "mfds", "korea"]}


def grounded_jurisdictions(text, jurisdictions):
    """Regions the message itself names; a region the model inferred (e.g. KR from a Korean company) is dropped."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    return [j for j in jurisdictions if any((w in words) if w.isascii() and " " not in w else (w in text.lower()) for w in REGION_WORDS[j])]


def map_selection(selected, shown):
    """Selected keys as listed. The model sometimes returns the list number ("2") or adds a prefix ("CODE:CNTO1275" for CNTO1275);
    a value maps to a shown key only when exactly one shown key fits."""
    out = []
    for value in selected:
        value = value.strip().rstrip(".")
        if value in shown:
            out.append(value)
        elif value.isdigit() and 0 < int(value) <= len(shown):
            out.append(shown[int(value) - 1])
        else:
            fits = [k for k in shown if norm(value).endswith(norm(k)) or norm(k).endswith(norm(value))]
            if len(fits) == 1:
                out.append(fits[0])
    return list(dict.fromkeys(out))


def label(c):
    names = " / ".join(f"{j}:{n}" for j, n in c["names"].items()) or "허가명 없음"
    kind = {"ingredient": "성분", "reference_product": "대조약", "biosimilar_product": "바이오시밀러", "other_product": "기타"}[c["level"]]
    flags = ("수집됨" if c["collected"] else "미수집") + (", 검색 가능" if c["searchable"] else "")
    eu = (c.get("statuses") or {}).get("EU")
    if eu and eu != "Authorised":
        flags += f", EU 상태: {eu}"
    return f"[{kind}] {c['product_id'] or c['key']} — {names} ({c['ingredient']}; {flags})"


def plan_digest(plan):
    """Bind an explicit UI start button to the plan the person actually saw."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()


class Agent:
    def __init__(self, conn, session_id=None, parse=None):
        self.conn = conn
        self.parse = parse or (lambda text, context: openai_parse(conn, self.session_id, text, context))
        if session_id:
            row = conn.execute("SELECT state FROM ingest.agent_sessions WHERE session_id = %s", (session_id,)).fetchone()
            if not row:
                raise SystemExit(f"no session {session_id}")
            self.session_id, self.state = session_id, row[0]
        else:
            self.session_id, self.state = str(uuid.uuid4()), self.fresh()
            conn.execute("INSERT INTO ingest.agent_sessions (session_id, state) VALUES (%s, %s)", (self.session_id, Jsonb(self.state)))

    @staticmethod
    def fresh():
        return {"stage": "idle", "candidates": [], "pending_intent": None, "target": None, "plan": None, "request_id": None,
                "shown_reports": {}, "user_texts": []}

    # ---- one turn ----------------------------------------------------------------------------------------

    def handle(self, text):
        text = text.strip()
        actions, parse = [], None
        if text.startswith("/"):
            reply = self.command(text, actions)
        else:
            self.state["user_texts"] = (self.state["user_texts"] + [text])[-10:]
            try:
                parse = self.parse(text, self.context())
            except Exception as exc:
                reply = f"요청을 해석하지 못했습니다({type(exc).__name__}). 다시 말씀해 주세요."
            else:
                parse = parse.model_copy(update={"jurisdictions": grounded_jurisdictions(text, parse.jurisdictions)})
                reply = self.dispatch(text, parse, actions)
        return self.record_turn(text, parse, actions, reply)

    def control(self, payload):
        """Explicit screen controls use the same dispatcher without a model call."""
        action = payload.get('action')
        if action == 'select' and set(payload) == {'action', 'candidate_key'}:
            key = payload['candidate_key']
            if self.state['stage'] != 'choosing' or not any(c['key'] == key for c in self.state['candidates']):
                raise ValueError('현재 목록에 있는 제품을 다시 선택하세요.')
            text = '목록에서 선택: ' + key
            parse = TurnParse(intent='select', mentions=[], jurisdictions=[], selected_keys=[key])
        elif action == 'start' and set(payload) == {'action', 'plan_sha256'}:
            if self.state['stage'] != 'planned' or not self.state['plan'] or payload['plan_sha256'] != plan_digest(self.state['plan']):
                raise ValueError('계획이 바뀌었거나 이미 시작했습니다. 최신 적재 계획을 확인하세요.')
            text = '계획대로 스테이징 수집 시작'
            parse = TurnParse(intent='confirm', mentions=[], jurisdictions=[], selected_keys=[])
        else:
            raise ValueError('지원하지 않는 적재 동작입니다.')
        self.state['user_texts'] = (self.state['user_texts'] + [text])[-10:]
        actions = [{'tool': 'ui_control', 'action': action}]
        reply = self.dispatch(text, parse, actions)
        return self.record_turn(text, parse, actions, reply)

    def record_turn(self, text, parse, actions, reply):
        turn = self.conn.execute("SELECT count(*) FROM ingest.agent_turns WHERE session_id = %s", (self.session_id,)).fetchone()[0] + 1
        self.conn.execute("INSERT INTO ingest.agent_turns (session_id, turn, user_text, parse, actions, reply, stage_after) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                          (self.session_id, turn, text, Jsonb(parse.model_dump()) if parse else None, Jsonb(actions), reply, self.state["stage"]))
        self.conn.execute("UPDATE ingest.agent_sessions SET state = %s, updated_at = now() WHERE session_id = %s", (Jsonb(self.state), self.session_id))
        return reply

    def context(self):
        lines = [f"stage: {self.state['stage']}"]
        if self.state["candidates"]:
            lines.append("numbered list shown to the user (key: description):")
            lines += [f"{i}. {c['key']}: {label(c)}" for i, c in enumerate(self.state["candidates"], 1)]
        if self.state["target"]:
            lines.append(f"current product: {label(self.state['target'])}")
        if self.state["stage"] == "planned":
            lines.append("a staging plan was shown and awaits confirmation")
        return "\n".join(lines)

    def dispatch(self, text, parse, actions):
        intent = parse.intent
        if intent == "cancel":
            self.state.update(stage="idle", candidates=[], pending_intent=None, plan=None)
            return "취소했습니다. 진행 중인 적재 요청이 있으면 /status로 볼 수 있습니다."
        if intent == "confirm":
            if self.state["stage"] in ("review", "staging", "promoting"):
                return "자료 등록 승인은 확인표(/status)를 본 뒤 /approve <PRODUCT_ID> 명령으로만 받습니다."
            if self.state["stage"] != "planned":
                return "지금 확인할 계획이 없습니다. 적재할 제품을 먼저 말씀해 주세요."
            return self.start_staging(actions)
        if intent == "select":
            keys = map_selection(parse.selected_keys, [c["key"] for c in self.state["candidates"]])
            if len(keys) != 1:
                return "목록에서 하나를 골라 주세요(번호나 이름)."
            self.state["target"] = next(c for c in self.state["candidates"] if c["key"] == keys[0])
            self.state["candidates"] = []
            intent = self.state["pending_intent"] or "licence_status"
        elif parse.mentions:
            words = [m for m in parse.mentions if norm(m.text) and norm(m.text) in norm(text)]  # a name the user did not write is dropped
            if not words:
                return "제품이나 성분 이름을 찾지 못했습니다. 이름을 그대로 적어 주세요."
            found, searched = {}, []
            for m in words:
                if m.guessed_type == "company":
                    continue
                for query in [m.text] + ([m.english_guess] if m.english_guess and norm(m.english_guess) != norm(m.text) else []):
                    r = registry.resolve(self.conn, query)
                    actions.append({"tool": "resolve", "query": query, "status": r.status, "candidates": [c.key for c in r.candidates]})
                    searched += r.searched
                    if r.candidates:
                        for c in r.candidates:
                            found.setdefault(c.key, c.model_dump(mode="json"))
                        break
            if not found:
                return (f"공식 목록에서 '{', '.join(m.text for m in words)}'을(를) 찾지 못했습니다(찾아본 곳: {', '.join(dict.fromkeys(searched)) or '이름 사전'}). "
                        "회사명만으로는 아직 찾지 않습니다. 성분명, 제품명 또는 개발코드로 알려 주세요.")
            if len(found) > 1:
                return self.choose(list(found.values()), intent, "여러 후보가 있습니다. 어느 것인가요?")
            self.state["target"] = next(iter(found.values()))
        elif not self.state["target"] and intent in ("licence_status", "ingest", "refresh", "coverage"):
            return "어떤 성분이나 제품인지 알려 주세요."
        if parse.jurisdictions or parse.intent != "select":  # a "2번" reply keeps the regions asked for earlier
            self.state["jurisdictions"] = parse.jurisdictions
        return self.act(intent, actions)

    def choose(self, candidates, intent, question):
        self.state.update(stage="choosing", candidates=candidates, pending_intent=intent)
        return question + "\n" + "\n".join(f"  {i}. {label(c)}" for i, c in enumerate(candidates, 1))

    def act(self, intent, actions):
        target = self.state["target"]
        if intent in ("licence_status", "ingest", "coverage") and target["level"] == "ingredient":
            products = [c.model_dump(mode="json") for c in registry.products_of(self.conn, target["ingredient"])]
            actions.append({"tool": "products_of", "ingredient": target["ingredient"], "count": len(products)})
            return self.choose(products, intent, f"{target['ingredient']} 제품 {len(products)}개입니다(공식 목록 기준). 어느 제품인가요?")
        if intent == "licence_status":
            return self.status(target, actions)
        if intent == "coverage":
            return self.coverage(target, actions)
        if intent == "ingest":
            return self.plan(target, actions)
        if intent == "refresh":
            if not target["product_id"]:
                return f"{label(target)}은(는) 아직 자료 저장소에 등록되지 않았습니다. 새로 적재하려면 '적재해줘'라고 해 주세요."
            return (f"{target['product_id']}은(는) 이미 자료 저장소에 등록된 제품입니다. 기존 자료 갱신은 에이전트가 자동으로 실행하지 않습니다:\n"
                    "  uv run python -m collection.collect  →  -m collection.build_indexes  →  -m collection.verify  →  -m ingestion.load_catalog")
        if intent == "add_guideline":
            return "지침 추가는 아직 에이전트가 실행하지 않습니다. configs/collection/guidelines.json에 항목을 넣고 `uv run python -m collection.collect.py --only guidelines`를 실행해 주세요."
        return HELP

    # ---- tools -------------------------------------------------------------------------------------------

    def status(self, target, actions):
        if not target["names"]:
            return f"{label(target)}: 개발코드만 확인됐고 허가명이 없습니다. 적재 계획을 세우면 EPAR로 허가명과 연결합니다('적재해줘')."
        report = licence_status.check(self.conn, target["key"])
        actions.append({"tool": "licence_status", "key": target["key"], "rows": len(report.rows)})
        only = set(self.state.get("jurisdictions") or [])
        lines = [f"{label(target)} 허가 현황"]
        for r in report.rows:
            if only and r.jurisdiction not in only:
                continue
            keep = {k: v for k, v in r.details.items() if k in ("licence", "interchangeable_approved", "marketing_status", "withdrawal_or_expiry",
                                                                 "first_interchangeable_exclusivity_ends", "din", "status_since")}
            lines.append(f"  - {JURISDICTION_KO[r.jurisdiction]} {r.name}: {r.status}" + (f", 허가 {r.authorised}" if r.authorised else "")
                         + (f" {keep}" if keep else "") + f"  [출처 {r.evidence.source}, 기준 {r.evidence.as_of}]")
        skipped = [JURISDICTION_KO[j] for j in report.not_checked if not only or j in only]
        if skipped:
            lines.append(f"  확인하지 않은 관할: {', '.join(skipped)}")
        if report.product_id:
            lines.append("  수집 당시와 달라진 점: " + ("; ".join(report.changed_since_collection) or "없음"))
        self.state["stage"] = "status_shown"
        return "\n".join(lines)

    def coverage(self, target, actions):
        if not target["product_id"]:
            return f"{label(target)}: 자료 저장소에 수집되지 않았습니다. 적재하려면 '적재해줘'라고 해 주세요."
        rows = self.conn.execute("SELECT d.source, count(*), sum(v.page_count), count(*) FILTER (WHERE d.default_search) FROM corpus.documents d "
                                 "JOIN corpus.document_versions v ON v.document_id = d.document_id AND v.version_id = d.current_version_id "
                                 "WHERE d.product_id = %s GROUP BY 1 ORDER BY 1", (target["product_id"],)).fetchall()
        actions.append({"tool": "coverage", "product_id": target["product_id"]})
        lines = [f"{label(target)} 수집 현황"] + [f"  - {s}: 문서 {n}건, {p}쪽 (기본 검색 대상 {d}건)" for s, n, p, d in rows]
        lines.append("  RAG 검색: " + ("운영 릴리스에 포함" if target["searchable"] else "아직 포함되지 않음(전처리 범위는 따로 정함)"))
        return "\n".join(lines)

    def plan(self, target, actions):
        if target["product_id"]:
            return f"{target['product_id']}은(는) 이미 자료 저장소에 수집돼 있습니다. 현황은 '수집 현황', 갱신은 '다시 수집'이라고 해 주세요."
        try:
            result = planning.plan(Candidate(**target), self.state["user_texts"])
        except planning.PlanError as exc:
            actions.append({"tool": "plan", "error": str(exc)})
            return f"적재 계획을 세우지 못했습니다: {exc}"
        req, entry = result["request"], result["entry"]
        p = req.products[0]
        actions.append({"tool": "plan", "product_id": p.product_id})
        self.state.update(stage="planned", plan={"request": req.model_dump(mode="json"), "entry": entry})
        licences = "; ".join(f"{l.jurisdiction} {l.name} {l.licence_id or ''}{'' if l.collected else ' (같은 제품, 문서는 받지 않음)'}" for l in p.licences)
        return "\n".join([
            f"적재 계획: {p.product_id} ({'대조약' if p.kind == 'reference' else '바이오시밀러'}, 성분 {req.ingredient}, 개발사 {p.company})",
            f"  허가: {licences}",
            f"  수집 관할: {', '.join(JURISDICTION_KO[j] for j in req.jurisdictions)}; 임상시험 {len(p.study_ids)}건 {' '.join(p.study_ids)}",
            f"  {'스테이징에 이전 수집본이 있어 받은 문서는 재사용합니다.' if result['already_staged'] else '스테이징(product_search/data/)에 새로 받습니다.'}"
            " 현재 자료 저장소는 바뀌지 않습니다.",
            "  진행할까요? ('진행'이라고 답하면 스테이징 수집을 시작합니다. 자료 저장소 등록은 확인표를 본 뒤 /approve로 따로 합니다.)"])

    def start_staging(self, actions):
        req = self.state["plan"]["request"]
        request_id = str(uuid.uuid4())
        with self.conn.transaction():
            self.conn.execute('SELECT pg_advisory_xact_lock(20261015, 3)')
            active = self.conn.execute("SELECT request_id::text FROM ingest.requests WHERE status IN ('queued','staging','approved','promoting') ORDER BY created_at LIMIT 1").fetchone()
            if active:
                return f"다른 적재 작업({active[0]})이 실행 중입니다. 완료 후 다시 진행해 주세요."
            self.conn.execute("INSERT INTO ingest.requests (request_id, kind, contract_version, payload) VALUES (%s, %s, %s, %s)",
                              (request_id, req["kind"], VERSION, Jsonb(req)))
            self.conn.execute("INSERT INTO ingest.request_events (request_id, event, payload) VALUES (%s, 'planned', %s)",
                              (request_id, Jsonb({"session_id": self.session_id, "discover_entries": [self.state["plan"]["entry"]],
                                                  "confirmed_by_text": self.state["user_texts"][-1]})))
        try:
            spawn(["stage", request_id], request_id)
        except OSError as exc:
            from ingestion.worker import event, set_status
            event(self.conn, request_id, 'failed', {'step': 'worker_start', 'error': type(exc).__name__})
            set_status(self.conn, request_id, 'failed')
            self.state.update(stage='planned', request_id=request_id)
            return '수집 작업을 시작하지 못했습니다. 작업 로그를 확인한 뒤 다시 시작하세요.'
        actions.append({"tool": "stage_collect", "request_id": request_id})
        self.state.update(stage="staging", request_id=request_id, plan=None)
        return f"스테이징 수집을 시작했습니다(요청 {request_id}). 몇 분 걸릴 수 있습니다. /status로 진행 상황과 확인표를 볼 수 있습니다."

    # ---- commands ------------------------------------------------------------------------------------------

    def command(self, text, actions):
        parts = text.split()
        if parts[0] == "/help":
            return HELP
        if parts[0] == "/reset":
            self.state = self.fresh()
            return "새로 시작합니다."
        if parts[0] == "/status":
            return self.request_status(actions)
        if parts[0] == "/approve":
            return self.approve(parts[1] if len(parts) > 1 else "", actions)
        return HELP

    def request_status(self, actions):
        request_id = self.state["request_id"]
        if not request_id:
            return "이 대화에서 시작한 적재 요청이 없습니다."
        status = self.conn.execute("SELECT status FROM ingest.requests WHERE request_id = %s", (request_id,)).fetchone()[0]
        events = self.conn.execute("SELECT event, payload FROM ingest.request_events WHERE request_id = %s ORDER BY event_id", (request_id,)).fetchall()
        actions.append({"tool": "job_status", "request_id": request_id, "status": status})
        lines = [f"요청 {request_id}: {status}"]
        if status == "awaiting_approval":
            for name, payload in events:
                if name == "staged":
                    self.state["shown_reports"][payload["product_id"]] = payload["report_sha256"]
                    lines += [f"--- 확인표 {payload['product_id']} ---", payload["report"].rstrip()]
            lines.append("허가·문서·시험이 맞으면 /approve <PRODUCT_ID>로 자료 저장소에 등록합니다(원본을 다시 받지 않고 그대로 복사).")
            self.state["stage"] = "review"
        elif status == "failed":
            lines += [f"  실패: {p.get('step')}: {p.get('error')}" for e, p in events if e == "failed"]
        elif status == "done":
            lines += [f"  DB 갱신: {p.get('catalog')}" for e, p in events if e == "database_updated"]
        return "\n".join(lines)

    def approve(self, product_id, actions):
        request_id = self.state["request_id"]
        shown = self.state["shown_reports"].get(product_id)
        if not request_id or not shown:
            return "먼저 /status로 그 제품의 확인표를 보고 승인해 주세요."
        status = self.conn.execute("SELECT status FROM ingest.requests WHERE request_id = %s", (request_id,)).fetchone()[0]
        latest = self.conn.execute("SELECT payload->>'report_sha256' FROM ingest.request_events WHERE request_id = %s AND event = 'staged' "
                                   "AND payload->>'product_id' = %s ORDER BY event_id DESC LIMIT 1", (request_id, product_id)).fetchone()
        if status != "awaiting_approval" or not latest or latest[0] != shown:
            return f"승인할 수 없습니다(요청 상태 {status}). 확인표가 바뀌었으면 /status로 다시 보세요."
        approval_id = str(uuid.uuid4())
        with self.conn.transaction():
            self.conn.execute('SELECT pg_advisory_xact_lock(20261015, 3)')
            # CLI and UI can race: recheck after taking the shared worker lock.
            current = self.conn.execute("SELECT status FROM ingest.requests WHERE request_id = %s FOR UPDATE", (request_id,)).fetchone()
            latest = self.conn.execute("SELECT payload->>'report_sha256' FROM ingest.request_events WHERE request_id = %s AND event = 'staged' "
                                       "AND payload->>'product_id' = %s ORDER BY event_id DESC LIMIT 1", (request_id, product_id)).fetchone()
            if not current or current[0] != 'awaiting_approval' or not latest or latest[0] != shown:
                return '요청 상태나 확인표가 바뀌었습니다. /status로 다시 확인하세요.'
            active = self.conn.execute("SELECT request_id::text FROM ingest.requests WHERE request_id <> %s AND status IN ('queued','staging','approved','promoting') LIMIT 1", (request_id,)).fetchone()
            if active:
                return f"다른 적재 작업({active[0]})이 실행 중입니다. 완료 후 승인해 주세요."
            self.conn.execute("INSERT INTO ingest.approvals (approval_id, request_id, product_id, approved_by, report_sha256) VALUES (%s, %s, %s, %s, %s)",
                              (approval_id, request_id, product_id, getpass.getuser(), shown))
            self.conn.execute("UPDATE ingest.requests SET status = 'approved' WHERE request_id = %s", (request_id,))
        try:
            spawn(["promote", request_id, approval_id], request_id)
        except OSError as exc:
            from ingestion.worker import event, set_status
            event(self.conn, request_id, 'failed', {'step': 'worker_start', 'error': type(exc).__name__})
            set_status(self.conn, request_id, 'failed')
            self.state['stage'] = 'review'
            return '승인은 기록했지만 자료 등록 작업을 시작하지 못했습니다. 작업 로그를 확인하세요.'
        actions.append({"tool": "promote_apply", "request_id": request_id, "approval_id": approval_id})
        self.state["stage"] = "promoting"
        return f"승인을 기록하고 자료 저장소 등록을 시작했습니다({product_id}). 끝나면 DB 사본과 이름 사전도 갱신됩니다. /status로 확인하세요."


def spawn(args, request_id):
    """Run the worker in the background; its output goes to the request's folder."""
    from ingestion.worker import RUNS
    folder = RUNS / request_id
    folder.mkdir(parents=True, exist_ok=True)
    flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if sys.platform == "win32" else 0
    with open(folder / f"worker_{args[0]}.log", "a", encoding="utf-8") as log:
        subprocess.Popen([sys.executable, "-m", "ingestion.worker", *args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--session")
    ap.add_argument("--say")
    a = ap.parse_args()
    with db.connect() as conn:
        agent = Agent(conn, a.session)
        if a.say:
            print(f"[session {agent.session_id}]")
            print(agent.handle(a.say))
        else:
            print(f"적재 요청 에이전트 (session {agent.session_id}). {HELP}")
            while True:
                try:
                    text = input("> ")
                except EOFError:
                    break
                if text.strip() in ("/quit", "/exit"):
                    break
                if text.strip():
                    print(agent.handle(text))
