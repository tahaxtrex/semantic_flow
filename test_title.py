from pathlib import Path
import pdfplumber

def test_font_size(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages[:3]):
            print(f"--- Page {i} ---")
            try:
                words = page.extract_words(extra_attrs=["size"])
                # Get the top 10 largest words
                largest_words = sorted(words, key=lambda w: float(w.get("size", 0)), reverse=True)[:15]
                for w in largest_words:
                    print(f"Size: {w.get('size', 0):.2f}, Text: {w['text']}")
            except Exception as e:
                print(f"Error: {e}")

test_font_size('data/courses/firstpart.pdf')
