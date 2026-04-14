from pathlib import Path
p = Path('polarsteps_pdf_generator.py')
line = p.read_text('utf-8').splitlines()[4293]
idx = line.index('border-color:')
print(repr(line[idx:idx+40]))
