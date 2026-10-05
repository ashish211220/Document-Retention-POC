import glob

for filepath in glob.glob('app/**/*.py', recursive=True):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    for i, line in enumerate(lines):
        if '"""' in line:
            print(f"{filepath}:{i+1}: {line.strip()}")
