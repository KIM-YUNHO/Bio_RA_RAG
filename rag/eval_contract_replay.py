"""Replay saved paired selections after generic fixes; one opt-in query repair.

The repair receives only the original question and its failed interpretation.
No new source selection, retrieval, OCR or embedding is performed. Fixtures and
case IDs are confined to this evaluation tool, never used by runtime rules.
"""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from rag import contract_query, contract_candidate, source_selection
from rag.eval_contract_stage2 import read, write, sha, TESTS, MODULES, BASE, RELEASE
from rag.openai_chat import EvaluationAPIChat, KST
TESTS = [*TESTS, "tests.test_contract_invariants"]

FROZEN = ROOT / "data/runs/rag_contract_stage2_2026-10-11_v4"
RUN = ROOT / "data/runs/rag_contract_stage2_postfix_2026-10-11_v4"
PRIOR_RUNS = [ROOT / f"data/runs/rag_contract_stage2_2026-10-11_v{i}" for i in range(1,5)]
CODE = [*MODULES, "rag/eval_contract_replay.py"]
REPAIR_CASE = "Q038"  # Question-only AND diagnostic; no answer is added to prompts.


def attempts():
    paths = {str((p/"api_eval").resolve()) for p in (ROOT/"data/runs").glob("rag_contract_stage2*") if p.is_dir()}
    return [a for p in (ROOT/"data/runs/model_quota").glob("*.json") for a in read(p)["attempts"]
            if a.get("evaluation_out") in paths]


def check_production():
    p=read(FROZEN/"prepared.json")
    assert all(sha(ROOT/n)==h for n,h in p["production_code_sha256"].items())
    assert sha(RELEASE/"release_manifest.json")==p["release_manifest_sha256"]
    assert sha(RELEASE/"candidate/chunks.jsonl")==p["chunks_sha256"]
    assert sha(FROZEN/"inputs.json")==p["inputs_sha256"]
    assert all(sha(FROZEN/"code_snapshot"/n)==h for n,h in p["candidate_code_sha256"].items())


def check_candidate():
    p=read(RUN/"prepared.json")
    assert all(sha(ROOT/n)==h for n,h in p["candidate_code_sha256"].items()), "postfix candidate changed"
    check_production()


def prepare():
    if (RUN/"prepared.json").exists():
        check_candidate();return
    check_production();RUN.mkdir(parents=True,exist_ok=True)
    with (RUN/"tests.txt").open("w",encoding="utf-8") as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(
            unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(t) for t in TESTS))
    assert result.wasSuccessful()
    p={"version":"contract-postfix-offline-replay-1","paired_run":str(FROZEN),"test_count":result.testsRun,
       "tests_passed":True,"date_kst":datetime.now(KST).date().isoformat(),"prior_stage2_attempts":len(attempts()),
       "fresh_api_cap":min(1,max(0,24-len(attempts()))),"overall_stage2_api_cap":24,"repair_case":REPAIR_CASE,
       "repair_uses_question_and_prior_proposal_only":True,"new_source_selection_calls":0,
       "end_to_end_accuracy_measured":False,"candidate_code_sha256":{n:sha(ROOT/n) for n in CODE},
       "frozen_paired_code_snapshot_preserved":True}
    for n in CODE+[t.replace(".","/")+".py" for t in TESTS]:
        dest=RUN/"code_snapshot"/n;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes((ROOT/n).read_bytes())
    write(RUN/"prepared.json",p);check_candidate()


def original_response(cid):
    matches=[read(f) for f in (FROZEN/"api_eval/api_calls").glob("*.response.json")]
    response=next(r for r in matches if r.get("case")==cid+":contract")
    return json.loads(response["raw_text"])


def replay(case, interpretation, folder):
    write(folder/"interpretation.json",interpretation)
    candidates,audits=contract_candidate.inventory(case["question"],interpretation,case["unit"],case["evidence"])
    write(folder/"source_audits.json",audits)
    modes={}
    for mode in ["baseline","candidate"]:
        old=read(FROZEN/"results"/case["id"]/(mode+".json"))
        new=contract_candidate.validate_selection(old["raw_selection"],candidates,audits,case["evidence"],interpretation,case["question"])
        literal=source_selection.resolve(old["raw_selection"],candidates,case["evidence"],case["question"],interpretation["understanding"])
        assert [(i["value"],i["quote"],i["source_selection"]) for i in literal["items"]]==[
            (i["value"],i["quote"],i["source_selection"]) for i in old["literal_resolved"]["items"]]
        write(folder/(mode+".json"),{"fresh_generation":False,"selection_source":str(FROZEN/"results"/case["id"]/(mode+".json")),
              "validated":new,"literal_values_and_offsets_unchanged":True})
        before=old["contract_validated"]
        modes[mode]={"before_status":before["status"],"after_status":new["status"],
            "before_accepted":len(before["items"]),"after_accepted":len(new["items"]),
            "before_related":len(before["related_items"]),"after_related":len(new["related_items"]),
            "before_rejected":len(before["rejected_items"]),"after_rejected":len(new["rejected_items"]),
            "request_coverage":new["request_coverage"]}
    return {"contract_status":interpretation["requirements"]["status"],"request_count":len(interpretation["requirements"]["requests"]),
            "missing_question_roles":interpretation["requirements"]["missing_known_mentions"],
            "question_warnings":interpretation["requirements"]["warnings"],"modes":modes}


