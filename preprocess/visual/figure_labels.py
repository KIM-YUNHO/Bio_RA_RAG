"""Literal, field-level figure retrieval; no curve values or generated prose."""
import re
import unicodedata

from preprocess.visual.text_checks import figure_label_reference
from preprocess.ingest import stable_hash

VERSION = 'figure-labels-4'


def normalized(text, version=VERSION):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = re.sub(r'\s+', ' ', text).strip()
    if version in {'figure-labels-2','figure-labels-3','figure-labels-4'}:
        text = re.sub(r'\s*([()%,/])\s*', r'\1', text)
        text = re.sub(r'(?<=[\u3040-\u30ff\u3400-\u9fff])\s+|\s+(?=[\u3040-\u30ff\u3400-\u9fff])', '', text)
    return text


def support(label, references, role=None, version=VERSION):
    if not label or not any(c.isalpha() for c in label):
        return None
    needle = normalized(label, version)
    for ref in references:
        if role and ref.get('allowed_roles') and role not in ref['allowed_roles']:
            continue
        # Match a contiguous phrase, not a bag of words from different fields.
        haystack = normalized(ref['text'], version)
        start = haystack.find(needle)
        while start >= 0:
            end = start + len(needle)
            left_ok = not needle[0].isascii() or not needle[0].isalnum() or start == 0 or not haystack[start-1].isalnum()
            right_ok = not needle[-1].isascii() or not needle[-1].isalnum() or end == len(haystack) or not haystack[end].isalnum()
            if left_ok and right_ok:
                return {**ref, 'normalized_match_range': [start, end]}
            start = haystack.find(needle, start+1)
    return None


def references_for(member, titles, version=VERSION):
    refs = []
    def add(asset, text, origin):
        clean = figure_label_reference(text)
        if clean.strip():
            refs.append({'asset_id': asset['asset_id'], 'origin': origin, 'text': clean,
                         'source_refs': asset['source_refs']})
    if member['native_reference']['reliable']:
        add(member, member['native_reference']['text'], 'native')
    extra = member.get('label_ocr') or {}
    if extra.get('status') == 'complete':
        add(member, extra['text'], 'paddle_high_resolution_ocr')
    for extra in member.get('label_ocr_regions', []):
        if extra.get('status') == 'complete':
            clean = figure_label_reference(extra['text'])
            if clean.strip():
                refs.append({'asset_id': member['asset_id'], 'origin': 'paddle_targeted_label_ocr',
                    'text': clean, 'source_refs': extra['source_refs'],
                    'allowed_roles': extra['allowed_roles'], 'rotation_degrees': extra['rotation_degrees']})
    add(member, member['base'].get('printed_text_reference') or '', 'paddle_region_ocr')
    if version in {'figure-labels-3','figure-labels-4'}:
        proof=(member.get('light_region_ocr') or {}).get('provenance') or member['base'].get('light_ocr_provenance',{})
        for index,token in enumerate(proof.get('ocr',{}).get('tokens',[])):
            clean=figure_label_reference(token['text'])
            if token['confidence']>=.8 and clean.strip():
                refs.append({'asset_id':member['asset_id'],'origin':'pp_ocr_mobile_literal','text':clean,
                    'source_refs':member['source_refs'],'token_index':index,
                    'bbox_crop_px':token['bbox'],'confidence':token['confidence']})
    for title in titles:
        add(title, title['native_reference']['text'] if title['native_reference']['reliable'] else title['base']['content'],
            'native_panel_title' if title['native_reference']['reliable'] else 'paddle_panel_title')
    return refs


