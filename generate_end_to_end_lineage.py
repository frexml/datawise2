import sys
from collections import defaultdict
try:
    from openpyxl import Workbook
except ImportError:
    print("Error: openpyxl not installed. Run: pip install openpyxl")
    sys.exit(1)

sys.path.append('backend/app/agents/lib')
from dsx_parser import parse_dsx
from partner_extractor import extract_partner_connections

def extract_table_name(stage):
    """Extract table name from stage properties."""
    if isinstance(stage, str) or not stage:
        return stage or ''
    props = stage.get('properties', {})
    if isinstance(props, dict):
        return props.get('TableName') or props.get('table_name') or props.get('OutputTable') or stage.get('name', '')
    return stage.get('name', '')

def extract_columns_from_stage(raw_data, stage_id, is_final_stage=False):
    """Extract column names from stage's pins."""
    columns = []
    for job in raw_data.get('DSJOB', []):
        for record in job.get('DSRECORD', []):
            rec_id = record.get('Identifier', '')
            ole_type = record.get('OLEType', '')
            if rec_id.startswith(stage_id) and ('Pin' in ole_type or 'Input' in ole_type or 'Output' in ole_type):
                # For final stages, use input pins; for source stages, use output pins
                if is_final_stage and 'Input' not in ole_type:
                    continue
                if not is_final_stage and 'Output' not in ole_type:
                    continue
                for subrecord in record.get('DSSUBRECORD', []):
                    col_name = subrecord.get('Name', '')
                    if col_name and col_name not in ['dataset', 'datasetmode', 'RTColumnProp']:
                        columns.append(col_name)
                if columns:
                    return columns
    return columns

