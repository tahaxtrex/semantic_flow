import logging
from pathlib import Path
from src.models import CourseEvaluation

logger = logging.getLogger(__name__)

class JSONExporter:
    """Exports structured course evaluations to purely validated JSON files on disk."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
    def export(self, evaluation: CourseEvaluation) -> Path:
        """Saves CourseEvaluation to a descriptive JSON file matching ADR-004 logic."""
        # Cleanly derive base_name from the source file
        source = evaluation.course_metadata.source
        if source and source != "Unknown":
            base_name = Path(source).stem
        else:
            base_name = "course_evaluation"
            
        output_path = self.output_dir / f"{base_name}_evaluation.json"
        
        logger.info(f"Exporting verbose Segment JSON payload to: {output_path}")
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                # model_dump_json handles standard python serialization for pydantic models
                f.write(evaluation.model_dump_json(indent=2))
            logger.info("JSON file reliably written.")
        except Exception as e:
            logger.error(f"IO Error writing to disk: {e}")
            raise
            
        return output_path
