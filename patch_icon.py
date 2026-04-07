from pathlib import Path
p = Path('polarsteps_pdf_generator.py')
t = p.read_text('utf-8')
old = 'color + ";\\">\" +'
new = 'mutedRouteColor + ";\\">\" +'
t2 = t.replace(old, new)
p.write_text(t2, 'utf-8')
print('changed', t != t2)
