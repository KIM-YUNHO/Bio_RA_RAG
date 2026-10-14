"""Chunks from model representation units have a distinct offset contract."""
from copy import deepcopy

from chunk_schema import FIELDS, STR, STRINGS, INT, OPT_STR, obj

BBOX = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 4, 'maxItems': 4}
REF = obj({'path': STR, 'sha256': STR, 'pdf_page': {'type': 'integer', 'minimum': 1}, 'bbox_pdf': BBOX})
REF['properties'].update({'rendered_path':STR,'rendered_sha256':STR,'locator_type':{'type':'string','enum':['html_render_page','verified_cmap_pdf_page']}})
REPRESENTATION_SPAN = obj({'unit_index': INT, 'unit_id': STR,
    'representation_start': INT, 'representation_end': {'type': 'integer', 'minimum': 1},
    'separator_before': {'enum': ['', '\n'], 'type': 'string'},
    'role': {'enum': ['content', 'repeated_context'], 'type': 'string'}})
fields = deepcopy(FIELDS)
fields['schema_version'] = {'const': 'rag-chunk-3', 'type': 'string'}
fields['source_kind'] = {'const': 'preprocessed', 'type': 'string'}
fields['provenance'] = obj({'parsed_path': STR, 'parsed_sha256': STR,
    'parser': STR, 'parser_version': STR,
    'offset_basis': {'const': 'preprocessing_representation_unit', 'type': 'string'},
    'source_spans': {'type': 'array', 'items': REPRESENTATION_SPAN, 'minItems': 1},
    'source_refs': {'type': 'array', 'items': REF, 'minItems': 1},
    'preprocessing_run': STR})
fields['structure']['properties'].update({'figure_id': OPT_STR, 'logical_group_id': OPT_STR,
    'member_unit_ids': STRINGS, 'context_unit_ids': STRINGS,
    'association_status': OPT_STR, 'continuation_status': OPT_STR})
fields['structure']['required'] = list(fields['structure']['properties'])
# Optional for earlier rag-chunk-3 builds; new table bundles carry printed roles.
fields['structure']['properties']['context_roles'] = {'type': 'object', 'additionalProperties':
    {'type': 'string', 'enum': ['caption', 'caption_continuation', 'footnote', 'source', 'unit']}}
fields['structure']['properties']['figure_labels'] = {'type': 'object'}
fields['structure']['properties']['visual_context'] = obj({'version': STR, 'base_header': STR,
    'link_ids': STRINGS, 'indexed_link_ids': STRINGS})
fields['structure']['properties']['related_visual_link_ids'] = STRINGS
fields['structure']['properties']['heading_anchors']['items']['properties']['heading_location']['properties'].update({
    'representation_start':INT, 'representation_end':INT})
fields['representation'] = obj({'unit_ids': STRINGS,
    'origins': {'type': 'object', 'additionalProperties': STR},
    'retrieval_eligible': {'const': True, 'type': 'boolean'},
    'numeric_evidence_eligible': {'const': False, 'type': 'boolean'},
    'semantic_status': {'const': 'unverified', 'type': 'string'},
    'description_review_required': {'type': 'boolean'}})
fields['document_metadata'] = {'type': 'object', 'properties': {
    **{k: OPT_STR for k in ('ingredient', 'product_kind', 'family', 'author', 'tier', 'dataset_version',
        'evaluation_cohort', 'evaluation_family_id', 'exposure_status', 'baseline_status')},
    **{k: {'type': 'boolean'} for k in ('default_search', 'model_execution_allowed')},
}, 'additionalProperties': False}
fields['study_candidates'] = obj({'version': STR, 'status': {'type':'string','const':'unverified'},
    'anchors': {'type':'array','items': obj({'study_key':STR,'basis':{'type':'string','enum':['literal_identifier','section_heading']},
        'unit_id':STR,'document_id':STR,'version_id':STR,'representation_start':INT,
        'representation_end':INT,'quote':STR,'source_refs':{'type':'array','items':REF,'minItems':1}})}})
SCHEMA = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
    'title': 'Preprocessed regulatory retrieval chunk',
    'description': 'Character ranges address selected model representations, never PDF text offsets.', **obj(fields)}
# Historical rag-chunk-3 artifacts remain valid; intake metadata is additive.
SCHEMA['required'].remove('document_metadata')
SCHEMA['required'].remove('study_candidates')
