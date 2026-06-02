import json
import os
import sys
from collections import defaultdict

def analyze_component(node, path, stats, hierarchy, depth=0):
    """
    Recursively analyzes the JSON structure.
    """
    current_type = "Unknown"
    name = "Unknown"
    
    if isinstance(node, dict):
        # Determine type
        if '__type__' in node:
            current_type = node['__type__']
        elif 'JobVersion' in node: # Root of DSX often has this
            current_type = "DSJob"
        
        # Determine Name
        if 'Name' in node:
            name = node['Name']
        elif 'JobName' in node:
            name = node['JobName']
        elif 'StageName' in node:
            name = node['StageName']
        elif 'Identifier' in node:
            name = node['Identifier']
            
        # Record stats
        stats[current_type] += 1
        
        # Record hierarchy
        if len(path) > 0:
            parent_type = path[-1]
            hierarchy[parent_type].add(current_type)
            
        # Recurse into children
        # If it has __children__, use that (from our parser)
        if '__children__' in node:
            for child in node['__children__']:
                analyze_component(child, path + [current_type], stats, hierarchy, depth + 1)
        else:
            # Fallback for standard dict traversal if __children__ isn't used exclusively
            for key, value in node.items():
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                             analyze_component(item, path + [current_type], stats, hierarchy, depth + 1)
                elif isinstance(value, dict):
                    analyze_component(value, path + [current_type], stats, hierarchy, depth + 1)

def print_tree(hierarchy, root="DSJob", indent=0, visited=None):
    if visited is None:
        visited = set()
    
    if root in visited:
        return
    visited.add(root)
    
    print("  " * indent + f"- {root}")
    children = sorted(list(hierarchy.get(root, [])))
    for child in children:
        print_tree(hierarchy, child, indent + 1, visited.copy())

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_structure.py <json_file>")
        sys.exit(1)
        
    file_path = sys.argv[1]
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        sys.exit(1)
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        stats = defaultdict(int)
        hierarchy = defaultdict(set)
        
        print(f"Analyzing {file_path}...")
        analyze_component(data, [], stats, hierarchy)
        
        print("\nComponent Counts:")
        for comp_type, count in sorted(stats.items(), key=lambda x: x[1], reverse=True):
            print(f"  {comp_type}: {count}")
            
        print("\nComponent Hierarchy:")
        # Find potential roots (types that are never children)
        all_children = set()
        for children in hierarchy.values():
            all_children.update(children)
        
        roots = [k for k in stats.keys() if k not in all_children]
        
        for root in roots:
            print_tree(hierarchy, root)
            
    except Exception as e:
        print(f"Error analyzing {file_path}: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
