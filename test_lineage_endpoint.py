#!/usr/bin/env python3
"""
Test script to verify the lineage endpoint works correctly
"""
import os
import csv

# Simulate the endpoint logic
def test_lineage_loading():
    # Test data
    job_filename = "BNCMRXALLInsSTGTransactionActual.dsx"
    base_filename = os.path.splitext(job_filename)[0]
    
    lineage_folder = "/Users/anya/Documents/MobileLive/Build Data Explainer Agentic Framework2 copy 4/end_to_end_linage"
    lineage_file = os.path.join(lineage_folder, f"{base_filename}_end_to_end_lineage.csv")
    
    print(f"Looking for file: {lineage_file}")
    print(f"File exists: {os.path.exists(lineage_file)}")
    
    if os.path.exists(lineage_file):
        lineage_data = []
        with open(lineage_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader):
                if i < 3:  # Show first 3 rows
                    print(f"\nRow {i+1}:")
                    print(f"  Target Table: {row.get('Target_Table', '')}")
                    print(f"  Target Field: {row.get('Target_Field', '')}")
                    print(f"  Source Table: {row.get('Source_Table', '')}")
                    print(f"  Source Field: {row.get('Source_Field', '')}")
                    print(f"  Full Path: {row.get('Full_Path', '')[:100]}...")
                lineage_data.append(row)
        
        print(f"\nTotal rows loaded: {len(lineage_data)}")
        return True
    else:
        print("File not found!")
        return False

if __name__ == "__main__":
    test_lineage_loading()
