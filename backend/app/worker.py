from celery import Celery
from app.core.config import settings
from app.agents.workflow import process_file
from app.db.database import SessionLocal
from app.db import models
from app.db.models import Result, Stage, Link, Annotation, Lineage
import json

celery_app = Celery(
    "worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND
)

@celery_app.task
def generate_lineage_task(job_id: int):
    """Async task to generate lineage after main processing"""
    db = SessionLocal()
    try:
        print(f"Generating lineage for job {job_id}...")
        from app.services.lineage_analyzer import find_all_paths, analyze_lineage_with_llm
        
        stages_list = db.query(Stage).filter(Stage.job_id == job_id).all()
        links_list = db.query(Link).filter(Link.job_id == job_id).all()
        
        if stages_list and links_list:
            lineage_records = find_all_paths(stages_list, links_list)
            
            # Analyze with LLM and save to DB
            for record in lineage_records:
                analyze_lineage_with_llm(record)
                
                lineage = Lineage(
                    job_id=job_id,
                    target_table=record['target_table'],
                    target_field=record['target_field'],
                    source_table=record['source_table'],
                    source_field=record['source_field'],
                    source_link=record['source_link'],
                    target_link=record['target_link'],
                    full_path=record['full_path'],
                    total_hops=record['total_hops'],
                    transformation_logic=record['transformation_logic'],
                    transformation_explanation=record['transformation_explanation'],
                    transformation_type=record['transformation_type'],
                    cardinality=record['cardinality']
                )
                db.add(lineage)
            
            db.commit()
            print(f"Lineage generation completed: {len(lineage_records)} records.")
    except Exception as e:
        print(f"Error generating lineage for job {job_id}: {e}")
        db.rollback()
    finally:
        db.close()

@celery_app.task(bind=True)
def process_dsx_task(self, job_id: int, file_path: str):
    db = SessionLocal()
    job = None # Initialize job outside try block for finally access
    try:
        # Update status to PROCESSING
        job = db.query(models.Job).filter(models.Job.id == job_id).first()
        if job:
            job.status = "PROCESSING"
            db.commit()
        
        # Run LangGraph Workflow
        # The process_file function is expected to return a dictionary
        # containing parsed_data, analysis_result, and structured components
        # like stages, links, and annotations.
        final_output = process_file(file_path)
        
        parsed_data = final_output.get("parsed_data", {})
        analysis_result = final_output.get("analysis_result", {})

        # Update Job status and save structured results
        if job:
            job.status = "COMPLETED"
            
            # Save main result
            result = Result(
                job_id=job_id,
                raw_json=parsed_data,
                analysis_summary=analysis_result, # Fixed: Use analysis_result directly
                llm_explanation=final_output.get("executive_summary", "")
            )
            db.add(result)
            
            # Get lineage info
            lineage_result = final_output.get("lineage_result", {})
            link_map = lineage_result.get("links", {})
            stage_positions = lineage_result.get("positions", {})
            
            print(f"Worker: Received {len(stage_positions)} stage positions.")
            if stage_positions:
                print(f"Worker: Sample Position Key: {list(stage_positions.keys())[0]}")
            
            # Save Stages
            for stage_data in final_output.get("stages", []):
                stage_id = stage_data.get("stage_id")
                props = stage_data.get("properties", {})
                
                # Add position to properties if available
                if stage_id in stage_positions:
                    props["x_pos"] = stage_positions[stage_id]["x"]
                    props["y_pos"] = stage_positions[stage_id]["y"]
                else:
                    print(f"Worker: Warning - No position for stage {stage_id}")
                
                stage = Stage(
                    job_id=job_id,
                    stage_id=stage_id,
                    name=stage_data.get("name"),
                    type=stage_data.get("type"),
                    properties=props,
                    llm_explanation=stage_data.get("llm_explanation")
                )
                db.add(stage)
            
            # Save Links - use edges from lineage_result to capture all partner connections
            edges = lineage_result.get("edges", [])
            
            print(f"Worker: Processing {len(edges)} edges from lineage_result")
            if edges:
                sample_edge = edges[0]
                print(f"Worker: Sample edge - {sample_edge.get('link_name')}: src={sample_edge.get('source')}, tgt={sample_edge.get('target')}, src_pin={sample_edge.get('source_pin')}, tgt_pin={sample_edge.get('target_pin')}")

            # Create map of link names to properties/explanations from analyzer
            link_props_map = {}
            for link in final_output.get("links", []):
                link_props_map[link.get("name")] = {
                    "link_id": link.get("link_id"),
                    "properties": link.get("properties"),
                    "llm_explanation": link.get("llm_explanation")
                }

            # Save each edge as a separate link record
            for edge in edges:
                link_name = edge.get("link_name")
                link_props = link_props_map.get(link_name, {})
                
                db_link = Link(
                    job_id=job_id,
                    link_id=link_props.get("link_id"),
                    name=link_name,
                    source_stage=edge.get("source"),
                    target_stage=edge.get("target"),
                    source_pin=edge.get("source_pin"),
                    target_pin=edge.get("target_pin"),
                    properties=link_props.get("properties"),
                    llm_explanation=link_props.get("llm_explanation")
                )
                db.add(db_link)
                
            # Save Annotations
            for anno_data in final_output.get("annotations", []):
                anno = Annotation(
                    job_id=job_id,
                    annotation_id=anno_data.get("annotation_id"),
                    name=anno_data.get("name"),
                    text=anno_data.get("text"),
                    properties=anno_data.get("properties"),
                    llm_explanation=anno_data.get("llm_explanation")
                )
                db.add(anno)
            
            db.commit()
            
            # Trigger async lineage generation
            generate_lineage_task.delay(job_id)
            print(f"Task {job_id} completed successfully.")
            
        return {"status": "success", "job_id": job_id}
        
    except Exception as e:
        print(f"Error processing task {job_id}: {e}")
        db.rollback() # Rollback any changes made during the try block
        if job:
            job.status = "FAILED"
            db.commit() # Commit the status change
        raise e
    finally:
        db.close()
