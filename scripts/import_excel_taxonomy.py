import pandas as pd
import json
import os

def safe_str(val):
    if pd.isna(val):
        return ""
    return str(val).strip()

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(base_dir, "720.xlsx")
    out_path = os.path.join(base_dir, "data", "retention_taxonomy.json")

    print(f"Reading {file_path}...")
    try:
        df = pd.read_excel(file_path, sheet_name="2. Schedule", header=1)
    except Exception as e:
        print(f"Failed to read excel file: {e}")
        return

    records = []
    
    for _, row in df.iterrows():
        agency_item = safe_str(row.get('3. Agency \nItem No.'))
        record_item = safe_str(row.get('4. Record\nSeries\nItem No.'))
        title = safe_str(row.get('5. Record Series Title'))
        
        # Skip empty rows
        if not title:
            continue
            
        record_id = record_item if record_item else agency_item
        if not record_id:
            record_id = f"SYNTH-{len(records)}"

        desc_base = safe_str(row.get('6. Description'))
        ac_def = safe_str(row.get('9. AC Definition'))
        remarks = safe_str(row.get('11. Remarks'))
        
        full_desc = desc_base
        if ac_def:
            full_desc += f" [Trigger Condition: {ac_def}]"
        if remarks:
            full_desc += f" [Remarks: {remarks}]"

        ret_code = safe_str(row.get('7. Ret. Code')).replace('', '').strip()
        years = row.get('8. Ret. Period Years')
        months = row.get('8. Ret. Period Months')
        
        rule = ret_code
        if pd.notna(years) and float(years) > 0:
            if not ret_code:
                print(f"Skipping row due to missing Retention Code: {title}")
                continue
            rule = f"{ret_code}-{int(float(years))}"
        elif pd.notna(months) and float(months) > 0:
            rule = f"{int(float(months))} MONTHS"
            
        if not rule:
            rule = "AV" # Default fallback
            
        keywords = []
        for word in title.replace("-", " ").split():
            clean_word = "".join(c for c in word if c.isalnum()).lower()
            if len(clean_word) > 3:
                keywords.append(clean_word)

        record = {
            "id": record_id,
            "category": "Texas State Records Retention Schedule",
            "section": "Agency 720",
            "document_type": title,
            "retention_label": rule,
            "retention_rule": rule,
            "classification": "Controlled",
            "team_owner": "State Agency",
            "expected_location": "SharePoint",
            "description": full_desc,
            "keywords": keywords,
            "policy_version": "2026-04"
        }
        records.append(record)
        
    print(f"Parsed {len(records)} records. Saving to {out_path}...")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)
    print("Done!")

if __name__ == "__main__":
    main()
