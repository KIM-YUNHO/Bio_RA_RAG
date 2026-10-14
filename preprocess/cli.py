import argparse
import json
import subprocess
from pathlib import Path

from .ingest import Document, from_catalog, from_inputs
from .inventory import run_inventory
from .validate import validate_run


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local RA document inventory and Paddle/Qwen preprocessing")
    commands = parser.add_subparsers(dest="command", required=True)
    workflow = commands.add_parser('workflow', help='All PDF pages -> local Paddle/Qwen -> chunks -> local search index and report')
    workflow_source = workflow.add_mutually_exclusive_group()
    workflow_source.add_argument('--input', type=Path, nargs='+')
    workflow_source.add_argument('--catalog', type=Path)
    workflow.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    workflow.add_argument('--document', action='append')
    workflow.add_argument('--out', type=Path, required=True)
    workflow.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1]/'configs/preprocess.local.json')
    workflow.add_argument('--resume', action='store_true')
    workflow.add_argument('--seed-run', type=Path, help='Optional validated extraction supplying matching Paddle caches')
    workflow.add_argument('--no-qwen', action='store_true')
    workflow.add_argument('--max-qwen-calls', type=int)
    workflow.add_argument('--no-label-ocr', action='store_true')
    workflow.add_argument('--max-ocr-calls', type=int)
    workflow.add_argument('--no-embedding', action='store_true')
    workflow.add_argument('--embedding-device', choices=('cuda','cpu'), default='cuda')
    workflow.add_argument('--query', action='append', default=[], help='Optional query; repeat to save hybrid search examples')
    workflow.add_argument('--include-nondefault', action='store_true')
    inventory = commands.add_parser("inventory", help="Inspect all pages and record candidates")
    source = inventory.add_mutually_exclusive_group(required=True)
    source.add_argument("--catalog", type=Path)
    source.add_argument("--input", type=Path, nargs="+")
    inventory.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="Resolve catalog paths against this root")
    inventory.add_argument("--document", action="append", help="Optional catalog document ID; repeat for a subset")
    inventory.add_argument("--out", type=Path, required=True)
    inventory.add_argument("--workers", type=int, default=2)
    inventory.add_argument("--table-strategies", nargs="+", choices=("lines", "text"), default=["lines", "text"])
    inventory.add_argument("--offline", action="store_true", help="Inventory always runs offline; accepted for explicit deployment configuration")
    resume = commands.add_parser("resume", help="Continue an inventory using source-bound checkpoints")
    resume.add_argument("--run", type=Path, required=True)
    resume.add_argument("--workers", type=int)
    validate = commands.add_parser("validate", help="Check stored contracts, coverage, and current source hashes")
    validate.add_argument("--run", type=Path, required=True)
    process = commands.add_parser("process", help="PDF upload -> PaddleOCR-VL-1.6 -> selective local Qwen")
    process_source = process.add_mutually_exclusive_group(required=True)
    process_source.add_argument("--input", type=Path, nargs="+")
    process_source.add_argument("--catalog", type=Path)
    process.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    process.add_argument("--document", action="append")
    process.add_argument("--out", type=Path, required=True)
    process.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "configs/preprocess.local.json")
    process.add_argument("--pages", help="Optional physical pages, e.g. 35,50-52; default: every page")
    process.add_argument("--no-qwen", action="store_true", help="Extract and queue enrichment without Qwen generation")
    process.add_argument("--max-qwen-calls", type=int, help="Optional per-run generation limit; cached replies do not consume it")
    enrich = commands.add_parser("enrich", help="Resume only local Qwen enrichment, retaining Paddle results")
    enrich.add_argument("--run", type=Path, required=True)
    enrich.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[1] / "configs/preprocess.local.json")
    enrich.add_argument("--max-qwen-calls", type=int)
    enrich.add_argument("--only-pending", action="store_true", help="Preserve completed candidates and retry only pending/failed work")
    status = commands.add_parser('status', help='Read upload progress and document review outcomes; no model calls')
    status.add_argument('--run', type=Path, required=True)
    status.add_argument('--format', choices=('json', 'text'), default='json')
    status.add_argument('--write', action='store_true', help='Save processing_report.json and .md')
    status.add_argument('--out', type=Path, help='Optional report export directory; requires --write')
    chunk = commands.add_parser('chunk', help='Connect eligible PDF representations to the existing chunker; local tokenizer only')
    grid = commands.add_parser('source-grid', help='Recover source-fixed table cells in a separate snapshot without OCR/VLM calls')
    grid.add_argument('--run', type=Path, required=True)
    grid.add_argument('--out', type=Path, required=True)
    grid.add_argument('--asset-id', action='append')
    chunk.add_argument('--run', type=Path, required=True)
    chunk.add_argument('--out', type=Path, required=True, help='Separate candidate directory')
    chunk.add_argument('--tokenizer-dir', type=Path, help='Optional deployed BGE-M3 tokenizer directory')
    chunk.add_argument('--visual-context-mode', choices=('none', 'headings', 'linked'), default='linked')
    regroup = commands.add_parser('regroup', help='Rebuild table/visual context in a separate run; no OCR/VLM calls')
    regroup.add_argument('--run', type=Path, required=True)
    regroup.add_argument('--out', type=Path, required=True)
    regroup.add_argument('--metadata-catalog', type=Path, help='Refresh descriptive metadata by exact source version, preserving intake policies')
    context = commands.add_parser('visual-context', help='Link captions, headings and explicit source references; no model calls')
    context.add_argument('--run', type=Path, required=True)
    context.add_argument('--out', type=Path, required=True, help='New separate sidecar directory')
    labels = commands.add_parser('figure-labels', help='Selective 300-dpi local Paddle label OCR and literal field projection; separate output')
    labels.add_argument('--run', type=Path, required=True)
    labels.add_argument('--out', type=Path, required=True)
    labels.add_argument('--config', type=Path, default=Path(__file__).resolve().parents[1] / 'configs/preprocess.local.json')
    labels.add_argument('--max-ocr-calls', type=int)
    labels.add_argument('--targeted', action='store_true', help='Read missing axis/legend bands; rotate vertical axis text')
    labels.add_argument('--tight', action='store_true', help='Use narrow label bands after broad OCR misses labels; requires --targeted')
    args = parser.parse_args(argv)
    if args.command == 'source-grid':
        from .grid_refinement import refine
        print(json.dumps(refine(args.run,args.out,args.asset_id),ensure_ascii=False,indent=2))
        return 0
    if args.command == 'workflow':
        from .workflow import run
        from .hybrid_config import load_config
        if args.document and not args.catalog and not args.resume:parser.error('--document requires --catalog')
        try:
            result=run(args.out,load_config(args.config),inputs=args.input,catalog=args.catalog,
                document_ids=args.document,root=args.root,resume=args.resume,seed_run=args.seed_run,
                use_qwen=not args.no_qwen,max_qwen_calls=args.max_qwen_calls,
                label_ocr=not args.no_label_ocr,max_ocr_calls=args.max_ocr_calls,
                embedding=not args.no_embedding,device=args.embedding_device,
                queries=args.query,include_nondefault=args.include_nondefault)
            print(json.dumps({k:result[k] for k in ('execution_complete','content_state','documents','chunks','candidate_directory')},ensure_ascii=False,indent=2))
            return 0
        except (ValueError,OSError,RuntimeError,KeyError,subprocess.SubprocessError) as exc:
            parser.exit(1,f'PDF workflow failed: {exc}\n')
    if args.command == 'visual-context':
        from .visual_context import write
        try:
            print(json.dumps(write(args.run, args.out), ensure_ascii=False, indent=2))
            return 0
        except (ValueError, OSError, KeyError) as exc:
            parser.exit(1, f'Visual context linking failed: {exc}\n')
    if args.command == 'figure-labels':
        from .figure_ocr import complete
        from .hybrid_config import load_config
        if args.tight and not args.targeted:
            parser.error('--tight requires --targeted')
        result = complete(args.run, args.out, load_config(args.config), args.max_ocr_calls, targeted=args.targeted, tight=args.tight)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result['failed'] else 0
    if args.command == 'regroup':
        from .regroup import regroup
        try:
            print(json.dumps(regroup(args.run, args.out, args.metadata_catalog), ensure_ascii=False, indent=2))
            return 0
        except (ValueError, OSError, KeyError) as exc:
            parser.exit(1, f'Table context regroup failed: {exc}\n')
    if args.command == 'chunk':
        from rag.preprocessed_chunks import CONFIG, write
        try:
            cfg = {**CONFIG, 'visual_context_mode': args.visual_context_mode}
            if args.tokenizer_dir:
                cfg['token_counter'] = str(args.tokenizer_dir.resolve())
            summary = write(args.run, args.out, cfg=cfg)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        except (ValueError, OSError, RuntimeError, KeyError) as exc:
            parser.exit(1, f'Preprocessed chunk build failed: {exc}\n')
    if args.command == 'status':
        from .reporting import build_report, markdown, save_report
        if args.out and not args.write:
            parser.error('--out requires --write')
        try:
            report = build_report(args.run)
            if args.write:
                save_report(report, args.out or args.run)
            print(markdown(report) if args.format == 'text' else json.dumps(report, ensure_ascii=False, indent=2))
            return 1 if report['state'] in {'failed', 'partial_failure'} else 0
        except (ValueError, OSError, KeyError, TypeError) as exc:
            parser.exit(1, f'Cannot read preprocessing status: {exc}\n')
    if args.command in ("process", "enrich"):
        from .hybrid_config import load_config, parse_pages
        from .hybrid import run_hybrid, resume_enrichment
        try:
            config = load_config(args.config)
            if args.max_qwen_calls is not None and args.max_qwen_calls < 0:
                parser.error("--max-qwen-calls must be nonnegative")
            if args.command == "enrich":
                summary = resume_enrichment(args.run, config, args.max_qwen_calls, only_pending=args.only_pending)
            else:
                if args.document and not args.catalog:
                    parser.error("--document requires --catalog")
                documents = from_catalog(args.catalog, args.root, args.document) if args.catalog else from_inputs(args.input, args.out)
                summary = run_hybrid(documents, args.out, config, parse_pages(args.pages),
                                     not args.no_qwen, args.max_qwen_calls)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 1 if summary.get("document_errors") or any(k != "parsed" for k in summary["page_statuses"]) or summary["qwen"]["failed"] or summary["qwen"].get("model_error") else 0
        except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
            parser.exit(1, f"Preprocessing failed: {exc}\n")
    if args.command == "validate":
        if (args.run / "extraction_manifest.json").exists():
            from .hybrid_validate import validate_extraction
            result = validate_extraction(args.run)
        else:
            result = validate_run(args.run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "resume":
        manifest = json.loads((args.run / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get('composition'):
            parser.error('Combined inventory is a frozen snapshot; resume a source component in a separate run')
        documents = [Document(**row) for row in manifest["documents"]]
        out, workers = args.run, manifest["workers"] if args.workers is None else args.workers
        strategies = manifest["configuration"]["table_strategies"]
    else:
        if args.document and not args.catalog:
            parser.error("--document requires --catalog")
        documents = from_catalog(args.catalog, args.root, args.document) if args.catalog else from_inputs(args.input, args.out)
        out, workers, strategies = args.out, args.workers, args.table_strategies
    if not 1 <= workers <= 8:
        parser.error("--workers must be between 1 and 8")
    summary = run_inventory(documents, out, strategies, workers)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed_pages"] or any(k != "heuristic_inventory_complete" for k in summary["document_statuses"]) else 0
