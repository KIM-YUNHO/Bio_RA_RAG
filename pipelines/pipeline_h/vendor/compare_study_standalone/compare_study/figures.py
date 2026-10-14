"""Common visual output in unrotated PDF top-left points; never count scan bitmaps as figures."""
import copy
import re
from pathlib import Path
import fitz
from .bench.common import sha


def chart_cues(text):
    lower = text.lower()
    scale = bool(re.search(r'(semi.?logarithmic|linear scale|logarithmic scale)', lower))
    axes = bool(re.search(r'\b(time|day|hour|week)\b', lower)) and bool(
        re.search(r'concentration|ng\s*/\s*ml|\baxis\b|absorbance|survival', lower))
    return scale or axes


def decorative(box, size, label=''):
    # Geometric furniture rule shared by all paths; label alone can also mark a logo.
    return label == 'header_image' or (box[3] < size[1]*.12 and
        (box[2]-box[0])*(box[3]-box[1]) < size[0]*size[1]*.035)


def figure(identity, box, kind='figure', text='', caption='', group=False, parent=None,
           model=None, status='detected', interpretation=None, history=None):
    return {'figure_id': identity, 'bbox': box, 'kind': kind, 'ocr_text': text,
            'caption': caption, 'is_logical_group': group, 'parent_id': parent,
            'model': model, 'status': status, 'interpretation': interpretation,
            'kind_history': history or [kind]}


def from_assets(assets, physical_page, model):
    out = []
    for a in assets:
        if a['pdf_page'] != physical_page or a['asset_type'] not in ('figure','figure_group'):
            continue
        e = a.get('enrichment') or {}
        answer = (e.get('validation') or {}).get('answer')
        f = figure(a['asset_id'], a['bbox_pdf'], a['block_label'],
                   a['base'].get('printed_text_reference',''),
                   a['base'].get('content','') if a.get('is_logical_group') else '',
                   bool(a.get('is_logical_group')), a.get('route',{}).get('parent_asset_id'),
                   (e.get('model') or {}).get('name', a['base'].get('engine',model)), e.get('status','detected'), answer)
        f.update(enrichment=copy.deepcopy(e), member_ids=a.get('member_asset_ids',[]),
                 panel_index=a.get('panel_index'), detection_model='PP-DocLayoutV3')
        out.append(f)
    return out


def finalize(row, path, physical_page, work):
    with fitz.open(path) as pdf:
        page = pdf[physical_page-1]
        size = [page.rect.width, page.rect.height]
        row['embedded_image_objects'] = len(page.get_images(full=True))
        figures = row.setdefault('figures', [])
        for i, f in enumerate(figures):
            f['decorative'] = decorative(f['bbox'], size, f['kind'])
            f['source_page'] = row['page']
            box = fitz.Rect(f['bbox']) & page.rect
            if not box.is_empty:
                crop = Path(work)/'figure_crops'/f'p{physical_page:03d}_{i:02d}.png'
                crop.parent.mkdir(exist_ok=True)
                page.get_pixmap(dpi=300, clip=box, alpha=False).save(crop)
                f.update(crop_path=str(crop), crop_sha256=sha(crop), crop_dpi=300)
    semantic = [f for f in figures if not f['decorative']]
    groups = [f for f in semantic if f['is_logical_group']]
    targets = [f for f in semantic if not f.get('parent_id')]
    attempted = [f for f in targets if f.get('interpretation') is not None or f.get('enrichment')]
    row['figure_counts'] = {'figure_regions':sum(not f['is_logical_group'] for f in semantic),
        'figure_groups':len(groups), 'logical_figures':len(targets),
        'decorative_images':sum(f['decorative'] for f in figures),
        'interpretation_attempted':len(attempted),
        'interpretation_completed':sum(f['status'] in ('heuristic_checks_passed','transcribed_unverified') for f in attempted),
        'interpretation_needs_review':sum(f['status'] not in ('heuristic_checks_passed','transcribed_unverified') for f in attempted)}
    row['flags']['figure_count_policy'] = 'physical regions and caption groups separate; no parent/child double count; scan image objects separate'
    return row


def attach_document_structure(row, blocks, physical_page, height):
    """Retain RA headings, speaker, furniture, redaction, study IDs and cell review state."""
    selected=[]
    for b in blocks:
        if b['page']!=physical_page:continue
        item=copy.deepcopy(b);l,bot,r,top=b['bbox']
        item['source_bbox_bottom_left']=item['bbox'];item['bbox']=[l,height-top,r,height-bot]
        item['coordinate_space']='PDF top-left points';selected.append(item)
        if b['type']=='table':item['cell_coordinate_space']='PDF bottom-left points (original ra_prep)'
    row['document_structure']=selected
    row['flags']['document_structure_summary']={
        'section_paths':sorted({tuple(b.get('section_path',[])) for b in selected if not b.get('furniture')}),
        'speakers':sorted({b.get('speaker','unknown') for b in selected if not b.get('furniture')}),
        'furniture_blocks':sum(bool(b.get('furniture')) for b in selected),
        'redacted_blocks':sum(bool(b.get('redacted')) for b in selected),
        'study_ids':sorted({s for b in selected for s in b.get('study_ids',[])}),
        'review_blocks':sum(b.get('status')=='needs_review' for b in selected)}
