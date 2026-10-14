"""Score the agent's parsing step (gpt-4o-mini -> TurnParse) on eval/agent_utterances_v0.1.json. Exit 1 on any miss.

Each case makes one OpenAI call (recorded in ingest.model_calls, purpose agent_benchmark). A case passes when the intent, the
jurisdictions and the selected keys match, every expected mention is found and resolves through the registry (its text, else the
model's english_guess) to the expected product, and no mention names something the message does not contain.
Usage: uv run python -m ingestion.eval_agent_parse
"""
import json
import sys
import uuid

from ingestion import agent, db, registry
from ingestion.contracts import ROOT
from ingestion.sources import norm

CASES = ROOT / "eval/agent_utterances_v0.1.json"


def resolves_to(conn, mention, expected):
    for query in [mention.text, mention.english_guess]:
        if query:
            r = registry.resolve(conn, query)
            if r.candidates and len(r.candidates) == 1:
                return r.candidates[0].key.startswith(expected) or r.candidates[0].key == expected, r.candidates[0].key
    return False, None


def score(conn, data):
    session = str(uuid.uuid4())
    results, made_up = [], 0
    for case in data["cases"]:
        context = {"list": data["list_context"], "plan": data["plan_context"]}.get(case.get("context"), "stage: idle")
        parse = agent.openai_parse(conn, session, case["text"], context)
        parse = parse.model_copy(update={"jurisdictions": agent.grounded_jurisdictions(case["text"], parse.jurisdictions)})  # as the agent does
        problems = []
        if parse.intent != case["intent"]:
            problems.append(f"intent {parse.intent} != {case['intent']}")
        fabricated = [m.text for m in parse.mentions if norm(m.text) not in norm(case["text"])]
        if case.get("context") != "list":  # in a list reply the code ignores mentions
            made_up += len(fabricated)
            if fabricated:
                problems.append(f"names not in the message: {fabricated}")
        for text, expected in case["mentions"].items():
            m = next((m for m in parse.mentions if norm(m.text) == norm(text)), None)
            if not m:
                problems.append(f"missing mention {text}")
                continue
            ok, got = resolves_to(conn, m, expected)
            if not ok:
                problems.append(f"{text} (guess {m.english_guess!r}) resolved to {got}, expected {expected}")
        if sorted(parse.jurisdictions) != sorted(case["jurisdictions"]):
            problems.append(f"jurisdictions {parse.jurisdictions} != {case['jurisdictions']}")
        if "selected_keys" in case:
            shown = [line.split(". ", 1)[1].split(": ")[0] for line in data["list_context"].splitlines() if line[:1].isdigit()]
            got = agent.map_selection(parse.selected_keys, shown)  # the same mapping the agent applies
            if got != case["selected_keys"]:
                problems.append(f"selected {parse.selected_keys} != {case['selected_keys']}")
        results.append({"text": case["text"], "ok": not problems, **({"problems": problems, "parse": parse.model_dump()} if problems else {})})
    conn.execute("INSERT INTO ingest.model_calls (purpose, session_id, model_requested, request_sha256, ok, error) VALUES ('agent_benchmark_marker', %s, %s, %s, true, %s)",
                 (session, agent.MODEL, "0" * 64, f"benchmark run over {len(results)} cases; agent_turn calls with this session_id belong to it"))
    return results, made_up


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads((ROOT / sys.argv[1] if len(sys.argv) > 1 else CASES).read_text(encoding="utf-8"))
    with db.connect() as conn:
        results, made_up = score(conn, data)
        tokens = conn.execute("SELECT sum(prompt_tokens), sum(completion_tokens) FROM ingest.model_calls WHERE at > now() - interval '30 minutes' AND purpose = 'agent_turn'").fetchone()
    misses = [r for r in results if not r["ok"]]
    print(json.dumps({"cases": len(results), "passed": len(results) - len(misses), "made_up_names": made_up,
                      "tokens_last_30_min": {"prompt": tokens[0], "completion": tokens[1]}, "misses": misses}, ensure_ascii=False, indent=2))
    sys.exit(1 if misses or made_up else 0)