def generate_complete_lineage(dsx_file, output_csv):
    """Generate complete end-to-end lineage for all fields."""
    raw_data = parse_dsx(dsx_file)
    data = extract_partner_connections(raw_data)
    
    stage_names = data.get('stage_map', {})
    edges = data.get('edges', [])
    partners = data.get('partners', {})
    
    # Build stage details from raw data - index by both ID and name
    stages = {}
    for job in raw_data.get('DSJOB', []):
        for record in job.get('DSRECORD', []):
            stage_id = record.get('Identifier', '')
            stage_name = stage_names.get(stage_id, stage_id)
            stage_type = record.get('OLEType', '')
            stage_info = {
                'id': stage_id,
                'name': stage_name,
                'type': stage_type,
                'properties': record
            }
            stages[stage_id] = stage_info
            stages[stage_name] = stage_info  # Also index by name
    
    print(f"Found {len(stages)} stages, {len(edges)} edges, {len(partners)} partners")
    
    if not stages:
        print("Warning: No stages found in raw data")
        return
    
    # Find final stages (stages with no outgoing edges)
    stage_has_output = set()
    for edge in edges:
        stage_has_output.add(edge.get('source', ''))
    
    final_stages = set()
    for edge in edges:
        target = edge.get('target', '')
        if target and target not in stage_has_output:
            final_stages.add(target)
    
    print(f"Found {len(final_stages)} final stages: {list(final_stages)[:5]}...")
    
    # Build pin-based graph for path tracing
    pin_graph = defaultdict(list)
    pin_to_stage = {}
    for edge in edges:
        source_stage = edge.get('source', '')
        target_stage = edge.get('target', '')
        source_pin = edge.get('source_pin', '')
        target_pin = edge.get('target_pin', '')
        if source_stage and target_stage:
            pin_graph[target_stage].append((source_stage, source_pin, target_pin))
            pin_to_stage[source_pin] = source_stage
            pin_to_stage[target_pin] = target_stage
    
    # Trace back from final stages to all sources
    all_lineages = []
    
    def trace_sources(stage, visited=None):
        if visited is None:
            visited = set()
        if stage in visited:
            return []
        visited.add(stage)
        
        sources = []
        if stage not in pin_graph:
            # This is a source stage
            return [(stage, [stage])]
        
        for source_stage, source_pin, target_pin in pin_graph[stage]:
            upstream = trace_sources(source_stage, visited.copy())
            for src, path in upstream:
                # Build path with pins: stage[pin] -> stage[pin]
                new_path = path[:-1] + [f"{path[-1]}[{source_pin}]", f"{stage}[{target_pin}]"]
                sources.append((src, new_path))
        
        return sources
    
    for final_stage_name in final_stages:
        final_stage = stages.get(final_stage_name, {})
        # Get columns from the stage that feeds into this final stage
        final_columns = []
        for edge in edges:
            if edge.get('target') == final_stage_name:
                source_feeding_final = stages.get(edge.get('source'), {})
                final_columns = extract_columns_from_stage(raw_data, source_feeding_final.get('id', ''), is_final_stage=False)
                if final_columns:
                    break
        sources = trace_sources(final_stage_name)
        
        for source_stage_name, path in sources:
            source_stage = stages.get(source_stage_name, {})
            source_columns = extract_columns_from_stage(raw_data, source_stage.get('id', ''), is_final_stage=False)
            
            # If columns found, create entries for each column
            if final_columns:
                for final_col in final_columns:
                    lineage = {
                        'final_stage': final_stage.get('name', ''),
                        'final_field': final_col,
                        'source_stage': source_stage.get('name', ''),
                        'source_field': ', '.join(source_columns) if source_columns else '',
                        'stage_path': ' -> '.join(path)
                    }
                    all_lineages.append(lineage)
            else:
                # Fallback if no columns
                lineage = {
                    'final_stage': final_stage.get('name', ''),
                    'final_field': '',
                    'source_stage': source_stage.get('name', ''),
                    'source_field': '',
                    'stage_path': ' -> '.join(path)
                }
                all_lineages.append(lineage)
    
    # Aggregate by final stage
    aggregated = {}
    for lin in all_lineages:
        key = (lin['final_stage'], lin['final_field'])
        if key not in aggregated:
            aggregated[key] = {
                'final_stage': lin['final_stage'],
                'final_field': lin['final_field'],
                'source_stages': [],
                'source_fields': [],
                'stage_paths': []
            }
        
        if lin['source_stage'] and lin['source_stage'] not in aggregated[key]['source_stages']:
            aggregated[key]['source_stages'].append(lin['source_stage'])
        if lin['source_field'] and lin['source_field'] not in aggregated[key]['source_fields']:
            aggregated[key]['source_fields'].append(lin['source_field'])
        if lin['stage_path'] and lin['stage_path'] not in aggregated[key]['stage_paths']:
            aggregated[key]['stage_paths'].append(lin['stage_path'])
    
    # Convert lists to comma-separated strings
    final_lineages = []
    for agg in aggregated.values():
        final_lineages.append({
            'final_stage': agg['final_stage'],
            'final_stage_columns': agg['final_field'],
            'source_stages': ', '.join(agg['source_stages']),
            'source_stage_columns': ', '.join(agg['source_fields']),
            'stage_path': '\n'.join(agg['stage_paths'])
        })
    
    # Write to XLSX
    if final_lineages:
        wb = Workbook()
        ws = wb.active
        ws.title = "Lineage"
        
        # Write header
        headers = ['final_stage', 'final_stage_columns', 'source_stages', 'source_stage_columns', 'stage_path']
        ws.append(headers)
        
        # Write data
        for lin in final_lineages:
            ws.append([lin['final_stage'], lin['final_stage_columns'], lin['source_stages'], lin['source_stage_columns'], lin['stage_path']])
        
        wb.save(output_csv)
        print(f"Generated lineage with {len(final_lineages)} aggregated records -> {output_csv}")
    else:
        print("No lineage data found")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_end_to_end_lineage.py <dsx_file> [output_name]")
        sys.exit(1)
    
    dsx_file = sys.argv[1]
    if len(sys.argv) > 2:
        output_csv = sys.argv[2] + '.xlsx'
    else:
        output_csv = dsx_file.replace('.dsx', '_complete_lineage.xlsx')
    
    generate_complete_lineage(dsx_file, output_csv)
