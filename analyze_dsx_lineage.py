#!/usr/bin/env python3
"""
DSX File Analyzer
This program analyzes a DataStage DSX file and extracts column lineage information.
It shows input columns, transformations, C++ code, and output column derivations.

Usage:
    python analyze_dsx_lineage.py [--use-llm] [--llm-batch-size N]
    
    --use-llm: Use OpenAI GPT-4o to generate contextual transformation descriptions
    --llm-batch-size: Number of transformations to process per API call (default: 50)
    
Output:
    - Console output showing analysis
    - CSV file: dsx_lineage_analysis.csv with detailed mappings
"""

import re
import sys
import csv
import argparse
import os
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

# Try to import tqdm for progress bars
try:
    from tqdm import tqdm
except ImportError:
    # Fallback if tqdm is not available
    def tqdm(iterable, desc="", total=None):
        return iterable

# Try to import dotenv for .env file support
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def parse_dsx(file_path: str) -> Dict[str, Any]:
    """
    Parses a DSX file into a Python dictionary.
    """
    try:
        with open(file_path, 'r', encoding='latin-1') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except Exception as e:
            raise Exception(f"Failed to read file {file_path}: {e}")

    start_index = 0
    content_lines = lines[start_index:]
    
    root = {}
    stack = [root]
    
    i = 0
    while i < len(content_lines):
        line = content_lines[i].strip()
        
        if not line:
            i += 1
            continue

        # Check for block start
        if line.startswith('BEGIN '):
            block_type = line.split(' ', 1)[1]
            new_block = {'__type__': block_type, '__children__': []}
            
            # Add to current parent
            parent = stack[-1]
            if block_type not in parent:
                 parent[block_type] = []
            parent[block_type].append(new_block)
            
            stack.append(new_block)
            i += 1
            continue
            
        # Check for block end
        if line.startswith('END '):
            if len(stack) > 1:
                stack.pop()
            i += 1
            continue
            
        # Check for multiline value start
        if line.endswith('=+=+=+='):
            key = line.replace('=+=+=+=', '').strip()
            # Read until next =+=+=+=
            buffer = []
            i += 1
            while i < len(content_lines):
                sub_line = content_lines[i] 
                if sub_line.strip() == '=+=+=+=':
                    break
                buffer.append(sub_line)
                i += 1
            
            stack[-1][key] = ''.join(buffer)
            i += 1
            continue

        # Standard Key "Value" or Key Value
        match = re.match(r'^([^"]+)\s+"(.*)"$', line)
        if match:
            key = match.group(1).strip()
            value = match.group(2)
            stack[-1][key] = value
            i += 1
            continue
            
        # Try unquoted value
        parts = line.split(None, 1)
        if len(parts) == 2:
            key = parts[0]
            value = parts[1]
            stack[-1][key] = value
        else:
            stack[-1][line] = None
            
        i += 1

    return root


