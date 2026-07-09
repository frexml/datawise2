from celery import Celery
from dsxlineage.core.config import settings
from dsxlineage.agents.workflow import process_file
from dsxlineage.db.database import SessionLocal
from dsxlineage.db import models
from dsxlineage.db.models import Result, Stage, Link, Annotation, Lineage
from dsxlineage.db.graph import sync_job_to_graph
from dsxlineage.agents.inefficiency_agent import detect_inefficiencies
import json

celery_app = Celery(
    "worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND
)

@celery_app.task
def generate_lineage_task(job_id: int):
    """Async task to generate lineage after main processing.

    Lineage records are built deterministically from stages + links; no LLM
    call is needed per record. Records are bulk-inserted in a single trip.
    """
    db = SessionLocal()
    try:
        print(f"Generating lineage for job {job_id}...")
        from dsxlineage.services.lineage_analyzer import find_all_paths

        stages_list = db.query(Stage).filter(Stage.job_id == job_id).all()
        links_list = db.query(Link).filter(Link.job_id == job_id).all()

        if not (stages_list and links_list):
            return

        lineage_records = find_all_paths(stages_list, links_list)
        if not lineage_records:
            return

        objects = [
            Lineage(
                job_id=job_id,
                target_table=r["target_table"],
                target_field=r["target_field"],
                source_table=r["source_table"],
                source_field=r["source_field"],
                source_link=r["source_link"],
                target_link=r["target_link"],
                full_path=r["full_path"],
                total_hops=r["total_hops"],
                transformation_logic=r["transformation_logic"],
                transformation_explanation=r["transformation_explanation"],
                transformation_type=r["transformation_type"],
                cardinality=r["cardinality"],
            )
            for r in lineage_records
        ]
        db.bulk_save_objects(objects)
        db.commit()
        print(f"Lineage generation completed: {len(objects)} records.")
    except Exception as e:
        print(f"Error generating lineage for job {job_id}: {e}")
        db.rollback()
    finally:
        db.close()

@celery_app.task
def regenerate_summary_task(review_id: int):
    """Re-run just the executive summary after a reviewer rejects it and
    chooses "Re-run" — the rejection feedback is fed back into the prompt.

    Rebuilds the per-stage bullet list from persisted Stage.llm_explanation
    (the original per-stage one-liners used on the first pass aren't stored),
    then reuses the same review row: regenerating -> pending_review again,
    so the audit trail (previous reviewer/feedback) stays visible until the
    next decision overwrites it.
    """
    from dsxlineage.agents.deep_analyzer_agent import DeepAnalyzerAgent

    db = SessionLocal()
    try:
        review = db.query(models.Review).filter(models.Review.id == review_id).first()
        if not review or review.target_type != "executive_summary":
            print(f"regenerate_summary_task: review {review_id} not found or not an executive_summary")
            return

        result = db.query(Result).filter(Result.id == review.target_id).first()
        if not result:
            print(f"regenerate_summary_task: result {review.target_id} not found for review {review_id}")
            return

        stages = db.query(Stage).filter(Stage.job_id == review.job_id).all()
        bullets = [
            f"- {s.name} ({s.type}): {(s.llm_explanation or '')[:200]}"
            for s in stages
        ]
        bullets_text = "\n".join(bullets) or "(no stages analyzed)"

        agent = DeepAnalyzerAgent()
        technical, business = agent.regenerate_executive_summary(bullets_text, feedback=review.feedback)

        result.llm_explanation = technical
        result.business_summary = business
        review.status = "pending_review"
        db.commit()
        print(f"regenerate_summary_task: regenerated summary for job {review.job_id}, review {review_id} back to pending_review.")
    except Exception as e:
        print(f"Error regenerating summary for review {review_id}: {e}")
        db.rollback()
        # Leave the review in "regenerating" rather than silently reverting to
        # "rejected" — a stuck state is visible and re-triggerable, a silent
        # revert would look like the rerun never happened.
    finally:
        db.close()

