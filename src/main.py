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
        evaluator = LLMEvaluator(config_path)
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
            metadata_ingestor = MetadataIngestor(pdf_path)
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
            for i, seg in enumerate(segments, 1):
                logger.info(f"  Evaluating segment {i}/{len(segments)}")
                eval_seg = evaluator.evaluate(metadata, seg)
                evaluated_segments.append(eval_seg)
                
            # 4. Aggregate & Export
            logger.info("Step 4/4: Aggregation and Export")
            course_eval = aggregator.aggregate(metadata, evaluated_segments)
            output_file = exporter.export(course_eval)
            
            logger.info(f"Successfully evaluated {pdf_path.name} -> {output_file.name}")
            
        except Exception as e:
            logger.error(f"Failed processing {pdf_path.name}: {e}")
            logger.exception(e)
            
    logger.info("Evaluation pipeline completely finished.")

if __name__ == "__main__":
    main()