def extract_stages_info(parsed_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract stage information from parsed DSX data."""
    
    stages_info = {}
    
    # DSRECORD entries are nested inside DSJOB
    dsjob_list = parsed_data.get("DSJOB", [])
    
    if not isinstance(dsjob_list, list) or len(dsjob_list) == 0:
        # Fallback: try to get DSRECORD from root
        ds_records = parsed_data.get("DSRECORD", [])
    else:
        # Get DSRECORD from the first DSJOB
        job = dsjob_list[0]
        ds_records = job.get("DSRECORD", [])
    
    if not isinstance(ds_records, list):
        return stages_info
    
    for record in ds_records:
        stage_name = record.get("Name", "")
        stage_type = record.get("StageType", "")
        ole_type = record.get("OLEType", "")
        
        if not stage_name:
            continue
            
        stages_info[stage_name] = {
            "name": stage_name,
            "type": stage_type,
            "ole_type": ole_type,
            "identifier": record.get("Identifier", ""),
            "input_pins": [],
            "output_pins": [],
            "properties": {},
            "dsrecord": record
        }
    
    return stages_info


def extract_transformer_logic(stage_record: Dict[str, Any], all_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Extract transformation logic from a transformer stage.
    Returns column mappings, derivations, and C++ code.
    """
    result = {
        "input_columns": [],
        "output_columns": [],
        "derivations": [],
        "cpp_code": "",
        "constraints": []
    }
    
    # Get the pin IDs from this stage
    input_pins = stage_record.get("InputPins", "").split("|")
    output_pins = stage_record.get("OutputPins", "").split("|")
    
    # Find the pin records that contain column definitions
    for rec in all_records:
        rec_id = rec.get("Identifier", "")
        ole_type = rec.get("OLEType", "")
        rec_name = rec.get("Name", "")
        
        # Check if this is an input pin for this stage
        if rec_id in input_pins and ole_type in ['CTrxInput', 'CCustomInput']:
            columns = extract_columns_from_record(rec)
            for col in columns:
                col['link_name'] = rec_name
                col['pin_type'] = 'input'
                result["input_columns"].append(col)
        
        # Check if this is an output pin for this stage
        elif rec_id in output_pins and ole_type in ['CTrxOutput', 'CCustomOutput']:
            columns = extract_columns_from_record(rec)
            for col in columns:
                col['link_name'] = rec_name
                col['pin_type'] = 'output'
                result["output_columns"].append(col)
                
                # Output columns have derivations
                if col.get('derivation'):
                    result["derivations"].append({
                        "column": col['name'],
                        "expression": col['derivation']
                    })
    
    # Look for C++ code in BuildOp or custom transforms
    cpp_code = stage_record.get("BuildOp", "")
    if not cpp_code:
        cpp_code = stage_record.get("CustomCode", "")
    if cpp_code:
        result["cpp_code"] = cpp_code
    
    # Look for constraints
    constraints = stage_record.get("Constraint", "")
    if constraints:
        result["constraints"].append(constraints)
    
    return result


def extract_columns_from_record(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract column definitions from a pin record."""
    columns = []
    
    # Check if this record has CMetaProperty metadata
    has_metabag = record.get("MetaBag") == "CMetaProperty"
    
    # Look for DSSUBRECORD entries that represent columns
    subrecs = record.get("DSSUBRECORD", [])
    if isinstance(subrecs, list):
        metabag_section = False
        
        for sr in subrecs:
            if not isinstance(sr, dict):
                continue
            
            # Check if we're in a MetaBag CMetaProperty section
            if has_metabag and sr.get("Owner") == "APT":
                metabag_section = True
                continue
                
            col_name = sr.get("Name", "")
            
            # Skip metadata entries
            if col_name in ['RTColumnProp', 'Schema', 'Part/Col', 'Sort', 'SeqSort', 
                          'SortAdv', 'TrxGenCache', 'TrxClassName', 'TrxGenWarnings', 
                          'TrxGenCode', 'schema', 'file', 'append\\\\overwrite']:
                continue
            
            # Skip if this is a CMetaProperty field (has Owner="APT" and we're in metabag context)
            if metabag_section or (has_metabag and sr.get("Owner") == "APT"):
                continue
            
            if col_name:
                columns.append({
                    "name": col_name,
                    "sql_type": sr.get("SqlType", ""),
                    "precision": sr.get("Precision", ""),
                    "scale": sr.get("Scale", ""),
                    "nullable": sr.get("Nullable", ""),
                    "description": sr.get("Description", ""),
                    "derivation": sr.get("Derivation", "")
                })
    
    return columns


def analyze_column_lineage(stages_info: Dict[str, Any], parsed_data: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Analyze column lineage across all stages.
    Returns a list of lineage records showing how output columns are derived.
    """
    lineage_records = []
    
    # Get container view for link information
    container_view = find_container_view(parsed_data)
    
    # Build stage connections
    connections, link_source_map = build_stage_connections(container_view, stages_info)
    
    # Get all DSRECORD entries to pass to transformer logic extraction
    dsjob_list = parsed_data.get("DSJOB", [])
    all_records = []
    if isinstance(dsjob_list, list) and len(dsjob_list) > 0:
        job = dsjob_list[0]
        all_records = job.get("DSRECORD", [])
    
    # Count transformer stages for progress
    transformer_stages = [(name, info) for name, info in stages_info.items() 
                         if "Transformer" in info.get("ole_type", "") or "Transformer" in info.get("type", "")]
    
    # Analyze each transformer stage with progress bar
    for stage_name, stage_info in tqdm(transformer_stages, desc="   Processing transformers"):
            
        # Extract transformation logic
        transform_logic = extract_transformer_logic(stage_info["dsrecord"], all_records)
        
        # Map output columns to input columns
        for output_col in transform_logic["output_columns"]:
            col_name = output_col["name"]
            derivation = output_col.get("derivation", "")
            
            # Analyze the derivation to identify source columns
            source_info = analyze_derivation(
                derivation, 
                transform_logic["input_columns"],
                connections.get(stage_name, []),
                link_source_map
            )
            
            # Create lineage record
            record = {
                "Target_Table": output_col.get('link_name', stage_name),
                "Target_Field": col_name,
                "Stage_Type": stage_info.get("type", "Transformer"),
                "Stage": stage_name,
                "Source_Table": source_info["source_table"],
                "Source_Field": source_info["source_field"],
                "Transformation_Logic": source_info["logic"],
                "Expression": derivation,
                "Cardinality": source_info["cardinality"],
                "Transformation_Type": source_info["type"],
                "Notes": source_info["notes"]
            }
            
            lineage_records.append(record)
            
        # Handle C++ code transformations
        if transform_logic["cpp_code"]:
            cpp_snippet = transform_logic["cpp_code"]
            # Truncate if too long
            if len(cpp_snippet) > 200:
                cpp_snippet = cpp_snippet[:200] + "..."
                
            lineage_records.append({
                "Target_Table": "CUSTOM_OUTPUT",
                "Target_Field": "MULTIPLE",
                "Stage_Type": stage_info.get("type", "Transformer"),
                "Stage": stage_name,
                "Source_Table": "CUSTOM_CPP",
                "Source_Field": "MULTIPLE",
                "Transformation_Logic": f"C++ BuildOp: {cpp_snippet}",
                "Expression": cpp_snippet,
                "Cardinality": "N->M",
                "Transformation_Type": "custom_cpp",
                "Notes": "Custom C++ transformation code"
            })
    
    # Analyze non-transformer stages (lookups, etc.) - excluding joins and aggregators
    other_stages = [(name, info) for name, info in stages_info.items() 
                   if "Lookup" in info.get("type", "") and "Join" not in info.get("type", "") 
                   and info.get("type", "") != "Aggregator/RemDup"]
    
    for stage_name, stage_info in tqdm(other_stages, desc="   Processing other stages"):
        # Handle lookup logic
        lookup_records = analyze_lookup_stage(stage_info, connections, link_source_map)
        lineage_records.extend(lookup_records)
    
    return lineage_records


def find_container_view(parsed_data: Dict[str, Any]) -> Dict[str, Any]:
    """Find the CContainerView in the parsed data."""
    
    ds_records = parsed_data.get("DSRECORD", [])
    
    if isinstance(ds_records, list):
        for record in ds_records:
            if record.get("OLEType") == "CContainerView":
                return record
    
    # Recursive search
    def find_view(data):
        if isinstance(data, dict):
            if data.get("OLEType") == "CContainerView":
                return data
            for k, v in data.items():
                res = find_view(v)
                if res: return res
        elif isinstance(data, list):
            for item in data:
                res = find_view(item)
                if res: return res
        return None
    
    return find_view(parsed_data) or {}


    return connections, link_source_map


def build_stage_connections(container_view: Dict[str, Any], stages_info: Dict[str, Any]) -> Tuple[Dict[str, List[str]], Dict[str, str]]:
    """
    Build a map of stage connections (which stages feed into which).
    Returns:
        - connections: target_stage -> list of source stages
        - link_source_map: link_name -> source_stage_name
    """
    
    connections = {}  # target_stage -> list of source stages
    link_source_map = {} # link_name -> source_stage_name
    
    stage_names = container_view.get("StageNames", "").split("|")
    stage_ids = container_view.get("StageList", "").split("|")
    link_groups = container_view.get("LinkNames", "").split("|")
    target_groups = container_view.get("TargetStageIDs", "").split("|")
    source_groups = container_view.get("LinkSourcePinIDs", "").split("|")
    
    # Map stage IDs to names
    stage_map = dict(zip(stage_ids, stage_names))
    
    # Build connections
    for i in range(len(link_groups)):
        links = [l.strip() for l in link_groups[i].split(",") if l.strip()]
        targets = [t.strip() for t in target_groups[i].split(",") if t.strip()] if i < len(target_groups) else []
        sources = [s.strip() for s in source_groups[i].split(",") if s.strip()] if i < len(source_groups) else []
        
        for j, link_name in enumerate(links):
            # Get source pin ID and extract stage ID
            source_pin_id = sources[j] if j < len(sources) else None
            source_stage_id = None
            
            if source_pin_id and 'P' in source_pin_id:
                source_stage_id = source_pin_id.split('P')[0]
            
            source_name = stage_map.get(source_stage_id, "Unknown") if source_stage_id else "Unknown"
            
            # Map link to source stage
            link_source_map[link_name] = source_name
            
            # Get target stage ID
            target_id = targets[j] if j < len(targets) else None
            target_name = stage_map.get(target_id, "Unknown") if target_id else "Unknown"
            
            if target_name not in connections:
                connections[target_name] = []
            connections[target_name].append(source_name)
    
    return connections, link_source_map


def determine_cardinality_simple(expression: str) -> str:
    """
    Simple cardinality determination without LLM.
    """
    if not expression or expression.strip() == "":
        return "1->1"
    
    # Count column references (format: Link.Column)
    pattern = r'\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b'
    matches = re.findall(pattern, expression)
    unique_refs = list(set(matches))
    
    if len(unique_refs) <= 1:
        return "1->1"
    else:
        return "N->1"


def analyze_expression_with_llm(expression: str) -> dict:
    """
    Single LLM call to extract all information from expression.
    """
    try:
        import openai
        import os
        
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return {"fields": "MULTIPLE", "tables": "MULTIPLE", "type": "expression"}
        
        client = openai.OpenAI(api_key=api_key)
        
        prompt = f"""Analyze this DataStage expression and provide:

Expression: {expression}

1. Source Fields: List all column references (TableName.ColumnName format)
2. Source Tables: List all table/link names (part before dot)
3. Transformation Type based on rules:
   - Single source field only → "direct"
   - IF/Else/conditional logic → "conditional"
   - Arithmetic operators (+,-,*,/,%) → "expression"
   - Functions (IsNull, Oconv, etc.) → "function"
   - Literal values only → "constant"

Format response as:
FIELDS: field1, field2, field3
TABLES: table1, table2
TYPE: direct

If none found, use "NONE".

Analysis:"""
        
        response = client.chat.completions.create(
            model="gpt-4-turbo-preview",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0,
            timeout=30
        )
        
        result = response.choices[0].message.content.strip()
        
        # Parse response
        fields = "MULTIPLE"
        tables = "MULTIPLE"
        trans_type = "expression"
        
        for line in result.split('\n'):
            if line.startswith('FIELDS:'):
                fields = line.replace('FIELDS:', '').strip()
            elif line.startswith('TABLES:'):
                tables = line.replace('TABLES:', '').strip()
            elif line.startswith('TYPE:'):
                trans_type = line.replace('TYPE:', '').strip().lower()
        
        valid_types = ["direct", "conditional", "expression", "function", "constant"]
        if trans_type not in valid_types:
            trans_type = "expression"
            
        return {
            "fields": fields if fields != "NONE" else "MULTIPLE",
            "tables": tables if tables != "NONE" else "MULTIPLE",
            "type": trans_type
        }
        
    except Exception:
        return {"fields": "MULTIPLE", "tables": "MULTIPLE", "type": "expression"}


def analyze_derivation(expression: str, input_columns: List[Dict], source_stages: List[str], link_source_map: Dict[str, str] = None) -> Dict[str, str]:
    """
    Analyze a derivation expression to identify source columns and transformation type.
    """
    link_source_map = link_source_map or {}
    
    if not expression or expression.strip() == "":
        cardinality = determine_cardinality_simple(expression)
        return {
            "source_table": "NONE_IDENTIFIED",
            "source_field": "NONE_IDENTIFIED",
            "logic": "No derivation specified",
            "cardinality": cardinality,
            "type": "passthrough",
            "notes": ""
        }
    
    # Use single LLM call to get all information
    print(f"   Analyzing expression: {expression[:50]}...")
    llm_result = analyze_expression_with_llm(expression)
    transformation_type = llm_result["type"]
    source_fields = llm_result["fields"]
    source_tables = llm_result["tables"]
    
    # Determine cardinality based on source field count
    if source_fields and source_fields != "MULTIPLE" and source_fields != "NONE":
        field_count = len([f.strip() for f in source_fields.split(',') if f.strip()])
        cardinality = "N->1" if field_count > 1 else "1->1"
    else:
        cardinality = determine_cardinality_simple(expression)
    
    # Check for constant assignment
    if expression.startswith("'") or expression.startswith('"') or expression.isdigit() or expression.startswith('@'):
        return {
            "source_table": "NONE",
            "source_field": "NONE",
            "logic": f"Assign constant value {expression}",
            "cardinality": cardinality,
            "type": transformation_type,
            "notes": ""
        }
    
    # Check for job parameter
    if expression.startswith('#') or 'DSJobParam' in expression:
        param_name = expression.replace('#', '').replace('DSJobParam.', '')
        return {
            "source_table": "JobParameter",
            "source_field": param_name,
            "logic": f"Job parameter: {expression}",
            "cardinality": cardinality,
            "type": transformation_type,
            "notes": ""
        }
    
    # Check for direct copy (just a column name)
    if re.match(r'^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$', expression.strip()):
        # Format: LinkName.ColumnName
        parts = expression.strip().split('.')
        link_name = parts[0]
        col_name = parts[1]
        
        # Use link name as source table
        return {
            "source_table": link_name,
            "source_field": parts[1],
            "logic": f"Direct copy from {expression}",
            "cardinality": cardinality,
            "type": transformation_type,
            "notes": ""
        }
    
    # Check for function calls
    if '(' in expression:
        # Extract function name
        func_match = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*\(', expression)
        if func_match:
            func_name = func_match.group(1)
            
            # Extract referenced columns
            referenced_cols = extract_column_references(expression)
            
            # Use link names as source tables
            source_tables = []
            for ref in referenced_cols:
                link = ref.split('.')[0]
                source_tables.append(link)
            
            unique_sources = sorted(list(set(source_tables)))
            
            return {
                "source_table": ", ".join(unique_sources) if unique_sources else "MULTIPLE",
                "source_field": ", ".join(referenced_cols) if referenced_cols else "MULTIPLE",
                "logic": f"Function: {expression}",
                "cardinality": cardinality,
                "type": transformation_type,
                "notes": f"Uses function: {func_name}"
            }
    
    # Check for expressions with operators
    if any(op in expression for op in ['+', '-', '*', '/', '||', '&&']):
        referenced_cols = extract_column_references(expression)
        
        # Use link names as source tables
        source_tables = []
        for ref in referenced_cols:
            link = ref.split('.')[0]
            source_tables.append(link)
        
        unique_sources = sorted(list(set(source_tables)))
        
        return {
            "source_table": ", ".join(unique_sources) if unique_sources else "MULTIPLE",
            "source_field": ", ".join(referenced_cols) if referenced_cols else "MULTIPLE",
            "logic": f"Expression: {expression}",
            "cardinality": cardinality,
            "type": transformation_type,
            "notes": ""
        }
    
    # Default: treat as column reference or constant
    if expression.strip():
        # Use extracted source fields and tables
        if source_fields and source_fields not in ["MULTIPLE", "NONE"]:
            return {
                "source_table": source_tables,
                "source_field": source_fields,
                "logic": f"Complex expression: {expression}",
                "cardinality": cardinality,
                "type": transformation_type,
                "notes": ""
            }
        else:
            return {
                "source_table": "NONE",
                "source_field": "NONE",
                "logic": f"Constant or expression: {expression}",
                "cardinality": cardinality,
                "type": transformation_type,
                "notes": ""
            }
    
    return {
        "source_table": "NONE_IDENTIFIED",
        "source_field": "NONE_IDENTIFIED",
        "logic": "Unknown expression",
        "cardinality": cardinality,
        "type": transformation_type,
        "notes": ""
    }


def extract_column_references(expression: str) -> List[str]:
    """Extract column references from an expression (format: LinkName.ColumnName)."""
    
    # Pattern to match Link.Column references
    pattern = r'\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b'
    matches = re.findall(pattern, expression)
    
    return list(set(matches))  # Remove duplicates


def analyze_join_stage(stage_info: Dict[str, Any], connections: Dict[str, List[str]], all_records: List[Dict[str, Any]], link_source_map: Dict[str, str] = None) -> List[Dict[str, str]]:
    """Analyze a join stage and extract column lineage."""
    
    records = []
    stage_name = stage_info["name"]
    link_source_map = link_source_map or {}
    
    # Find output links for this stage
    output_links = [link for link, source_stage in link_source_map.items() if source_stage == stage_name]
    target_link = output_links[0] if output_links else f"{stage_name}_OUTPUT"
    
    # Find input links for this stage
    input_links = []
    for record in all_records:
        if record.get("Name") in connections.get(stage_name, []):
            # Find links that come from this source stage
            source_links = [link for link, src in link_source_map.items() if src == record.get("Name")]
            input_links.extend(source_links)
    
    # Basic join logic
    records.append({
        "Target_Table": target_link,
        "Target_Field": "MULTIPLE",
        "Stage_Type": "Join",
        "Stage": stage_name,
        "Source_Table": ", ".join(input_links) if input_links else ", ".join(connections.get(stage_name, [])),
        "Source_Field": "MULTIPLE",
        "Transformation_Logic": "Join operation",
        "Expression": "Join operation",
        "Cardinality": "N->M",
        "Transformation_Type": "join",
        "Notes": f"Join stage: {stage_name}"
    })
    
    return records


def analyze_lookup_stage(stage_info: Dict[str, Any], connections: Dict[str, List[str]], link_source_map: Dict[str, str] = None) -> List[Dict[str, str]]:
    """Analyze a lookup stage and extract column lineage."""
    
    records = []
    stage_name = stage_info["name"]
    link_source_map = link_source_map or {}
    
    # Find output links for this stage
    output_links = [link for link, source_stage in link_source_map.items() if source_stage == stage_name]
    target_link = output_links[0] if output_links else f"{stage_name}_OUTPUT"
    
    # Find input links for this stage
    input_links = []
    for source_stage in connections.get(stage_name, []):
        source_links = [link for link, src in link_source_map.items() if src == source_stage]
        input_links.extend(source_links)
    
    records.append({
        "Target_Table": target_link,
        "Target_Field": "MULTIPLE",
        "Stage_Type": "Lookup",
        "Stage": stage_name,
        "Source_Table": ", ".join(input_links) if input_links else ", ".join(connections.get(stage_name, [])),
        "Source_Field": "MULTIPLE",
        "Transformation_Logic": "Lookup operation",
        "Expression": "Lookup operation",
        "Cardinality": "N->1",
        "Transformation_Type": "lookup",
        "Notes": f"Lookup stage: {stage_name}"
    })
    
    return records


def analyze_aggregator_stage(stage_info: Dict[str, Any], connections: Dict[str, List[str]], all_records: List[Dict[str, Any]], link_source_map: Dict[str, str] = None) -> List[Dict[str, str]]:
    """Analyze an aggregator stage and extract column lineage."""
    
    records = []
    stage_name = stage_info["name"]
    link_source_map = link_source_map or {}
    
    # Find output links for this stage
    output_links = [link for link, source_stage in link_source_map.items() if source_stage == stage_name]
    target_link = output_links[0] if output_links else f"{stage_name}_OUTPUT"
    
    # Find input links for this stage
    input_links = []
    for source_stage in connections.get(stage_name, []):
        source_links = [link for link, src in link_source_map.items() if src == source_stage]
        input_links.extend(source_links)
    
    records.append({
        "Target_Table": target_link,
        "Target_Field": "MULTIPLE",
        "Stage_Type": "Aggregator/RemDup",
        "Stage": stage_name,
        "Source_Table": ", ".join(input_links) if input_links else ", ".join(connections.get(stage_name, [])),
        "Source_Field": "MULTIPLE",
        "Transformation_Logic": "Aggregation/Deduplication",
        "Expression": "Aggregation/Deduplication",
        "Cardinality": "N->M",
        "Transformation_Type": "aggregation",
        "Notes": f"Aggregator/RemDup stage: {stage_name}"
    })
    
    return records


def print_analysis(lineage_records: List[Dict[str, str]], stages_info: Dict[str, Any]):
    """Print a formatted analysis to console."""
    
    print("\n" + "="*80)
    print("DSX FILE ANALYSIS REPORT")
    print("="*80)
    
    print("\n📊 STAGES FOUND:")
    print("-" * 80)
    for stage_name, stage_info in stages_info.items():
        print(f"  • {stage_name}")
        print(f"    Type: {stage_info['type']}")
        print(f"    OLE Type: {stage_info['ole_type']}")
    
    print(f"\n📋 TOTAL STAGES: {len(stages_info)}")
    print(f"📋 TOTAL LINEAGE RECORDS: {len(lineage_records)}")
    
    print("\n" + "="*80)
    print("COLUMN LINEAGE MAPPING")
    print("="*80)
    
    # Group by target table
    by_target = {}
    for record in lineage_records:
        target = record["Target_Table"]
        if target not in by_target:
            by_target[target] = []
        by_target[target].append(record)
    
    for target_table, records in by_target.items():
        print(f"\n🎯 TARGET: {target_table}")
        print("-" * 80)
        
        for record in records:
            print(f"\n  Field: {record['Target_Field']}")
            print(f"    ← Source: {record['Source_Table']}.{record['Source_Field']}")
            print(f"    Logic: {record['Transformation_Logic']}")
            print(f"    Type: {record['Transformation_Type']} ({record['Cardinality']})")
            if record['Notes']:
                print(f"    Notes: {record['Notes']}")


def save_to_csv(lineage_records: List[Dict[str, str]], output_file: str):
    """Save lineage records to CSV file."""
    
    if not lineage_records:
        print("No lineage records to save.")
        return
    
    fieldnames = [
        "Target_Table",
        "Target_Field",
        "Stage_Type",
        "Stage",
        "Source_Table",
        "Source_Field",
        "Transformation_Logic",
        "Expression",
        "Cardinality",
        "Transformation_Type",
        "Notes"
    ]
    
    with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(lineage_records)
    
    print(f"\n✅ Lineage records saved to: {output_file}")


def main():
    """Main execution function."""
    
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Analyze DataStage DSX files and extract column lineage',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic analysis
  python analyze_dsx_lineage.py
  
  # With LLM-enhanced descriptions
  python analyze_dsx_lineage.py --use-llm
  
  # With custom batch size
  python analyze_dsx_lineage.py --use-llm --llm-batch-size 30
"""
    )
    
    parser.add_argument(
        '--use-llm',
        action='store_true',
        help='Use OpenAI GPT-4o to generate contextual transformation descriptions'
    )
    
    parser.add_argument(
        '--llm-batch-size',
        type=int,
        default=50,
        help='Number of transformations to process per API call (default: 50)'
    )
    
    parser.add_argument(
        '--estimate-cost',
        action='store_true',
        help='Estimate LLM API cost before processing'
    )
    
    parser.add_argument(
        '--dsx-file',
        type=str,
        default="samples/BNCMRXALLInsSTGTransactionActual.dsx",
        help='Path to DSX file to analyze'
    )
    
    args = parser.parse_args()
    
    # DSX file to analyze
    dsx_file = args.dsx_file
    
    if not Path(dsx_file).exists():
        print(f"❌ Error: DSX file not found: {dsx_file}")
        print(f"Please ensure the file exists.")
        sys.exit(1)
    
    print(f"🔍 Analyzing DSX file: {dsx_file}")
    if args.use_llm:
        print("🤖 LLM enhancement: ENABLED (OpenAI GPT-4o)")
    print("This may take a moment...\n")
    
    try:
        # Parse DSX file
        print("📖 Step 1: Parsing DSX file...")
        parsed_data = parse_dsx(dsx_file)
        print("   ✓ Parsing complete")
        
        # Extract stages
        print("📖 Step 2: Extracting stage information...")
        stages_info = extract_stages_info(parsed_data)
        print(f"   ✓ Found {len(stages_info)} stages")
        
        # Analyze lineage
        print("📖 Step 3: Analyzing column lineage...")
        lineage_records = analyze_column_lineage(stages_info, parsed_data)
        print(f"   ✓ Extracted {len(lineage_records)} lineage mappings")
        
        # Enhance with LLM if requested
        if args.use_llm:
            print("\n📖 Step 4: Enhancing with LLM (OpenAI GPT-4o)...")
            
            try:
                from llm_enhancer import TransformationEnhancer
                
                # Check for API key
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    print("\n⚠️  WARNING: OPENAI_API_KEY not found in environment.")
                    print("   Set it with: export OPENAI_API_KEY='your-key-here'")
                    print("   Or create a .env file with: OPENAI_API_KEY=your-key-here")
                    print("\n   Continuing without LLM enhancement...\n")
                else:
                    enhancer = TransformationEnhancer()
                    
                    # Estimate cost if requested
                    if args.estimate_cost or len(lineage_records) > 100:
                        cost_info = enhancer.estimate_cost(len(lineage_records), args.llm_batch_size)
                        print(f"\n   💰 Cost Estimate:")
                        print(f"      Transformations: {cost_info['num_transformations']}")
                        print(f"      API Calls: {cost_info['num_api_calls']}")
                        print(f"      Estimated Cost: ${cost_info['estimated_cost_usd']:.2f} USD")
                        
                        if args.estimate_cost:
                            response = input("\n   Proceed with LLM enhancement? (y/n): ")
                            if response.lower() != 'y':
                                print("   Skipping LLM enhancement...")
                                args.use_llm = False
                    
                    if args.use_llm:
                        # Batch enhance all transformations
                        enhanced_descriptions = enhancer.batch_enhance(
                            lineage_records,
                            batch_size=args.llm_batch_size
                        )
                        
                        # Update lineage records with enhanced descriptions
                        if len(enhanced_descriptions) != len(lineage_records):
                            print(f"⚠️  Warning: Mismatch in descriptions. Records: {len(lineage_records)}, Enhanced: {len(enhanced_descriptions)}")
                        
                        for i, (record, enhanced_desc) in enumerate(zip(lineage_records, enhanced_descriptions)):
                            record['Transformation_Logic'] = enhanced_desc
                            if i == 0:
                                print(f"DEBUG: Updated record 0 in main. New logic: {enhanced_desc[:50]}...")
                        
                        print(f"   ✓ Enhanced {len(lineage_records)} transformation descriptions")
            
            except ImportError:
                print("\n⚠️  LLM enhancer not available. Install dependencies:")
                print("   pip install -r requirements.txt")
                print("\n   Continuing without LLM enhancement...\n")
            except Exception as e:
                print(f"\n⚠️  Error during LLM enhancement: {e}")
                print("   Continuing with basic descriptions...\n")
        
        # Print analysis
        print_analysis(lineage_records, stages_info)
        
        # Save to CSV
        output_csv = "dsx_lineage_analysis.csv"
        if args.use_llm:
            output_csv = "dsx_lineage_analysis_llm_enhanced.csv"
        
        save_to_csv(lineage_records, output_csv)
        
        print("\n" + "="*80)
        print("✨ ANALYSIS COMPLETE!")
        if args.use_llm:
            print("🤖 LLM-enhanced descriptions saved!")
        print("="*80)
        
    except Exception as e:
        print(f"\n❌ Error during analysis: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
