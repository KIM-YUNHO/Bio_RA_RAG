"""Separate independently headed tables from an over-broad caption group.

No cells, source text, figure groups or clinical assignments are changed. Missing
headers remain unresolved; a proximity link is never a certificate.
"""
import re
from .table_quality import TableHTML

VERSION = 'table-caption-ownership-1'


def header(asset):
    parser = TableHTML()
    parser.feed(asset['selected']['content']); parser.close()
    if parser.errors or not parser.rows:
        return None
    labels = [re.sub(r'[^a-z0-9]+', '', cell['text'].casefold()) for cell in parser.rows[0]]
    # A data continuation may start with a row label and numerical result cells.
    if sum(bool(re.match(r'^[a-z]', value) and len(re.findall('[a-z]', value)) >= 4) for value in labels) < 2:
        return None
    return tuple(labels)


def separate(assets, groups):
    from .visual_groups import display_box, horizontal_overlap
    lookup = {a['asset_id']: a for a in assets}; decisions = []
    for group in list(groups):
        if group['kind'] != 'table' or not group.get('caption_asset_id') or len(group['member_asset_ids']) < 2:
            continue
        members = sorted((lookup[uid] for uid in group['member_asset_ids']),
                         key=lambda a: (a['pdf_page'], display_box(a)[1]))
        primary = members[0]; signature = header(primary)
        if not signature:
            continue
        detached = []
        for table in members[1:]:
            other = header(table)
            if (table['pdf_page'] == primary['pdf_page'] and other and other != signature
                    and display_box(table)[1] >= display_box(primary)[3]
                    and horizontal_overlap(display_box(table), display_box(primary)) >= .4):
                detached.append(table)
        if not detached:
            continue
        for uid in group.get('context_asset_ids', []):
            if uid not in group['caption_part_asset_ids']:
                lookup[uid].pop('logical_group_id', None)
                lookup[uid].pop('table_context_role', None)
        group['relations'] = [r for r in group.get('relations', [])
                              if not r.get('relation', '').startswith('adjacent_printed_table_')]
        for table in detached:
            uid = table['asset_id']; group['member_asset_ids'].remove(uid)
            table.pop('logical_group_id', None)
            decisions.append({'asset_id': uid, 'previous_group_id': group['group_id'],
                              'reason': 'distinct_printed_column_headers', 'version': VERSION})
        group['relations'] = [r for r in group['relations'] if r.get('asset_id') not in {a['asset_id'] for a in detached}]
    return decisions
