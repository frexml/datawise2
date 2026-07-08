"""
End-to-End Lineage Analysis Service
"""
from typing import List, Dict, Any
from dsxlineage.db import models
import json

def extract_stage_transformations(stages: List[models.Stage]) -> Dict[str, List[Dict[str, Any]]]:
    """Extract transformation details from stage properties"""
    stage_transformations = {}
    
    for stage in stages:
        if not stage.properties:
            continue
            
        # Get llm_explanation which contains transformation details
        llm_explanation = stage.llm_explanation or ""
        
        # Parse derivations from properties if available
        derivations = []
        
        # Check if stage has transformation data in properties
        if isinstance(stage.properties, dict):
            # Look for output columns with derivations
            output_cols = stage.properties.get('output_columns', [])
            for col in output_cols:
                if isinstance(col, dict) and col.get('derivation'):
                    derivations.append({
                        'target_field': col.get('name', ''),
                        'expression': col.get('derivation', ''),
                        'source_field': extract_source_from_expression(col.get('derivation', '')),
                        'transformation_logic': llm_explanation,
                        'transformation_type': determine_type_from_expression(col.get('derivation', '')),
                        'cardinality': determine_cardinality_from_expression(col.get('derivation', ''))
                    })
        
        if derivations:
            stage_transformations[stage.name] = derivations
    
    return stage_transformations


def extract_source_from_expression(expression: str) -> str:
    """Extract source field from expression"""
    import re
    if not expression:
        return "NONE"
    
    # Match Link.Column pattern
    matches = re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b', expression)
    if matches:
        return ", ".join(list(set(matches)))
    return "MULTIPLE"


def determine_type_from_expression(expression: str) -> str:
    """Determine transformation type from expression"""
    if not expression:
        return "passthrough"
    
    import re
    if re.match(r'^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$', expression.strip()):
        return "direct"
    elif 'IF' in expression.upper() or 'ELSE' in expression.upper():
        return "conditional"
    elif '(' in expression:
        return "function"
    elif any(op in expression for op in ['+', '-', '*', '/', '%']):
        return "expression"
    elif expression.startswith("'") or expression.startswith('"'):
        return "constant"
    return "expression"


def determine_cardinality_from_expression(expression: str) -> str:
    """Determine cardinality from expression"""
    import re
    if not expression:
        return "1->1"
    
    matches = re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*)\b', expression)
    unique_refs = list(set(matches))
    
    return "N->1" if len(unique_refs) > 1 else "1->1"


def find_all_paths(stages: List[models.Stage], links: List[models.Link]) -> List[Dict[str, Any]]:
    """Find all end-to-end paths from source to target stages"""
    
    if not stages or not links:
        return []
    
    # Build stage name to stage map
    stage_by_name = {s.name: s for s in stages}
    
    # Build adjacency list using stage names
    graph = {}
    link_map = {}
    
    for link in links:
        src = link.source_stage
        tgt = link.target_stage
        if src and tgt and src != "Unknown" and tgt != "Unknown":
            if src not in graph:
                graph[src] = []
            if tgt not in graph[src]:  # Avoid duplicates
                graph[src].append(tgt)
            link_map[(src, tgt)] = link
    
    if not graph:
        print("No valid stage connections found")
        return []
    
    # Find source and target stages from graph
    all_stages = set()
    has_incoming = set()
    has_outgoing = set()
    
    for src, targets in graph.items():
        all_stages.add(src)
        has_outgoing.add(src)
        for tgt in targets:
            all_stages.add(tgt)
            has_incoming.add(tgt)
    
    source_stages = [s for s in all_stages if s not in has_incoming and s in has_outgoing]
    target_stages = [s for s in all_stages if s not in has_outgoing and s in has_incoming]
    
    print(f"Found {len(source_stages)} source stages and {len(target_stages)} target stages")
    
    if not source_stages or not target_stages:
        print("No source or target stages found - returning direct connections")
        # Return direct connections as lineage
        lineage_records = []
        for (src, tgt), link in link_map.items():
            lineage_records.append({
                "target_table": tgt,
                "target_field": tgt,
                "source_table": src,
                "source_field": src,
                "source_link": link.name if link else "",
                "target_link": link.name if link else "",
                "full_path": f"{src}->{tgt}",
                "total_hops": 2,
                "transformation_logic": "Direct connection",
                "transformation_explanation": "",
                "transformation_type": "",
                "cardinality": ""
            })
        return lineage_records
    
    # DFS to find all paths
    paths = []
    
    def dfs(current, target, path, visited):
        if current == target:
            paths.append(path[:])
            return
        
        if current in visited:
            return
        
        visited.add(current)
        
        for next_stage in graph.get(current, []):
            path.append(next_stage)
            dfs(next_stage, target, path, visited)
            path.pop()
        
        visited.remove(current)
    
    # Find paths
    for src in source_stages:
        for tgt in target_stages:
            dfs(src, tgt, [src], set())
    
    # Extract transformation details from stages
    stage_transformations = extract_stage_transformations(stages)
    
    # Convert to lineage records with transformation details
    lineage_records = []
    for path in paths:
        if len(path) < 2:
            continue
        
        # Get transformation details from intermediate stages
        all_transformations = []
        for stage_name in path[1:-1]:  # Exclude source and target
            if stage_name in stage_transformations:
                all_transformations.extend(stage_transformations[stage_name])
        
        # Use first transformation details if available
        if all_transformations:
            trans = all_transformations[0]
            transformation_logic = trans.get('transformation_logic', 'Multi-stage transformation')
            transformation_type = trans.get('transformation_type', 'expression')
            cardinality = trans.get('cardinality', '1->1')
            source_field = trans.get('source_field', path[0])
            target_field = trans.get('target_field', path[-1])
        else:
            transformation_logic = "Multi-stage transformation"
            transformation_type = "expression"
            cardinality = "1->1"
            source_field = path[0]
            target_field = path[-1]
        
        source_link = link_map.get((path[0], path[1])) if len(path) >= 2 else None
        target_link = link_map.get((path[-2], path[-1])) if len(path) >= 2 else None
        
        lineage_records.append({
            "target_table": path[-1],
            "target_field": target_field,
            "source_table": path[0],
            "source_field": source_field,
            "source_link": source_link.name if source_link else "",
            "target_link": target_link.name if target_link else "",
            "full_path": "->".join(path),
            "total_hops": len(path),
            "transformation_logic": transformation_logic,
            "transformation_explanation": transformation_logic,
            "transformation_type": transformation_type,
            "cardinality": cardinality
        })
    
    return lineage_records


def analyze_lineage_with_llm(lineage_record: Dict[str, Any]) -> Dict[str, Any]:
    """No-op function - transformation details already extracted from DSX"""
    return lineage_record
