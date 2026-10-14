"""Attach printed table context by geometry without changing cells or labels."""
from collections import defaultdict
import re

from .ingest import stable_hash

VERSION = 'table-context-2'


def text(asset):
    native = asset['native_reference']
    return native['text'].strip() if native['reliable'] else asset['base']['content'].strip()


def note_role(asset):
    if asset['asset_type'] != 'text':
        return None
    value = text(asset)
    if re.match(r'\s*(source(?:\s+data)?|data\s+source|出典|資料出所|来源)\s*[:：]', value, re.I):
        return 'source'
    if re.match(r'\s*(units?|単位|单位)\s*[:：]', value, re.I):
        return 'unit'
    if asset['block_label'] in {'vision_footnote', 'footnote', 'table_footnote'}:
        return 'footnote'
    if asset['block_label'] == 'text' and re.match(r'\s*(notes?|abbreviations?|注)\s*[:：]', value, re.I):
        return 'footnote'
    return None


def assemble(assets, groups, grouping_version):
    # Import here to keep geometry functions in the existing grouping module.
    from .visual_groups import display_box, horizontal_overlap
    from .table_caption_ownership import separate
    separate(assets, groups)
    by_page = defaultdict(list)
    lookup = {a['asset_id']: a for a in assets}
    for a in assets:
        by_page[(a['document_id'], a['version_id'], a['pdf_page'])].append(a)
    owned = {uid for g in groups for uid in g['member_asset_ids']}
    for table in assets:
        if table['asset_type'] != 'table' or table['asset_id'] in owned:
            continue
        gid = 'g'+stable_hash([table['document_id'], table['version_id'], 'table', table['asset_id']])[:26]
        table['logical_group_id'] = gid
        groups.append({'group_id': gid, 'document_id': table['document_id'], 'version_id': table['version_id'],
            'kind': 'table', 'label': None, 'caption_asset_id': None, 'caption_part_asset_ids': [],
            'caption_text': '', 'caption_origin': None, 'member_asset_ids': [table['asset_id']],
            'association_status': 'standalone_region', 'continuation_status': 'not_detected',
            'relations': [], 'grouping_version': grouping_version})
    tables = [g for g in groups if g['kind'] == 'table']
    owners = {uid: g for g in tables for uid in g['member_asset_ids']}
    for g in tables:
        for uid in g.get('context_asset_ids', []):
            if uid not in g['caption_part_asset_ids'] and lookup[uid].get('logical_group_id') == g['group_id']:
                lookup[uid].pop('logical_group_id', None)
                lookup[uid].pop('table_context_role', None)
        g['relations'] = [r for r in g.get('relations', [])
                          if not r.get('relation', '').startswith('adjacent_printed_table_')]
        g['context_version'] = VERSION
        g['context_asset_ids'] = list(g['caption_part_asset_ids'])
        g['context_roles'] = {uid: 'caption' if i == 0 else 'caption_continuation'
                              for i, uid in enumerate(g['caption_part_asset_ids'])}
        g['context_issues'] = []
    for note in sorted(assets, key=lambda a: (a['document_id'], a['version_id'], a['pdf_page'], display_box(a)[1])):
        role = note_role(note)
        if role is None or note.get('logical_group_id'):
            continue
        nb = display_box(note)
        rows = by_page[(note['document_id'], note['version_id'], note['pdf_page'])]
        candidates = []
        for visual in rows:
            if visual['asset_type'] not in {'figure', 'table'}:
                continue
            vb = display_box(visual)
            gap = nb[1]-vb[3]
            if -2 <= gap <= 160 and horizontal_overlap(nb, vb) >= .4:
                blockers = [r for r in rows if r not in (visual, note)
                    and vb[3] <= display_box(r)[1] and display_box(r)[3] <= nb[1]
                    and horizontal_overlap(nb, display_box(r)) >= .15
                    and not note_role(r)]
                if not blockers:
                    candidates.append((max(gap, 0), visual))
        candidates.sort(key=lambda item: item[0])
        # A nearby figure wins over a more distant table; close ties stay unassigned.
        if not candidates or len(candidates) > 1 and candidates[1][0]-candidates[0][0] < 15:
            if candidates:
                note['context_association_issues'] = ['ambiguous_table_context_owner']
                for _, candidate in candidates:
                    if candidate['asset_id'] in owners:
                        owners[candidate['asset_id']]['context_issues'].append({'asset_id': note['asset_id'], 'reason': 'ambiguous_note_owner'})
            continue
        visual = candidates[0][1]
        if visual['asset_id'] not in owners:
            continue
        g = owners[visual['asset_id']]
        note['logical_group_id'] = g['group_id']
        g['context_asset_ids'].append(note['asset_id'])
        g['context_roles'][note['asset_id']] = role
        g['relations'].append({'asset_id': note['asset_id'], 'relation': 'adjacent_printed_table_'+role})
    for g in tables:
        related = [lookup[uid] for uid in [*g['caption_part_asset_ids'], *g['member_asset_ids'],
                   *[uid for uid in g['context_asset_ids'] if uid not in g['caption_part_asset_ids']]]]
        g['source_refs'] = [ref for a in related for ref in a['source_refs']]
        g['pdf_pages'] = sorted({a['pdf_page'] for a in related})
        for uid, role in g['context_roles'].items():
            a = lookup[uid]
            a['table_context_role'] = role
            # Reliable native text keeps e.g. Japanese footnotes in the original script.
            if a['native_reference']['reliable'] and not a['route']['task']:
                a['selected'] = {'origin': 'native_table_context', 'content': text(a)}
