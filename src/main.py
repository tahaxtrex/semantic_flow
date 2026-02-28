import argparse
import logging
import sys
from pathlib import Path
from dotenv import load_dotenv

def setup_logging(output_dir: Path) -> logging.Logger:
    """Configure logging to output to both console and a file in the output directory."""
    evaluate_log = output_dir / "evaluate.log"
    
    # Create logger
    logger = logging.getLogger("SemanticFlowEvaluator")
    logger.setLevel(logging.INFO)
    
    # Create formatters
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    
    # File handler
    file_handler = logging.FileHandler(evaluate_log)
    file_handler.setFormatter(formatter)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    
    # Add handlers (clear existing to avoid duplicates if called multiple times)
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
        help='Optional path or URL to metadata file (JSON, TXT, HTML, or PDF). Only extracted if provided.'
    )
    parser.add_argument(
        '--ai',
        action='store_true',
        default=False,
        help='Use AI (Claude → Gemini fallback) to fill missing metadata fields.'
    )
    
    args = parser.parse_args()
    
    input_dir = Path(args.input)
    output_dir = Path(args.output)
    config_path = Path(args.config)
    
    # Ensure input directory exists
    if not input_dir.exists():
        print(f"Error: Input directory {input_dir} does not exist.")
        sys.exit(1)
        
    # Ensure config file exists
    if not config_path.exists():
        print(f"Error: Config file {config_path} does not exist.")
        sys.exit(1)
        
    # Create output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Setup logging
    logger = setup_logging(output_dir)
    logger.info("Starting SemanticFlow Pedagogical Evaluator")
    logger.info(f"Input directory: {input_dir}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Config path: {config_path}")
    
    # Check for PDFs
    pdfs = list(input_dir.glob("*.pdf"))
    if not pdfs:
        logger.warning(f"No PDFs found in {input_dir}")
        sys.exit(0)
        
    logger.info(f"Found {len(pdfs)} PDF(s) to process.")
    
    # Initialize shared components
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
    
    for pdf_path in pdfs:
        try:
            logger.info(f"--- Processing: {pdf_path.name} ---")
            
            # 1. Ingest Metadata
            logger.info("Step 1/4: Metadata Extraction")
            metadata_ingestor = MetadataIngestor(course_pdf_path=pdf_path, metadata_source=args.metadata)
            metadata = metadata_ingestor.ingest()
            
            # 2. Segment PDF
            logger.info("Step 2/4: Deterministic Segmentation")
            segmenter = SmartSegmenter(pdf_path)
            segments = segmenter.segment()
            
            if args.limit > 0:
                logger.info(f"Limiting evaluation to first {args.limit} segments for testing.")
                segments = segments[:args.limit]
            
            # 3. Evaluate Segments
            logger.info(f"Step 3/4: LLM Evaluation ({len(segments)} segments)")
            evaluated_segments = []
            
            BATCH_SIZE = 5
            total_batches = (len(segments) + BATCH_SIZE - 1) // BATCH_SIZE
            for i in range(0, len(segments), BATCH_SIZE):
                batch = segments[i:i+BATCH_SIZE]
                batch_num = i // BATCH_SIZE + 1
                logger.info(f"  Evaluating batch {batch_num}/{total_batches} ({len(batch)} segments)")
                eval_batch = evaluator.evaluate_batch(metadata, batch)
                evaluated_segments.extend(eval_batch)
                
            # 4. Aggregate & Export
            logger.info("Step 4/4: Aggregation and Export")
            model_string = "Claude-3-5-Sonnet" if args.model == 'claude' else "Gemini-2.5-Flash"
            course_eval = aggregator.aggregate(metadata, evaluated_segments, model_used=model_string)
            output_file = exporter.export(course_eval)
            
            logger.info(f"Successfully evaluated {pdf_path.name} -> {output_file.name}")
            
        except Exception as e:
            logger.error(f"Failed processing {pdf_path.name}: {e}")
            logger.exception(e)
            
    logger.info("Evaluation pipeline completely finished.")

if __name__ == "__main__":
    main()
