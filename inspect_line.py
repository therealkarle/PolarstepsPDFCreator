from pathlib import Path
p = Path('polarsteps_pdf_generator.py')
lines = p.read_text('utf-8').splitlines()
print(lines[4293])
print(repr(lines[4293]))
