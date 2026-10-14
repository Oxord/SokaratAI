"""Assemble pitch v10: inject base64 media (logo video, team photos) from v9 into the template.

Run from the repo root:
    python pitch/src/build_v10.py pitch/sokrat_pitch_v9.html pitch/src/v10_template.html pitch/sokrat_pitch_v10.html

Edit pitch/src/v10_template.html, never the built pitch/sokrat_pitch_v10.html.
"""
import re, sys
v9_path, tpl_path, out_path = sys.argv[1:4]
v9 = open(v9_path, encoding='utf-8').read()
tpl = open(tpl_path, encoding='utf-8').read()

video = re.search(r'<source src="(data:video/mp4;base64,[^"]+)"', v9).group(1)
photos = dict(re.findall(r'<img src="(data:image/jpeg;base64,[^"]+)" alt="([^"]+)"', v9)[i][::-1] for i in range(4))
# Фото из pitch/src/photo_<name>.jpg (если есть) заменяют фото из v9.
import base64, os
_src_dir = os.path.dirname(os.path.abspath(__file__))
for _key, _name in (('Иван', 'ivan'), ('Илья', 'ilya'), ('Никита', 'nikita')):
    _path = os.path.join(_src_dir, f'photo_{_name}.jpg')
    if os.path.exists(_path):
        photos[_key] = 'data:image/jpeg;base64,' + base64.b64encode(open(_path, 'rb').read()).decode('ascii')
subs = {
    '{{LOGO_VIDEO}}': video,
    '{{PHOTO_IVAN}}': photos['Иван'],
    '{{PHOTO_ILYA}}': photos['Илья'],
    '{{PHOTO_NIKITA}}': photos['Никита'],
}
for k, v in subs.items():
    if k not in tpl:
        sys.exit(f'marker missing: {k}')
    tpl = tpl.replace(k, v)
if '{{' in tpl:
    sys.exit('unreplaced marker left')
open(out_path, 'w', encoding='utf-8', newline='\n').write(tpl)
print('ok', len(tpl))
