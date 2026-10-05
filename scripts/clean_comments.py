import os
import glob
import re

def clean_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    new_lines = []
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Only target lines that start with '#' (actual comments, not docstrings)
        if stripped.startswith('#') and not stripped.startswith('# noqa') and not stripped.startswith('# type:'):
            
            # Remove dashed decorative lines completely
            if '---' in stripped or '===' in stripped:
                continue
                
            # Check if the next line is a function or class definition
            if i + 1 < len(lines):
                next_stripped = lines[i+1].strip()
                if next_stripped.startswith('def ') or next_stripped.startswith('async def ') or next_stripped.startswith('class '):
                    # We keep this single comment line as a minimal description
                    new_lines.append(line)
                    continue
            
            # Otherwise, skip/remove this comment line!
            continue
            
        new_lines.append(line)
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

if __name__ == '__main__':
    for filepath in glob.glob('app/**/*.py', recursive=True):
        clean_file(filepath)
    print("Safely cleaned all # comments from app/ directory.")
