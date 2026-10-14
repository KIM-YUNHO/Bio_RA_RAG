"""Offline rules of the ingestion request agent. The model step is scored by `python -m ingestion.eval_agent_parse` (calls OpenAI)."""
import json
import unittest

from ingestion import agent, registry
from ingestion.contracts import TurnParse


class AgentRules(unittest.TestCase):
    def test_strict_schema_is_accepted_shape(self):
        schema = agent.strict_schema(TurnParse)
        text = json.dumps(schema)
        for word in agent.UNSUPPORTED:
            self.assertNotIn(f'"{word}"', text)
        for node in [schema] + list(schema.get("$defs", {}).values()):
            self.assertFalse(node["additionalProperties"])
            self.assertEqual(sorted(node["required"]), sorted(node["properties"]))  # strict mode: every field required

    def test_selection_by_key_number_or_prefixed_key(self):
        shown = ["CNTO1275", "SB17", "CODE:DMB3115", "EU:EMEA/H/C/006544"]
        self.assertEqual(agent.map_selection(["SB17"], shown), ["SB17"])
        self.assertEqual(agent.map_selection(["3"], shown), ["CODE:DMB3115"])
        self.assertEqual(agent.map_selection(["CODE:CNTO1275"], shown), ["CNTO1275"])
        self.assertEqual(agent.map_selection(["DMB3115"], shown), ["CODE:DMB3115"])
        self.assertEqual(agent.map_selection(["9", "XYZ"], shown), [])

    def test_regions_must_be_named_in_the_message(self):
        self.assertEqual(agent.grounded_jurisdictions("셀트리온 우스테키누맙 허가 상태 알려줘", ["KR"]), [])
        self.assertEqual(agent.grounded_jurisdictions("웨즐라나 유럽이랑 미국 허가 상태", ["EU", "US"]), ["EU", "US"])
        self.assertEqual(agent.grounded_jurisdictions("BAT2206 국내 허가 됐어?", ["KR"]), ["KR"])
        self.assertEqual(agent.grounded_jurisdictions("is it approved in the us", ["US"]), ["US"])
        self.assertEqual(agent.grounded_jurisdictions("status please", ["US"]), [])  # 'us' only as a word

    def test_hangul_and_latin_share_a_phonetic_key(self):
        for ko, en in [("피즈치바", "Pyzchiva"), ("오툴피", "Otulfi"), ("이뮬도사", "Imuldosa"), ("웨즐라나", "Wezlana"), ("스텔라라", "Stelara"),
                       ("우스테키누맙", "ustekinumab"), ("스테키마", "Steqeyma"), ("예신텍", "Yesintek"), ("휴미라", "Humira")]:
            self.assertEqual(registry.phonetic(ko), registry.phonetic(en), (ko, en))
        self.assertNotEqual(registry.phonetic("Pyzchiva"), registry.phonetic("Stelara"))

    def test_romanization_breaks_ties(self):
        rows = [{"name": n} for n in ("Pyzchiva", "Avzivi", "Fiasp")]
        self.assertEqual([r["name"] for r in registry.closest("피즈치바", rows)], ["Pyzchiva"])


if __name__ == "__main__":
    unittest.main()
