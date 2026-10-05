import pdfplumber
import json
import re

pdf_path = r"C:\Users\CX-DATA_LABS\.gemini\antigravity-ide\brain\6ba153a5-0300-4cc8-8f4b-64a10601ad33\.user_uploaded\media_1790236041992.pdf"

records = []
current_category = "General"
current_section = "General"

def clean_text(text):
    if not text:
        return ""
    return text.replace('\n', ' ').strip()

with pdfplumber.open(pdf_path) as pdf:
    for page in pdf.pages:
        # Extract tables
        tables = page.extract_tables()
        for table in tables:
            for row in table:
                if not row or not any(row):
                    continue
                
                # Check for category header (e.g. "Category 1 - ALL TEAMS")
                row_text = clean_text(row[0] if row[0] else "")
                if "Category" in row_text and "-" in row_text:
                    current_category = row_text.split("-", 1)[1].strip()
                    continue
                
                if "Section" in row_text and "-" in row_text:
                    current_section = row_text.split("-", 1)[1].strip()
                    continue
                
                # Header row
                if row[0] == "Item #":
                    continue
                
                # Normal row should have multiple columns
                if len(row) >= 5 and re.match(r'^\d+\.\d+\.\d+$', clean_text(row[0])):
                    item_id = clean_text(row[0])
                    doc_cat = clean_text(row[1])
                    # Location is row 2
                    retention = clean_text(row[3])
                    classification = clean_text(row[4])
                    owner = clean_text(row[5]) if len(row) > 5 else ""
                    
                    records.append({
                        "id": item_id,
                        "category": current_category,
                        "section": current_section,
                        "document_type": doc_cat,
                        "retention_label": classification,
                        "retention_rule": retention,
                        "team_owner": owner,
                        "description": doc_cat
                    })

print(f"Extracted {len(records)} records from PDF.")
with open("data/retention_taxonomy.json", "w") as f:
    json.dump(records, f, indent=2)

print("Saved to data/retention_taxonomy.json")
