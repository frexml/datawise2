from typing import Dict, Any, List
import json
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from app.core.config import settings

class DeepAnalyzerAgent:
    def __init__(self):
        self.llm = ChatOpenAI(
            model=settings.OPENAI_MODEL, 
            temperature=0,
            api_key=settings.OPENAI_API_KEY
        )

    def _extract_subrecords(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively extract properties from DSSUBRECORDs.
        Returns a flattened dictionary of properties.
        """
        properties = {}
        
        # Extract direct properties
        for key, value in record.items():
            if key not in ["DSSUBRECORD", "InputPins", "OutputPins", "MetaBag"]:
                properties[key] = value
                
        # Extract subrecords
        if "DSSUBRECORD" in record:
            subrecords = record["DSSUBRECORD"]
            if isinstance(subrecords, list):
                for sub in subrecords:
                    if isinstance(sub, dict):
                        name = sub.get("Name", "")
                        value = sub.get("Value", "")
                        
                        if name:
                            # If value is present, use it
                            if value:
                                properties[name] = value
                            else:
                                # If no value, check for other useful fields like "Prompt", "Default"
                                sub_props = {k: v for k, v in sub.items() if k not in ["Name", "__type__", "__children__", "Owner"]}
                                if sub_props:
                                    properties[name] = sub_props
                        
                        # Recursively extract from children if any
                        # (Though DSSUBRECORD usually flat in JSON structure provided, checking just in case)
                        if "DSSUBRECORD" in sub:
                            child_props = self._extract_subrecords(sub)
                            for k, v in child_props.items():
                                properties[f"{name}.{k}"] = v
                                
        return properties

    def analyze_link(self, link: Dict[str, Any]) -> str:
        """
        Analyze a single link using LLM.
        """
        link_name = link.get("Name", "Unnamed")
        
        # 1. Extract all properties
        properties = self._extract_subrecords(link)
        
        # 2. Prepare Prompt
        prompt = f"""
        You are an expert DataStage Developer.
        
        Link to Analyze:
        Name: {link_name}
        
        Link Properties (Schema & Configuration):
        {json.dumps(properties, indent=2)}
        
        Task:
        Provide a detailed technical analysis of this link, focusing on the DATA SCHEMA.
        
        1. **Link Overview**: What is this link connecting? (e.g., "Output from Stage A to Stage B")
        2. **Schema Analysis** (Crucial):
           - Identify key columns, data types (SQLType), length/precision.
           - Note any nullable columns.
           - Explain the structure of the data being passed.
        3. **Configuration**: Any specific partitioning, sorting, or buffering settings?
        
        Output Format:
        <analysis>
        (Detailed schema and configuration analysis)
        </analysis>
        """
        
        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            return response.content
        except Exception as e:
            return f"<analysis>Error analyzing link: {e}</analysis>"

    def analyze_stage(self, stage: Dict[str, Any], context: str) -> str:
        """
        Analyze a single stage using LLM.
        """
        stage_name = stage.get("Name", "Unnamed")
        stage_type = stage.get("StageType", stage.get("OLEType", "Unknown"))
        
        # 1. Extract all properties
        properties = self._extract_subrecords(stage)
        
        # 2. Identify Special C++ Code Sections
        trx_code = properties.get("TrxGenCode", "")
        trx_cache = properties.get("TrxGenCache", "")
        trx_class = properties.get("TrxClassName", "")
        trx_warnings = properties.get("TrxGenWarnings", "")
        
        # Remove large code from properties to avoid duplication in prompt
        if "TrxGenCode" in properties:
            del properties["TrxGenCode"]
            
        # 3. Prepare Prompt
        prompt = f"""
        You are an expert DataStage Developer.
        
        Current Context (Upstream):
        {context}
        
        Stage to Analyze:
        Name: {stage_name}
        Type: {stage_type}
        
        Configuration Properties:
        {json.dumps(properties, indent=2)}
        """
        
        if trx_code:
            prompt += f"""
            
            C++ TRANSFORMATION CODE FOUND (TrxGenCode):
            Class Name: {trx_class}
            Cache Settings: {trx_cache}
            Warnings: {trx_warnings}
            
            CODE:
            ```cpp
            {trx_code}
            ```
            
            SPECIAL INSTRUCTION FOR C++ CODE:
            Analyze the provided C++ code in detail.
            1. Identify every output column assignment.
            2. Explain the logic/transformation for each column.
            3. Note any conditional logic (if/else), data type conversions, or string manipulations.
            """
            
        prompt += """
        
        Task:
        Provide a comprehensive technical analysis of this stage.
        
        1. **Stage Overview**: What is this stage and what is its primary purpose?
        2. **Configuration Analysis**: Explain key settings found in the properties (e.g., File paths, Table names, Join keys, Sort order).
        3. **Transformation Logic** (Crucial): 
           - If C++ code is present, explain it line-by-line or column-by-column as requested above.
           - If no code, explain the implicit logic based on properties (e.g., "Joins input A and B on Key X").
        4. **Data Flow**: Summary of inputs and outputs.
        5. **One-Sentence Summary**: For downstream context.
        
        Output Format:
        <analysis>
        (Detailed Markdown analysis)
        </analysis>
        <summary>
        (One sentence summary)
        </summary>
        """
        
        # Debug: Print prompt for first stage or if code exists
        if trx_code:
            print(f"Analyzing Stage with Code: {stage_name}")
            # print(prompt) # Uncomment to see full prompt
            
        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            return response.content
        except Exception as e:
            return f"<analysis>Error analyzing stage: {e}</analysis><summary>Error processing {stage_name}</summary>"

    def analyze_annotation(self, annotation: Dict[str, Any]) -> str:
        """
        Analyze a single annotation using LLM.
        """
        anno_name = annotation.get("Name", "Unnamed")
        
        # 1. Extract all properties
        properties = self._extract_subrecords(annotation)
        
        # 2. Prepare Prompt
        prompt = f"""
        You are an expert DataStage Developer.
        
        Annotation to Analyze:
        Name: {anno_name}
        
        Properties (Text & Style):
        {json.dumps(properties, indent=2)}
        
        Task:
        Explain the purpose of this annotation.
        1. **Content**: What does the annotation say?
        2. **Context**: Based on the text, what part of the job logic does it likely describe?
        
        Output Format:
        <analysis>
        (Concise explanation)
        </analysis>
        """
        
        try:
            response = self.llm.invoke([HumanMessage(content=prompt)])
            return response.content
        except Exception as e:
            return f"<analysis>Error analyzing annotation: {e}</analysis>"

    def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        analysis_result = state.get("analysis_result")
        if not analysis_result:
            raise ValueError("No analysis result provided")

        print("DeepAnalyzerAgent: Starting Comprehensive Analysis...")
        
        job_props = analysis_result.get("job_properties", {})
        job_type = state.get("parsed_data", {}).get("_metadata", {}).get("job_type", "Unknown")
        
        components = analysis_result.get("components", {})
        stages = components.get("stages", {})
        links = components.get("links", {})
        annotations = components.get("annotations", {})
        containers = components.get("containers", {})
        
        # 1. Determine Execution Order
        stage_order = []
        # Try to find StageList in CContainerView
        for container_id, container in containers.items():
            if container.get("OLEType") == "CContainerView":
                stage_list_str = container.get("StageList", "")
                if stage_list_str:
                    stage_order = [s for s in stage_list_str.split("|") if s and (not s.startswith("V") or "S" in s)]
                    break
        
        if not stage_order:
            # Fallback sort
            def sort_key(x):
                if 'V0S' in x:
                    try: return (0, int(x.replace('V0S', '')))
                    except: return (1, x)
                return (1, x)
            stage_order = sorted(stages.keys(), key=sort_key)
            
        # Initialize structured output containers
        analyzed_stages = []
        analyzed_links = []
        analyzed_annotations = []
        
        context_summary = f"Job: {job_props.get('Name')}, Type: {job_type}"
        
        # 2. Analyze Stages
        print(f"Analyzing {len(stage_order)} Stages...")
        for idx, stage_id in enumerate(stage_order):
            if stage_id not in stages:
                continue
                
            stage = stages[stage_id]
            print(f"Processing Stage {idx+1}/{len(stage_order)}: {stage.get('Name')}")
            
            # Analyze Stage
            llm_output = self.analyze_stage(stage, context_summary)
            
            try:
                analysis = llm_output.split("<analysis>")[1].split("</analysis>")[0].strip()
                summary = llm_output.split("<summary>")[1].split("</summary>")[0].strip()
                
                # Extract properties including TrxGenCode
                stage_props = self._extract_subrecords(stage)
                
                analyzed_stages.append({
                    "stage_id": stage_id,
                    "name": stage.get("Name"),
                    "type": stage.get("StageType", stage.get("OLEType", "Unknown")),
                    "properties": stage_props,
                    "llm_explanation": analysis
                })
                
                # Update context with this stage's summary
                context_summary += f"\n- {stage.get('Name')}: {summary}"
                
                # Keep context manageable (last 10 stages)
                context_lines = context_summary.split('\n')
                if len(context_lines) > 15:
                    context_summary = context_lines[0] + "\n" + "\n".join(context_lines[-10:])
                    
            except Exception as e:
                print(f"Error parsing LLM output for {stage_id}: {e}")
                analyzed_stages.append({
                    "stage_id": stage_id,
                    "name": stage.get("Name"),
                    "type": stage.get("StageType", "Unknown"),
                    "properties": {},
                    "llm_explanation": f"Error analyzing stage: {e}"
                })

        # 3. Analyze Links (Separately)
        print(f"Analyzing {len(links)} Links...")
        for link_id, link in links.items():
            print(f"Processing Link: {link.get('Name')}")
            link_analysis_output = self.analyze_link(link)
            try:
                link_analysis = link_analysis_output.split("<analysis>")[1].split("</analysis>")[0].strip()
                
                analyzed_links.append({
                    "link_id": link_id,
                    "name": link.get("Name"),
                    "source_stage": "Unknown", # Lineage skipped for now
                    "target_stage": "Unknown",
                    "properties": self._extract_subrecords(link),
                    "llm_explanation": link_analysis
                })
            except Exception as e:
                print(f"Error analyzing link {link.get('Name')}: {e}")

        # 4. Analyze Annotations
        if annotations:
            print(f"Analyzing {len(annotations)} annotations...")
            for anno_id, anno in annotations.items():
                print(f"  Analyzing Annotation: {anno.get('Name', 'Unnamed')}")
                anno_output = self.analyze_annotation(anno)
                try:
                    anno_analysis = anno_output.split("<analysis>")[1].split("</analysis>")[0].strip()
                    
                    analyzed_annotations.append({
                        "annotation_id": anno_id,
                        "name": anno.get("Name", "Unnamed"),
                        "text": anno.get("Text", ""), # Assuming Text property exists or is extracted
                        "properties": self._extract_subrecords(anno),
                        "llm_explanation": anno_analysis
                    })
                except:
                    print(f"Error analyzing annotation {anno.get('Name')}")

        # Final Polish (Executive Summary)
        # We can construct a simplified report for the executive summary generation
        simple_report = ""
        for s in analyzed_stages:
            simple_report += f"Stage: {s['name']} ({s['type']})\n{s['llm_explanation'][:200]}...\n\n"
            
        final_prompt = f"""
        You are an expert DataStage Developer.
        
        Here is a summary of the job components:
        {simple_report}
        
        Task:
        Write a high-level executive summary of what this entire job accomplishes.
        
        Output:
        (Executive Summary)
        """
        
        final_response = self.llm.invoke([HumanMessage(content=final_prompt)])
        executive_summary = final_response.content
        
        return {
            "executive_summary": executive_summary,
            "stages": analyzed_stages,
            "links": analyzed_links,
            "annotations": analyzed_annotations
        }
