from dsxlineage.db.database import SessionLocal
from dsxlineage.db import models

db = SessionLocal()
try:
    job = db.query(models.Job).order_by(models.Job.id.desc()).first()
    if not job:
        print("No jobs found")
    else:
        print(f"Latest Job ID: {job.id}, Status: {job.status}")
        print(f"  Stages: {len(job.stages)}")
        print(f"  Links: {len(job.links)}")
        print(f"  Annotations: {len(job.annotations)}")
        
        if job.links:
            print(f"\n  All Links:")
            for link in job.links:
                print(f"    {link.name}: {link.source_stage} -> {link.target_stage}")
        
        result = db.query(models.Result).filter(models.Result.job_id == job.id).first()
        if result:
            print(f"\n  Result found. ID: {result.id}")
finally:
    db.close()
