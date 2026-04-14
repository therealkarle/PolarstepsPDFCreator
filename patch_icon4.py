from pathlib import Path
p = Path('polarsteps_pdf_generator.py')
text = p.read_text('utf-8')
old = 'border-color:" + color + ";\\\">" +'
new = 'border-color:" + mutedRouteColor + ";\\\">" +'
text2 = text.replace(old, new)
p.write_text(text2, 'utf-8')
print('changed', text != text2)
