from pathlib import Path
p = Path('polarsteps_pdf_generator.py')
line = p.read_text('utf-8').splitlines()[4293]
old = 'border-color:" + color + ";\\\\\\">" +'
print('old repr:', repr(old))
print('contains:', old in line)
print('line repr:', repr(line[line.index('border-color:'):line.index('border-color:')+40]))