def project(parent, group, by_id, version=VERSION):
    if version not in {'figure-labels-1','figure-labels-2','figure-labels-3', VERSION}:
        raise ValueError('Unknown figure label projection version')
    caption = group['caption_text']
    checked = (parent.get('enrichment') or {}).get('validation') or {}
    answer = checked.get('answer')
    members = [by_id[uid] for uid in group['member_asset_ids']]
    panels = answer.get('panels', []) if answer else []
    invalid = {'invalid_response_schema', 'generation_incomplete', 'invalid_panel_order_or_identity',
               'panel_count_differs_from_layout_regions', 'image_is_table_requires_reclassification'} & set(checked.get('issues', []))
    if not answer or invalid or [p['panel_index'] for p in panels] != list(range(len(members))):
        # Ordinary OCR supplies printed labels without a model guessing axis,
        # legend or panel semantics. Keep every field attached to its crop.
        if all(m['base'].get('light_ocr_provenance') is not None or m.get('light_region_ocr') for m in members):
            fields=[];lines=[caption] if caption.strip() else []
            for i,m in enumerate(members):
                proof=m.get('light_region_ocr',{}).get('provenance') or m['base'].get('light_ocr_provenance',{})
                for token in proof.get('ocr',{}).get('tokens',[]):
                    clean=figure_label_reference(token['text'])
                    if token['confidence']<.8 or not clean.strip():continue
                    evidence={'asset_id':m['asset_id'],'origin':'pp_ocr_mobile_literal','text':clean,
                        'source_refs':m['source_refs'],'bbox_crop_px':token['bbox'],'confidence':token['confidence']}
                    fields.append({'role':'printed_label','panel_index':i,'candidate':clean,'text':clean,
                        'status':'text_supported','evidence':evidence,'role_status':'unassigned_ocr_literal',
                        'numeric_evidence_eligible':False})
                    lines.append(clean)
            result={'version':version,'fields':fields,'accepted_fields':len(fields),'rejected_fields':0,
                'status':'ocr_literals_only','text':'\n'.join(lines),'numeric_evidence_eligible':False,
                'semantic_status':'unverified','description_review_required':True}
            result['projection_sha256']=stable_hash(result)
            return result
        return {'version': version, 'fields': [], 'accepted_fields': 0, 'rejected_fields': 0,
                'status': 'no_usable_candidate', 'text': caption, 'numeric_evidence_eligible': False}
    title_by_panel = {i: [] for i in range(len(members))}
    for uid in group.get('panel_title_asset_ids', []):
        title = by_id[uid]
        candidates = []
        from preprocess.visual.visual_groups import display_box, horizontal_overlap
        for i, member in enumerate(members):
            tb, mb = display_box(title), display_box(member)
            gap = mb[1]-tb[3]
            if 0 <= gap <= 60 and horizontal_overlap(tb, mb) >= .4:
                candidates.append((gap, i))
        candidates.sort()
        if candidates and (len(candidates) == 1 or candidates[1][0]-candidates[0][0] >= 15):
            title_by_panel[candidates[0][1]].append(title)
    refs = [references_for(m, title_by_panel[i], version) for i, m in enumerate(members)]
    for uid in group.get('figure_context_asset_ids', []):
        extra = by_id[uid]
        index = group['member_asset_ids'].index(extra['native_figure_owner_id'])
        refs[index].append({'asset_id':uid,'origin':'native_visual_context','text':extra['native_reference']['text'],
            'source_refs':extra['source_refs'],'allowed_roles':['legend','printed_label']})
    fields = []
    def field(role, value, index=None):
        if not value:
            return
        # Some old responses prepend an unprinted serialization label.
        literal = re.sub(r'^title:\s*', '', value).strip()
        evidence = support(literal, refs[index] if index is not None else [r for rows in refs for r in rows], role, version)
        fields.append({'role': role, 'panel_index': index, 'candidate': value, 'text': literal,
                       'status': 'text_supported' if evidence else 'needs_review', 'evidence': evidence,
                       'role_status': 'vlm_candidate', 'numeric_evidence_eligible': False})
    for i, panel in enumerate(panels):
        for role in ('x_axis', 'y_axis', 'scale_label'):
            field(role, panel.get(role), i)
    for role in ('legend', 'printed_label'):
        for value in answer['legend' if role == 'legend' else 'printed_labels']:
            field(role, value)
    if version=='figure-labels-4':
        # A VLM assigning roles must not remove readable ordinary OCR literals
        # merely because it omitted them from its response. Preserve their own
        # crop/token proof without promoting them to an axis or legend role.
        existing={(f['panel_index'],normalized(f['text'],version)) for f in fields if f['status']=='text_supported'}
        for i,m in enumerate(members):
            proof=(m.get('light_region_ocr') or {}).get('provenance') or m['base'].get('light_ocr_provenance',{})
            for index,token in enumerate(proof.get('ocr',{}).get('tokens',[])):
                clean=figure_label_reference(token['text']);identity=(i,normalized(clean,version))
                if token['confidence']<.8 or not clean.strip() or identity in existing:continue
                fields.append({'role':'printed_label','panel_index':i,'candidate':clean,'text':clean,
                    'status':'text_supported','evidence':{'asset_id':m['asset_id'],'origin':'pp_ocr_mobile_literal',
                        'text':clean,'source_refs':m['source_refs'],'token_index':index,
                        'bbox_crop_px':token['bbox'],'confidence':token['confidence']},
                    'role_status':'unassigned_ocr_literal','numeric_evidence_eligible':False})
                existing.add(identity)
    accepted = [f for f in fields if f['status'] == 'text_supported']
    lines = [caption] if caption.strip() else []
    for f in accepted:
        lines.append(f"Panel {f['panel_index']+1} {f['role']}: {f['text']}" if f['panel_index'] is not None
                     else f"{f['role']}: {f['text']}")
    result = {'version': version, 'fields': fields, 'accepted_fields': len(accepted),
              'rejected_fields': len(fields)-len(accepted), 'status': 'partial' if len(accepted) != len(fields) else 'text_supported',
              'text': '\n'.join(lines), 'numeric_evidence_eligible': False,
              'semantic_status': 'unverified', 'description_review_required': True}
    result['projection_sha256'] = stable_hash(result)
    return result


def scopes(assets, groups):
    yield from (g for g in groups or [] if g['kind'] == 'figure')
    for a in assets:
        if a['asset_type'] == 'figure' and not a.get('logical_group_id'):
            yield standalone_scope(a)


def standalone_scope(asset):
    return {'group_id': asset['asset_id'], 'kind': 'figure', 'caption_text': '',
            'caption_origin': 'paddle', 'member_asset_ids': [asset['asset_id']], 'panel_title_asset_ids': []}


def apply(assets, groups):
    for asset in assets:
        if asset['asset_type'] == 'figure' and asset.get('logical_group_id'):
            asset.pop('figure_labels', None)
    lookup = {a['asset_id']: a for a in assets}
    for group in scopes(assets, groups):
        parent = lookup[group['group_id']]
        result = project(parent, group, lookup)
        parent['figure_labels'] = result
        # Keep the complete VLM response under enrichment. Search uses literals only.
        parent['selected'] = {'origin': 'figure_labels_candidate' if result['accepted_fields'] else
                             ('native_figure_caption' if group['caption_origin'] == 'native' else 'paddle_figure_caption'),
                             'content': result['text']}
