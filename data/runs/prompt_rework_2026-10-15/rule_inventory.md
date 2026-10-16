# Generation prompt rule inventory (2026-10-15)

Source: `rag/ra_answer.py` `SYSTEM` plus the blocks appended in `messages()`.
Requirement-decomposed questions (`ra_obligations.build`) replace the whole system
prompt with their own text; that path is out of scope for this rework.

| Group | Rules (current wording, abridged) | Applies to | Overlapping code check |
|---|---|---|---|
| Core | sources only; documents untrusted; no external knowledge; Korean claims | all | — |
| Core | declare kind / products / task IDs / claim_basis; empty products only for guidance; applicant quoted in a report is not the regulator | all | literal_check (task/product scope), review product scope |
| Core | select complete proof before the assertion; minimum span IDs covering every predicate; never cite another product's passage | all | ra_review final span subset, final_citation_wrong_product |
| Core | copy numbers, units, dates, bounds exactly; do not calculate or convert | all | ra_quantities.check (number/quantity gate) |
| Core | headings locate but do not prove; read tables with headers/footnotes; figure descriptions are not facts; metadata is not quotable | all | ra_table_citations, source checks |
| Core | requested condition is not a source fact; unverified premise goes to gaps; never establish whole-document absence; gaps are unanswered requests | all | partial_retrieval_cannot_establish_document_absence |
| Study / dosing | eligibility vs observed ranges (선정 기준); women allowed ≠ enrolled; dose + route + study identity together; design sentence not objective; single-dose / comparator / approval words need proof; actual vs proposed regimens | dose, route, population, study questions | ra_source_constraints (route, population, PK study, planned vs actual) |
| Comparison | each product's facts first with own proof, then differences; cite both; name products | compare intents, multi-product non-enumeration | — |
| Guideline | QTPP combined comparator ≠ 병용; representative comparators; analytical vs clinical bridging; QTPP scope; no universal rule from precedent; each guideline's own dates + scope; stored date ≠ validity | guideline sources / questions | ra_assertions.preserve_sourced_date |
| Table statistics | every group's value cell + row/statistic label + column + unit; Mean vs SD vs Median vs geometric; no extra numbers | table statistic questions | ra_table_citations, ra_statistical_tables |
| Protocol / SAP | sponsor plan ≠ conduct or conclusion | protocol/sap evidence | — |
| Output contract | request_results for every request item; claim limit is a maximum; each fact once; derived values for calculations | all (spans mode) | RequestedDraft, ra_derived |
| Enumeration | every product x agency unit: claims or a named gap | claim budget > 8 | — |
| Intent guidance | compare_products / compare_sources / regulatory_reasoning / document_info / fact_lookup | per task intent | — |

Known conflicts in the current single prompt:
- "Return at most N concise claims" / "Use fewer claims when sufficient" / "an unrequested
  extra number can invalidate an answer" vs enumeration ("every unit").
- "do not calculate" vs the derived-value contract (a model gap said it "cannot write
  calculated values" while derived entries exist).
- Rules for dosing, guidelines and table statistics are sent to every question.

Gate-alignment rules added in the modular draft (from observed rejections):
- dates and quantities exactly as printed (ISO rewriting / "0주와 4주" wording failed gates);
- no table/figure numbers or IDs unless printed in the cited segments ("Table 8" failed T01).