class RepairChat(EvaluationAPIChat):
    def _reserve(self,key,request_path,name,output):
        p=read(RUN/"prepared.json");check_candidate()
        if len(attempts())>=p["overall_stage2_api_cap"]:
            raise RuntimeError("overall stage2 API cap reached")
        if datetime.now(KST).date().isoformat()!=p["date_kst"]:
            raise RuntimeError("date changed; daily usage review required")
        daily=ROOT/"data/runs/model_quota"/(p["date_kst"]+".json")
        if daily.exists() and len(read(daily)["attempts"])>=76:
            raise RuntimeError("original project daily envelope reached")
        return super()._reserve(key,request_path,name,output)


def run(do_repair=False):
    prepare();check_candidate()
    cases=read(FROZEN/"inputs.json");out={"kind":"saved_selection_replay","accuracy_measured":False,"fresh_source_selection_calls":0,"cases":{}}
    for case in cases:
        inter=contract_query.resolve(case["question"],original_response(case["id"]))
        out["cases"][case["id"]]=replay(case,inter,RUN/"replay"/case["id"])
    # Programme assay is an explicit question-only fixture, not an API answer.
    old=read(BASE/"openai_eval/responses/Q033.json");block=old["results"][0]
    proposal={"requests":[{"id":"assay","field":{"dimension":"attribute","value":"assay","anchor":{"quote":"분석법","occurrence":0}},"conditions":[]}],"non_request_mentions":[]}
    from rag.request_contract import ground
    inter={"understanding":old["understanding"],"requirements":ground(old["question"],proposal)}
    candidates,audits=contract_candidate.inventory(old["question"],inter,block["unit"],block["evidence"])
    methods=[{"source_id":cid,"value":candidates[cid]["value"],"status":a["status"],"scope":a["fact"].get("scope_audit")}
             for cid,a in audits.items() if a["fact"] and "MSD-ECL" in candidates[cid]["value"] and candidates[cid]["kind"]=="source_paragraph"]
    assert methods and all(m["status"]=="unknown" for m in methods)
    # Full programme paragraph is related; individual sentences without its
    # scope statement stay unknown. Do not copy that scope into every fragment.
    assert any(m["scope"]["requested"][0]["status"]=="related" for m in methods)
    write(RUN/"assay_offline_fixture.json",{"model_calls":0,"programme_methods_not_certified_for_trial":True,"methods":methods})
    if do_repair:
        if read(RUN/"prepared.json")["fresh_api_cap"] == 0:
            raise RuntimeError("API evaluation cap exhausted; use offline replay")
        case=next(c for c in cases if c["id"]==REPAIR_CASE)
        client=RepairChat(RUN/"api_eval",max_new_calls=1);client.config.num_ctx=24576;client.config.num_predict=4500
        client.case=REPAIR_CASE+":question_repair"
        try:
            inter=contract_query.repair(case["question"],original_response(REPAIR_CASE),chat_client=client)
            write(RUN/"repair_interpretation.json",inter)
            out["question_repair"]={"case":REPAIR_CASE,"fresh_model_calls":1,"source_selection_replayed_not_regenerated":True,
                                    **replay(case,inter,RUN/"repaired_replay"/REPAIR_CASE)}
        finally:
            client.close()
    out["all_stage2_attempts"]=len(attempts());out["api_statuses"]=dict(Counter(a.get("status") for a in attempts()))
    out["production_unchanged"]=True;out["frozen_paired_results_preserved"]=True
    check_candidate();write(RUN/"summary.json",out)
    print(json.dumps({"cases":{cid:{"contract":c["contract_status"],"candidate":c["modes"]["candidate"]} for cid,c in out["cases"].items()},
        "question_repair":out.get("question_repair"),"all_stage2_attempts":out["all_stage2_attempts"]},ensure_ascii=False),flush=True)


if __name__=="__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument("--repair",action="store_true");args=parser.parse_args();run(args.repair)
