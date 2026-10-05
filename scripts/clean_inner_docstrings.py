import re
import glob

def clean_inner_docstrings(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    has_module_doc = content.startswith('"""')
    matches = list(re.finditer(r'([ \t]*)\"\"\"[\s\S]*?\"\"\"', content))
    
    new_content = ""
    last_end = 0
    for i, match in enumerate(matches):
        if i == 0 and has_module_doc and match.start() == 0:
            new_content += content[last_end:match.end()]
            last_end = match.end()
            continue
            
        indent = match.group(1)
        new_content += content[last_end:match.start()]
        # Replace the giant docstring with a minimal safe docstring
        new_content += f'{indent}"""."""'
        last_end = match.end()
        
    new_content += content[last_end:]
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)

for f in ['app/sync/scheduler.py', 'app/sync/sharepoint_sync.py', 'app/services/classification_service.py', 'app/services/document_understanding_service.py', 'app/services/document_intelligence_service.py']:
    clean_inner_docstrings(f)
