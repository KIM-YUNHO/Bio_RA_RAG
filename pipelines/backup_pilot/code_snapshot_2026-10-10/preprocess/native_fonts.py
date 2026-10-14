"""Verified ToUnicode-only derivatives; preserve original graphics and identity."""
import io,json,re
from pathlib import Path
import fitz
from fontTools.ttLib import TTFont
import text_layer
from .ingest import sha256
from .inventory import write_json

VERSION='verified-font-derivative-1'


def object_value(pdf,xref,skip=()):
    """Compare PDF values independent of dictionary order and object-stream packing."""
    m=fitz.mupdf
    obj=m.pdf_load_object(m.pdf_document_from_fz_document(pdf.this),xref)
    def value(item,excluded=()):
        if m.pdf_is_indirect(item):return ('ref',m.pdf_to_num(item),m.pdf_to_gen(item))
        if m.pdf_is_dict(item):
            pairs=[]
            for i in range(m.pdf_dict_len(item)):
                key=m.pdf_to_name(m.pdf_dict_get_key(item,i))
                if key not in excluded:pairs.append((key,value(m.pdf_dict_get_val(item,i))))
            return ('dict',tuple(sorted(pairs)))
        if m.pdf_is_array(item):return ('array',tuple(value(m.pdf_array_get(item,i)) for i in range(m.pdf_array_len(item))))
        return m.pdf_sprint_obj(None,0,item,1,1)[0]
    return value(obj,skip)


def validate_delta(source, derived, fonts):
    """Every old object/stream stays identical except a verified font ToUnicode link."""
    changed={int(f['xref']):f for f in fonts if f['status']=='repaired'}
    if not changed:raise ValueError('No verified font repair')
    with fitz.open(source) as original,fitz.open(derived) as repaired:
        if len(original)!=len(repaired):raise ValueError('Font repair changed page count')
        links={}
        used={}
        for page in original:
            for span in page.get_texttrace():
                used.setdefault(span['font'],set()).update(g for u,g,*_ in span['chars'] if u==0xFFFD)
        for xref,proof in changed.items():
            kind,value=original.xref_get_key(xref,'ToUnicode')
            if kind!='null':raise ValueError('A mapped source font cannot be replaced')
            old=original.xref_object(xref);new=repaired.xref_object(xref)
            match=re.search(r'/ToUnicode\s+(\d+)\s+0\s+R',new)
            if not match or object_value(original,xref)!=object_value(repaired,xref,('ToUnicode',)):
                raise ValueError('Font repair changed fields beyond ToUnicode')
            stream=int(match[1]);links[xref]=stream
            base=proof['font'].split('+',1)[-1]
            ref=text_layer.reference(base)
            if ref is None:raise ValueError('No reference font for claimed repair')
            embedded=TTFont(io.BytesIO(original.extract_font(xref)[3]));reference,by_glyph=ref
            expected={}
            for gid in sorted(used.get(base,set())):
                mine,theirs=text_layer.glyph_signature(embedded,gid),text_layer.glyph_signature(reference,gid)
                if mine is not None and mine==theirs and gid in by_glyph:
                    character=text_layer.character(base,by_glyph[gid])
                    if character is not None:expected[gid]=character
            if not expected or repaired.xref_stream(stream)!=text_layer.to_unicode_cmap(expected):
                raise ValueError('Font mapping is not backed by identical source glyph outlines and advances')
        if set(links.values())!=set(range(original.xref_length(),repaired.xref_length())):
            raise ValueError('Unexpected objects added during font repair')
        for xref in range(1,original.xref_length()):
            # save(garbage=0) unpacks object streams and regenerates cross-reference
            # storage. Every contained semantic object is still checked by its ID.
            storage=original.xref_get_key(xref,'Type')[1] in ('/ObjStm','/XRef')
            if storage and repaired.xref_object(xref)=='null':continue
            if xref not in changed and object_value(original,xref)!=object_value(repaired,xref):
                raise ValueError('Font repair changed an original PDF object')
            if original.xref_is_stream(xref)!=repaired.xref_is_stream(xref):
                raise ValueError('Font repair changed stream identity')
            if original.xref_is_stream(xref) and original.xref_stream(xref)!=repaired.xref_stream(xref):
                raise ValueError('Font repair changed an original PDF stream')
        return {'old_objects_unchanged_except_verified_font_links':True,
            'original_content_streams_unchanged':True,'verified_fonts':len(changed),'original_pages':len(original)}


def prepare(documents,out):
    for doc in documents:
        if doc.kind!='pdf':continue
        if sha256(doc.raw_path)!=doc.version_id:raise ValueError('Original source changed before font repair')
        destination=Path(out)/'verified_native_pdf'/f'{doc.version_id}.pdf'
        # A failed staging/validation attempt may leave an unregistered derivative.
        # Preserve the source; replace only this exact per-run derived file.
        if destination.is_file():destination.unlink()
        fonts=text_layer.repair(doc.raw_path,destination)
        if not any(f['status']=='repaired' for f in fonts):continue
        checks=validate_delta(doc.raw_path,destination,fonts)
        report={'version':VERSION,'source_sha256':doc.version_id,'derived_sha256':sha256(destination),
            'fonts':fonts,'checks':checks,'repair_code_sha256':sha256(Path(text_layer.__file__)),
            'numeric_evidence_eligible':False}
        report_path=destination.with_suffix('.proof.json');write_json(report_path,report)
        doc.rendered_path=str(destination.resolve());doc.rendered_sha256=report['derived_sha256']
        doc.native_font_repair={'version':VERSION,'path':str(report_path.resolve()),'sha256':sha256(report_path)}


def validate(doc):
    proof=doc.native_font_repair
    if not proof or proof['version']!=VERSION or sha256(proof['path'])!=proof['sha256']:
        raise ValueError('PDF derivative lacks its exact font repair proof')
    report=json.loads(Path(proof['path']).read_text(encoding='utf-8'))
    if (report['source_sha256']!=doc.version_id or report['derived_sha256']!=doc.rendered_sha256
            or report['repair_code_sha256']!=sha256(Path(text_layer.__file__))):
        raise ValueError('Font repair source, derivative or code changed')
    validate_delta(doc.raw_path,doc.rendered_path,report['fonts'])
