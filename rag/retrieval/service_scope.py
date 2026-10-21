"""The regulatory sources available in the interactive service."""
# ClinicalTrials.gov protocol/SAP (jurisdiction 'sponsor') are the sponsor's plans. They are not default
# search documents: retrieval reaches them only for a protocol/sap role or the non-default option.
SOURCES = frozenset(('EMA', 'FDA', 'Health Canada', 'ClinicalTrials.gov'))
JURISDICTIONS = frozenset(('EU', 'US', 'CA', 'sponsor'))
SEARCH_POLICY_VERSION = 'ra-document-defaults-1'


def default_search(chunk):
    """Public product information is primary evidence in the allowed RA scope.

    Cohort and representation eligibility remain separate filter_idx guards.
    """
    meta = chunk.get('document_metadata') or {}
    if 'default_search' in meta and type(meta['default_search']) is not bool:
        return False
    if allowed(chunk) and meta.get('family') == 'product_information':
        return True
    return meta.get('default_search') is not False


def allowed(chunk):
    return chunk.get('source') in SOURCES and chunk.get('jurisdiction') in JURISDICTIONS