@celery_app.task
def generate_scopeiq_estimate_task(job_id: int):
    """Async task backing the on-demand 'Generate ScopeIQ Estimate' button.

    The triggering endpoint already created/reset the ScopeIQEstimate row to
    status="generating" before dispatching this task, so a missing row here
    means the job was deleted mid-flight — nothing to do.
    """
    from dsxlineage.agents.scopeiq_agent import generate_estimate_for_job

    db = SessionLocal()
    try:
        estimate = db.query(models.ScopeIQEstimate).filter(models.ScopeIQEstimate.job_id == job_id).first()
        if not estimate:
            print(f"generate_scopeiq_estimate_task: no ScopeIQEstimate row for job {job_id}, skipping")
            return

        try:
            result = generate_estimate_for_job(job_id)
        except Exception as exc:
            estimate.status = "failed"
            estimate.error = str(exc)
            db.commit()
            print(f"ScopeIQ estimate generation failed for job {job_id}: {exc}")
            return

        estimate.status = "completed"
        estimate.package_id = result["package_id"]
        estimate.dimensions = result["dimensions"]
        estimate.role_day_totals = result["role_day_totals"]
        estimate.uplift_adjustments = result["uplift_adjustments"]
        estimate.risk_adjustments = result["risk_adjustments"]
        estimate.total_days_base = result["total_days_base"]
        estimate.total_days_adjusted = result["total_days_adjusted"]
        estimate.complexity_tier = result["complexity_tier"]
        estimate.generated_at = result["generated_at"]
        db.commit()
        print(
            f"ScopeIQ estimate generated for job {job_id}: "
            f"{result['total_days_adjusted']} days ({result['complexity_tier']})."
        )
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

        def on_stage(stage: str):
            """Persist real pipeline progress for the Home page's animated stepper."""
            if job:
                job.current_stage = stage
                db.commit()

        # Run LangGraph Workflow
        # The process_file function is expected to return a dictionary
        # containing parsed_data, analysis_result, and structured components
        # like stages, links, and annotations.
        final_output = process_file(file_path, on_stage=on_stage)

        parsed_data = final_output.get("parsed_data", {})
        analysis_result = final_output.get("analysis_result", {})

        # Update Job status and save structured results
        if job:
            job.current_stage = "saving_results"
            db.commit()

            # Save main result
            result = Result(
                job_id=job_id,
                raw_json=parsed_data,
                analysis_summary=analysis_result, # Fixed: Use analysis_result directly
                llm_explanation=final_output.get("executive_summary", ""),
                business_summary=final_output.get("executive_summary_business", "")
            )
            db.add(result)
            db.flush()  # populate result.id for the Review row below

            db.add(models.Review(
                job_id=job_id,
                target_type="executive_summary",
                target_id=result.id,
                status="pending_review",
            ))


            # Get lineage info
            lineage_result = final_output.get("lineage_result", {})
            link_map = lineage_result.get("links", {})
            stage_positions = lineage_result.get("positions", {})
            
            print(f"Worker: Received {len(stage_positions)} stage positions.")
            if stage_positions:
                print(f"Worker: Sample Position Key: {list(stage_positions.keys())[0]}")
            
            # Save Stages
            graph_stages = []
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
                graph_stages.append({
                    "stage_id": stage_id,
                    "name": stage_data.get("name"),
                    "type": stage_data.get("type"),
                })

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
            graph_links = []
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
                graph_links.append({
                    "name": link_name,
                    "source_stage": edge.get("source"),
                    "target_stage": edge.get("target"),
                    "source_pin": edge.get("source_pin"),
                    "target_pin": edge.get("target_pin"),
                })

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

            job.current_stage = "detecting_inefficiencies"
            db.commit()

            # Mirror stages/links into Neo4j for graph-native queries
            # (inefficiency detection, future lineage traversal). Additive
            # only — a Neo4j failure must not fail the job.
            try:
                sync_job_to_graph(job_id, job.filename, graph_stages, graph_links)
            except Exception as graph_err:
                print(f"Warning: Neo4j sync failed for job {job_id}: {graph_err}")
            else:
                # Inefficiency detection needs the graph mirror above, so it
                # only runs when that sync succeeded. Best-effort, same as
                # the sync itself — must not fail the job.
                try:
                    findings = detect_inefficiencies(job_id)
                    print(f"Inefficiency detection for job {job_id}: {len(findings)} pattern(s) flagged.")
                except Exception as ineff_err:
                    print(f"Warning: inefficiency detection failed for job {job_id}: {ineff_err}")

            job.status = "COMPLETED"
            job.current_stage = "completed"
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
