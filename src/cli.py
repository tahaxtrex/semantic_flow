import argparse
import json
import logging
import sys
from pathlib import Path
from src.extractor import PDFExtractor
from src.segmenter import StructuralSegmenter
from src.evaluator import PedagogicalEvaluator
from src.models import CourseReport, CourseMetadata

def main():
    parser = argparse.ArgumentParser(description="SemanticFlow Pedagogical Evaluator CLI")
    parser.add_argument("--course", type=str, help="Path to a specific PDF course file")
    parser.add_argument("--all", action="store_true", help="Process all PDFs in data/courses")
    parser.add_argument("--meta", type=str, help="Path to external metadata JSON file")
    args = parser.parse_args()

    # Initialize components
    segmenter = StructuralSegmenter()
    evaluator = PedagogicalEvaluator()
    
    # Load Metadata if provided
    metadata = None
    if args.meta:
        meta_path = Path(args.meta)
        if not meta_path.exists():
            print(f"Error: Metadata file not found: {args.meta}", file=sys.stderr)
            sys.exit(1)
        try:
            with open(meta_path, "r") as f:
                metadata = CourseMetadata.model_validate_json(f.read())
            print(f"[+] Using external metadata: {args.meta}")
        except Exception as e:
            print(f"Error parsing metadata: {e}", file=sys.stderr)
            sys.exit(1)

    courses_dir = Path("data/courses")
    if args.course:
        target_pdfs = [Path(args.course)]
    elif args.all:
        target_pdfs = list(courses_dir.glob("*.pdf"))
    else:
        print("Please provide --course <path> or use --all.")
        return

    for pdf_path in target_pdfs:
        print(f"[*] Processing: {pdf_path.name}")
        
        # 1. Extraction
        extractor = PDFExtractor(str(pdf_path))
        pages = extractor.extract_full_text()
        bookmarks = extractor.get_bookmarks()
        
        # 2. Use embedded metadata if no explicit metadata provided
        current_metadata = metadata
        if not current_metadata:
            # Attempt to get embedded metadata
            try:
                pdf_meta = extractor.get_metadata()
                current_metadata = CourseMetadata(
                    title=pdf_meta.get("Title"),
                    author=pdf_meta.get("Author"),
                    description=pdf_meta.get("Subject"),
                    source="embedded"
                )
                print("    - Using embedded PDF metadata.")
            except Exception:
                current_metadata = None
        
        # 3. Segmentation
        segments = segmenter.segment_course(pages, bookmarks)
        print(f"    - Identified {len(segments)} segments.")
        
        # 4. Evaluation
        segment_evals = {}
        total_scores = {rubric: 0.0 for rubric in evaluator.rubrics.keys()}
        
        for i, segment in enumerate(segments):
            print(f"    - Evaluating segment {i+1}/{len(segments)}: {segment['title']}...")
            try:
                result = evaluator.evaluate_segment(segment['text'], segment['title'], current_metadata)
                segment_evals[i] = result
                
                # Update totals for averaging
                for rubric in total_scores.keys():
                    total_scores[rubric] += getattr(result.scores, rubric)
                
                # Save individual segment evaluation
                eval_path = Path(f"data/evaluations/{pdf_path.stem}_seg_{i}.json")
                with open(eval_path, "w") as f:
                    f.write(result.model_dump_json(indent=2))
                    
            except Exception as e:
                print(f"      [!] Error evaluating segment {i}: {e}")

        # 4. Aggregation
        if segment_evals:
            avg_scores = {r: s / len(segment_evals) for r, s in total_scores.items()}
            report = CourseReport(
                course_name=pdf_path.stem,
                average_scores=avg_scores,
                segment_evaluations=segment_evals
            )
            
            # Save Final Report
            report_path = Path(f"data/reports/{pdf_path.stem}_report.json")
            with open(report_path, "w") as f:
                f.write(report.model_dump_json(indent=2))
            
            print(f"[+] Report generated: {report_path}")
            print(f"    - Average Score: {sum(avg_scores.values())/len(avg_scores):.2f}/10")

if __name__ == "__main__":
    main()
