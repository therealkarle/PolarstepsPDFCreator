from pathlib import Path
p = Path('polarsteps_pdf_generator.py')
line = p.read_text('utf-8').splitlines()[4293]
idx = line.index('border-color:')
for l in range(idx, idx+40):
    print(l-idx, line[l])
print('slice repr:', repr(line[idx:idx+25]))
