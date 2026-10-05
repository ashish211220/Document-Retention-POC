import re

def clean_docstring(filepath, short_text):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Replace the very first multi-line string in the file
    new_content = re.sub(r'\"\"\"[\s\S]*?\"\"\"', f'"""\n{short_text}\n"""', content, count=1)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)

clean_docstring('app/sync/sharepoint_sync.py', 'SharePoint Sync Module\nPushes a completed classification result out to SharePoint.')
clean_docstring('app/services/classification_service.py', 'Classification Service\nOrchestrates the full document classification pipeline.')
