from pathlib import Path
p = Path('polarsteps_pdf_generator.py')
line = p.read_text('utf-8').splitlines()[4293]
actual = line[line.index('border-color:'):line.index('border-color:')+23+5]
# actual substring around closing sequence
print('actual repr:', repr(actual))
for s in [
    'border-color:" + color + ";\\\">" +',
    r'border-color:" + color + ";\">" +',
    r'border-color:" + color + ";\">" +',
    'border-color:" + color + ";\\\">" +',
]:
    print('test repr:', repr(s), 'contains?', s in line)
