from pathlib import Path
from PyPDF2 import PdfReader

p=Path('TripPdfs/Hochlitten Weihnachten .pdf')
if not p.exists():
    print('PDF not found:', p)
    raise SystemExit(1)
reader=PdfReader(str(p))
text='\n'.join(page.extract_text() or '' for page in reader.pages)
# print some stats
print('Pages:', len(reader.pages))
# find sample around certain trip-related terms
for s in ['Säntis','Hochlitten','Herz','Stern']:
    idx = text.find(s)
    if idx!=-1:
        print('\n--- sample around',s,'---')
        print(text[max(0,idx-60):idx+60])
# show words with colons (aliases)
aliases = [w for w in text.split() if w.startswith(':') and w.endswith(':')]
print('\nAliases found:', alias:=aliases[:20])
print('\nTotal text length:', len(text))
# write full text to file
out_path = Path('temp') / 'tmp_pdf_text.txt'
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(text)
print(f"\nWrote {out_path}")
