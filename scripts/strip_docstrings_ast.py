import ast
import glob
import sys

def remove_docstrings(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        source = f.read()

    try:
        tree = ast.parse(source)
    except Exception as e:
        print(f"Error parsing {filepath}: {e}")
        return

    # Collect the line number ranges of all docstrings EXCEPT the module-level one
    docstring_ranges = []
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node):
                doc_node = node.body[0]
                # ast nodes have lineno (1-indexed) and end_lineno
                start = doc_node.lineno
                end = doc_node.end_lineno
                docstring_ranges.append((start, end))

    if not docstring_ranges:
        return

    # Sort in reverse order so deleting lines from the bottom doesn't shift the top line numbers
    docstring_ranges.sort(key=lambda x: x[0], reverse=True)
    
    lines = source.splitlines()
    for start, end in docstring_ranges:
        # Check if the block is just a single line, if so the user might want to keep it?
        # The user said "remove all commands" (comments). Let's remove them all.
        
        # If removing this leaves the function empty, insert 'pass'
        # But to be safe with indentation, let's just replace the lines with '    pass' or similar?
        # Actually, if we just delete the lines, and the function is empty, it causes a SyntaxError.
        # But this codebase has no empty functions (they all have real code).
        # We can just delete the lines.
        del lines[start-1:end]

    new_source = "\n".join(lines) + "\n"
    
    # Simple check to ensure we didn't break syntax
    try:
        ast.parse(new_source)
    except SyntaxError:
        print(f"Skipping {filepath} because removing docstrings caused a SyntaxError (likely an empty function).")
        return
        
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_source)
    print(f"Cleaned {filepath}")

for f in glob.glob('app/**/*.py', recursive=True):
    # Don't touch openai.py or search.py or config.py just in case, only core logic
    if "sync\\" in f or "services\\" in f or "api\\" in f:
        remove_docstrings(f)
