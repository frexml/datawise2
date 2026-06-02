import os

job_filename = "BNCMRXALLInsSTGTransactionActual.dsx"
base_filename = os.path.splitext(job_filename)[0]
lineage_folder = "/Users/anya/Documents/MobileLive/Build Data Explainer Agentic Framework2 copy 4/end_to_end_linage"
lineage_file = os.path.join(lineage_folder, f"{base_filename}_end_to_end_lineage.csv")

print(f"Job filename: {job_filename}")
print(f"Base filename: {base_filename}")
print(f"Looking for: {lineage_file}")
print(f"File exists: {os.path.exists(lineage_file)}")
print(f"\nFiles in folder:")
for f in os.listdir(lineage_folder):
    print(f"  - {f}")
