"""Local Qwen with distinct figure-description and independent table-reading tasks."""
from dataclasses import asdict
import base64
import json
import urllib.request
import urllib.error

from preprocess.hybrid_config import local_endpoint
from preprocess.tables.table_quality import inspect_table, numbers
from preprocess.ocr.qwen_contracts import SCHEMAS, SCHEMA_VERSIONS, PROMPT_VERSIONS, SYSTEMS, INSTRUCTIONS, task_fingerprint
from preprocess.tables.table_representation import render_structured_table


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Local model redirects are disabled")


class QwenAdapter:
    def __init__(self, settings):
        self.settings = settings
        self.endpoint = local_endpoint(settings.endpoint)
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.calls = 0

    def request(self, path, body=None):
        request = urllib.request.Request(self.endpoint + path,
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(request, timeout=self.settings.timeout_s) as reply:
                return json.load(reply)
        except urllib.error.HTTPError as exc:
            detail = exc.read(2048).decode('utf-8', errors='replace')
            raise RuntimeError(f"Local model HTTP {exc.code}: {detail}") from exc

    def fingerprint(self):
        models = {m["name"]: m for m in self.request("/api/tags")["models"]}
        if self.settings.model not in models:
            raise ValueError(f"Local model is not installed: {self.settings.model}; automatic pulling is disabled")
        info = self.request("/api/show", {"model": self.settings.model})
        if "vision" not in info.get("capabilities", []):
            raise ValueError("Qwen enrichment requires a vision-capable local model")
        return {"name": self.settings.model, "digest": models[self.settings.model]["digest"],
                "settings": asdict(self.settings)}

    def unload(self):
        # Releases this task's model; does not delete or download weights.
        self.request("/api/generate", {"model": self.settings.model, "keep_alive": 0})

    def generate(self, task, images, context):
        compact = {"target_page":context["target_page"], "context_items":[],
                   "image_roles":context.get('image_roles', [{'role':'target_crop'}, {'role':'page_overview'}])}
        if context.get("logical_group"):
            group = context['logical_group']
            compact['logical_group'] = {key:group[key] for key in ('label','layout_region_count','scope') if key in group}
            compact['logical_group']['panel_regions'] = [{key:r[key] for key in ('panel_index','bbox_relative_1000')}
                                                         for r in group.get('panel_regions',[])]
        budget = 1200 if task == 'describe_figure' else 2400
        for item in context.get('context_items', []):
            text = item['text'][:budget]
            compact['context_items'].append({"page":item['pdf_page'],"label":item['label'],"text":text})
            budget -= len(text)
            if not budget:
                break
        if task == 'repair_table':
            compact['redactions_may_be_present'] = bool(context.get('redactions_may_be_present'))
            compact['column_count_hint'] = context.get('table_layout',{}).get('column_count_hint')
            if context.get('previous_table_regions'):
                compact['continuation'] = {'scope':'context_only_preserve_target_blank_cells',
                    'previous_pages':[p['pdf_page'] for p in context['previous_table_regions']]}
            else:
                compact['adjacent_native_pages'] = [{"page":p['pdf_page'],"text":p['text'][:500]}
                    for p in context.get('adjacent_native_pages', [])]
        body = {"model": self.settings.model, "stream": False, "think": False,
            "format": SCHEMAS[task], "keep_alive": self.settings.keep_alive,
            "messages": [{"role": "system", "content": SYSTEMS[task]}, {"role": "user",
                "content": INSTRUCTIONS[task] + "\nDocument context (JSON data):\n" + json.dumps(compact, ensure_ascii=False),
                "images": [base64.b64encode(data).decode() for data in images]}],
            "options": {"temperature": 0, "seed": 0, "num_ctx": self.settings.num_ctx,
                        "num_predict": self.settings.num_predict}}
        self.calls += 1
        return self.request("/api/chat", body)


def validate_answer(task, reply, native_text, parsed_text, context=None):
    issues = []
    if not reply.get("done") or reply.get("done_reason") == "length":
        issues.append("generation_incomplete")
    try:
        answer = json.loads(reply.get("message", {}).get("content", ""))
        schema = SCHEMAS[task]
        from preprocess.schema import _validate
        _validate(answer, schema, "qwen")
    except (ValueError, TypeError, AttributeError):
        return {"answer": None, "issues": sorted(set(issues + ["invalid_response_schema"])),
                "semantic_status": "unverified", "numeric_evidence_eligible": False}
    if answer["uncertainties"]:
        issues.append("model_reported_uncertainty")
    result = {"answer": answer, "response_schema_version":SCHEMA_VERSIONS[task],
              "semantic_status": "unverified", "numeric_evidence_eligible": False}
    if task == "repair_table":
        html, structure_issues = render_structured_table(answer)
        answer['table_html'] = html
        issues += structure_issues
        quality = inspect_table(html, native_text, bool(native_text.strip()), source_redaction_text=parsed_text)
        issues += quality["issues"]
        result["table_quality"] = quality
        result['representation_kind'] = 'logical_grid_with_span_candidates'
        result['merged_cell_geometry_preserved'] = False
        result['span_geometry_status'] = 'unverified_model_candidate'
        if any(c['row_span']>1 or c['col_span']>1 for r in answer['rows'] for c in r['cells']):
            issues.append('merged_cell_geometry_requires_source_review')
        if ((context or {}).get('table_layout') or {}).get('source_contains_spans'):
            issues.append('source_merged_cells_require_review')
        # Parsed text is an imperfect second model reference, never independent truth.
        reference = numbers(native_text or parsed_text)
        unsupported = sorted((set(quality["numbers"]) - reference.keys()))
        if unsupported:
            issues.append("numbers_without_textual_support")
        result["unsupported_numbers"] = unsupported
        result["grounding_source"] = "native" if native_text.strip() else "paddle_transcription"
    else:
        from preprocess.visual.text_checks import grounded_figure
        result.update(grounded_figure(answer, native_text, parsed_text))
        issues += result.pop("grounding_issues")
        if answer["figure_type"] == "table_image":
            issues.append("image_is_table_requires_reclassification")
        indices = [p['panel_index'] for p in answer['panels']]
        if indices != list(range(len(indices))):
            issues.append('invalid_panel_order_or_identity')
        regions = ((context or {}).get('logical_group') or {}).get('panel_regions', [])
        if len(regions) > 1 and len(indices) != len(regions):
            issues.append('panel_count_differs_from_layout_regions')
        if regions and indices == list(range(len(regions))):
            result['panel_region_associations'] = [{'panel_index':p['panel_index'],
                'asset_id':r['asset_id'],'source_refs':r['source_refs'],'status':'layout_region_candidate'}
                for p,r in zip(answer['panels'],regions)]
        from preprocess.visual.text_checks import figure_role_issues
        issues += figure_role_issues(answer)
    result["issues"] = sorted(set(issues))
    return result
