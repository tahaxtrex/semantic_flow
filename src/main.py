import argparse
import logging
import sys
from pathlib import Path
from dotenv import load_dotenv


def setup_logging(output_dir: Path) -> logging.Logger:
    """Configure logging to output to both console and a file in the output directory."""
    evaluate_log = output_dir / "evaluate.log"

    logger = logging.getLogger("SemanticFlowEvaluator")
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(evaluate_log)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    if logger.hasHandlers():
        logger.handlers.clear()

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="SemanticFlow Pedagogical Evaluator")
    parser.add_argument("--input", type=str, required=True, help="Path to input directory containing courses (PDFs)")
    parser.add_argument("--output", type=str, required=True, help="Path to output directory for JSON evaluations")
    parser.add_argument("--config", type=str, default="config/rubrics.yaml", help="Path to rubrics YAML configuration")
    parser.add_argument("--limit", type=int, default=0, help="Limit the number of segments to evaluate per PDF for quick testing (0 = no limit)")
    parser.add_argument(
        '--model',
        type=str,
        choices=['claude', 'gemini'],
        default='claude',
        help='LLM model to use for evaluation (default: claude)'
    )
    parser.add_argument(
        '--metadata',
        type=str,
        default=None,
        help='Optional path or URL to metadata file (JSON, TXT, HTML, or PDF).'
    )
    parser.add_argument(
        '--ai',
        action='store_true',
        default=False,
        help='Use AI (Claude → Gemini fallback) to fill missing metadata fields.'
    )

    args = parser.parse_args()

    output_dir = Path(args.output)
    config_path = Path(args.config)
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: Input path {input_path} does not exist.")
        sys.exit(1)

    if not config_path.exists():
        print(f"Error: Config file {config_path} does not exist.")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(output_dir)
    logger.info("Starting SemanticFlow Pedagogical Evaluator")
    logger.info(f"Input: {input_path}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Config path: {config_path}")

    # Accept --input as either a directory or a single .pdf file
    if input_path.is_file() and input_path.suffix.lower() == ".pdf":
        pdfs = [input_path]
    else:
        pdfs = sorted(input_path.glob("*.pdf"))
        if not pdfs:
            logger.warning(f"No PDFs found in {input_path}")
            sys.exit(0)

    logger.info(f"Found {len(pdfs)} PDF(s) to process.")

    from src.metadata import MetadataIngestor
    from src.segmenter import SmartSegmenter
    from src.evaluator import LLMEvaluator
    from src.aggregator import ScoreAggregator
    from src.exporter import JSONExporter

    try:
        evaluator = LLMEvaluator(config_path, preferred_model=args.model)
    except Exception as e:
        logger.error(f"Failed to initialize evaluator: {e}")
        sys.exit(1)

    aggregator = ScoreAggregator()
    exporter = JSONExporter(output_dir)
    model_string = "Claude 4.6 Sonnet" if args.model == 'claude' else "Gemini-2.5-Flash"

    for pdf_path in pdfs:
        try:
            logger.info(f"--- Processing: {pdf_path.name} ---")

            # Step 1: Metadata
            logger.info("Step 1/5: Metadata Extraction")
            metadata_ingestor = MetadataIngestor(
                course_pdf_path=pdf_path,
                metadata_source=args.metadata,
                use_ai=args.ai,
                preferred_model=args.model,
            )
            metadata = metadata_ingestor.ingest()

            # Step 2: Segmentation (ADR-039: pass metadata so segmenter can
            # validate tier chapter counts against the parsed TOC)
            logger.info("Step 2/5: Deterministic Segmentation")
            segmenter = SmartSegmenter(pdf_path, course_metadata=metadata)
            segments = segmenter.segment()

            if args.limit > 0:
                logger.info(f"Limiting evaluation to first {args.limit} segments for testing.")
                segments = segments[:args.limit]

            # Step 3: Module Gate — batch evaluation (6 rubrics + per-segment summaries)
            logger.info(f"Step 3/5: Module Gate Evaluation ({len(segments)} segments)")
            evaluated_segments = []

            BATCH_SIZE = 5
            total_batches = (len(segments) + BATCH_SIZE - 1) // BATCH_SIZE
            previous_summaries = []
            for i in range(0, len(segments), BATCH_SIZE):
                batch = segments[i:i + BATCH_SIZE]
                batch_num = i // BATCH_SIZE + 1
                logger.info(f"  [Module Gate] Batch {batch_num}/{total_batches} ({len(batch)} segments)")
                eval_batch = evaluator.evaluate_batch(metadata, batch, previous_summaries=previous_summaries)
                evaluated_segments.extend(eval_batch)
                previous_summaries.extend(
                    seg.summary for seg in eval_batch if getattr(seg, "summary", None)
                )

            # Step 4: Course Gate — single capstone call (holistic rubrics)
            # Skip if no instructional segments exist: scoring from metadata alone is misleading.
            has_instructional = any(s.segment_type == "instructional" for s in segments)
            non_instructional_raw = [s for s in segments if s.segment_type != "instructional"]
            if not has_instructional:
                logger.warning(
                    "Step 4/5: Course Gate skipped — no instructional segments found. "
                    "Course Gate requires module content summaries to give meaningful scores."
                )
                course_assessment = evaluator._make_incomplete_course_assessment()
                is_partial_course = False
            else:
                logger.info("Step 4/5: Course Gate Evaluation (capstone)")
                course_assessment, is_partial_course = evaluator.evaluate_course(
                    metadata=metadata,
                    evaluated_segments=evaluated_segments,
                    non_instructional_segments=non_instructional_raw,
                )
                if is_partial_course:
                    logger.info(
                        "[Course Gate] File identified as a partial course fragment — "
                        "scoring adjusted to avoid penalising absent modules."
                    )

            # Step 5: Aggregate & Export
            logger.info("Step 5/5: Aggregation and Export")
            course_eval = aggregator.aggregate(
                metadata=metadata,
                segments=evaluated_segments,
                course_assessment=course_assessment,
                model_used=model_string,
            )
            output_file = exporter.export(course_eval)

            logger.info(f"Successfully evaluated {pdf_path.name} -> {output_file.name}")

        except Exception as e:
            logger.error(f"Failed processing {pdf_path.name}: {e}")
            logger.exception(e)

    logger.info("Evaluation pipeline completely finished.")


if __name__ == "__main__":
    main()
