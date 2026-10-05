import os
from pathlib import Path

def generate_context():
    project_root = Path('.')
    output_file = Path('AI_CONTEXT.md')
    
    # Files and directories to include
    include_dirs = ['app', 'data']
    include_files = ['requirements.txt', 'README.md', '.env.example']
    
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("# Document Retention POC - AI Context File\n\n")
        f.write("## Current Status\n")
        f.write("This project is a FastAPI-based AI Document Retention system.\n")
        f.write("We have successfully completed **Phases 1 through 5**:\n")
        f.write("1. FastAPI + PDF Upload + Azure Document Intelligence\n")
        f.write("2. Retention Taxonomy & Metadata Service\n")
        f.write("3. Azure AI Search (Indexing & Hybrid Search)\n")
        f.write("4. Azure OpenAI (Document Understanding)\n")
        f.write("5. Classification, Validation & Confidence Scoring\n\n")
        f.write("### Next Goal: Phase 6 (PostgreSQL Integration)\n")
        f.write("The next step is to integrate PostgreSQL using SQLAlchemy (and asyncpg/psycopg2) to store document metadata, classification results, processing history, and audit logs.\n\n")
        
        f.write("## Project Structure\n```text\n")
        for root, dirs, files in os.walk(project_root):
            dirs[:] = [d for d in dirs if d not in ['__pycache__', '.venv', 'venv', 'env', '.git']]
            level = root.replace(str(project_root), '').count(os.sep)
            indent = ' ' * 4 * (level)
            f.write(f"{indent}{os.path.basename(root)}/\n")
            subindent = ' ' * 4 * (level + 1)
            for file in files:
                if file.endswith('.pyc') or file == 'AI_CONTEXT.md' or file == 'generate_context.py':
                    continue
                f.write(f"{subindent}{file}\n")
        f.write("```\n\n")
        
        f.write("## Codebase Files\n\n")
        
        # Process files
        all_paths_to_process = []
        for d in include_dirs:
            p = project_root / d
            if p.exists():
                for root, dirs, files in os.walk(p):
                    dirs[:] = [d for d in dirs if d not in ['__pycache__']]
                    for file in files:
                        if file.endswith(('.py', '.json', '.txt', '.md')):
                            all_paths_to_process.append(Path(root) / file)
                            
        for file in include_files:
            p = project_root / file
            if p.exists():
                all_paths_to_process.append(p)
                
        for file_path in all_paths_to_process:
            f.write(f"### `{file_path}`\n")
            ext = file_path.suffix.lstrip('.')
            if ext == '':
                ext = 'text'
            if ext == 'example':
                ext = 'env'
                
            f.write(f"```{ext}\n")
            try:
                with open(file_path, 'r', encoding='utf-8') as src:
                    f.write(src.read())
            except Exception as e:
                f.write(f"# Error reading file: {e}")
            f.write("\n```\n\n")

if __name__ == "__main__":
    generate_context()
    print("Context generated successfully in AI_CONTEXT.md")
