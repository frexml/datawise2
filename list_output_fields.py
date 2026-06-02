import pandas as pd
import requests
import sys
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

def list_output_fields(job_id, output_file=None):
    """List all fields from final output stages"""
    
    if not output_file:
        output_file = f"job_{job_id}_output_fields.xlsx"
    
    base_url = "http://localhost:8000/api"
    
    print(f"Fetching job {job_id}...")
    response = requests.get(f"{base_url}/jobs/{job_id}/lineage")
    if response.status_code != 200:
        print(f"Error: Could not fetch lineage data")
        return
    
    lineage_data = response.json()
    
    if not lineage_data:
        print("No lineage data found")
        return
    
    # Extract unique target stage and field combinations
    output_fields = []
    seen = set()
    
    for record in lineage_data:
        target_stage = record.get('target_table', '')
        target_field = record.get('target_field', '')
        
        key = (target_stage, target_field)
        if key not in seen and target_stage and target_field:
            seen.add(key)
            output_fields.append({
                'Target_Stage': target_stage,
                'Target_Field': target_field
            })
    
    print(f"Found {len(output_fields)} output fields")
    
    # Create DataFrame
    df = pd.DataFrame(output_fields)
    df = df.sort_values(['Target_Stage', 'Target_Field'])
    
    # Write to Excel
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Output Fields', index=False)
        
        # Summary
        summary = pd.DataFrame([{
            'Total Output Stages': df['Target_Stage'].nunique(),
            'Total Output Fields': len(df)
        }])
        summary.to_excel(writer, sheet_name='Summary', index=False)
    
    # Format Excel
    wb = load_workbook(output_file)
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        
        # Header formatting
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        
        # Auto-adjust column widths
        for column in ws.columns:
            max_length = 0
            column_letter = get_column_letter(column[0].column)
            for cell in column:
                if cell.value:
                    max_length = max(max_length, len(str(cell.value)))
            ws.column_dimensions[column_letter].width = min(max_length + 2, 60)
    
    wb.save(output_file)
    print(f"✓ Export complete: {output_file}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python list_output_fields.py <job_id> [output_file.xlsx]")
        sys.exit(1)
    
    job_id = int(sys.argv[1])
    output_file = sys.argv[2] if len(sys.argv) > 2 else None
    
    list_output_fields(job_id, output_file)
