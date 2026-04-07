from pathlib import Path
p = Path('polarsteps_pdf_generator.py')
line = p.read_text('utf-8').splitlines()[4293]
idx = line.index('border-color:')
s = line[idx:idx+22]
print('line slice repr:', repr(s))
print('line slice:', s)
print('len:', len(s))
