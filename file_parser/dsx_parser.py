import re
import sys

def parse_dsx(file_path):
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

    # Skip header until separator
    start_index = 0
    for i, line in enumerate(lines):
        if line.strip() == '=+=+=+=':
            start_index = i + 1
            break
    
    content_lines = lines[start_index:]
    
    root = {}
    stack = [root]
    current_key = None
    multiline_buffer = []
    in_multiline = False
    
    # Regex for Key "Value"
    # Handles: Key "Value", Key =+=+=+= (multiline start), Key (no value)
    # But DSX structure is often:
    # Key "Value"
    # BEGIN DSSUBRECORD
    #   Name "..."
    # END DSSUBRECORD
    
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
        # Sometimes it looks like: JobControlCode =+=+=+=
        if line.endswith('=+=+=+='):
            key = line.replace('=+=+=+=', '').strip()
            # Read until next =+=+=+=
            buffer = []
            i += 1
            while i < len(content_lines):
                sub_line = content_lines[i] # Keep original formatting/indentation
                if sub_line.strip() == '=+=+=+=':
                    break
                buffer.append(sub_line)
                i += 1
            
            stack[-1][key] = ''.join(buffer)
            i += 1
            continue

        # Standard Key "Value" or Key Value
        # We need to be careful about splitting.
        # Examples:
        # JobVersion "56.0.0"
        # ControlAfterSubr "0"
        # Parameters "CParameters"
        
        # Regex to capture Key and optional Quoted Value
        match = re.match(r'^([^"]+)\s+"(.*)"$', line)
        if match:
            key = match.group(1).strip()
            value = match.group(2)
            stack[-1][key] = value
            i += 1
            continue
            
        # Try unquoted value
        # e.g. ParamType "13" is caught above.
        # e.g. SomeKey SomeValue
        parts = line.split(None, 1)
        if len(parts) == 2:
            key = parts[0]
            value = parts[1]
            stack[-1][key] = value
        else:
            # Just a key? or weird line
            stack[-1][line] = None
            
        i += 1

    return root
