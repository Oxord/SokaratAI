"""Собирает самодостаточную папку питча для флешки: HTML + assets + локальные шрифты.

    .venv/bin/python pitch/src/package_offline.py pitch/sokrat_pitch_v11.html <папка_назначения>

Шрифты Google Fonts скачиваются один раз и кладутся в fonts/, чтобы питч выглядел
правильно без интернета. Видео и картинки берутся из pitch/assets/.
"""
import hashlib
import re
import shutil
import sys
from pathlib import Path

import requests  # есть в .venv проекта; сертификаты берёт из certifi

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def fetch(url: str) -> bytes:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return resp.content


def main(html_path: str, out_dir: str) -> None:
    src = Path(html_path)
    out = Path(out_dir)
    html = src.read_text(encoding="utf-8")
    assets_src = src.parent / "assets"

    out.mkdir(parents=True, exist_ok=True)
    (out / "assets").mkdir(exist_ok=True)
    needed = sorted(set(re.findall(r'src="assets/([^"]+)"', html)))
    for name in needed:
        shutil.copy2(assets_src / name, out / "assets" / name)
    print("assets:", ", ".join(needed))

    # Шрифты → локально
    link = re.search(r'<link href="(https://fonts\.googleapis\.com/css2[^"]+)" rel="stylesheet">', html)
    if link:
        css = fetch(link.group(1)).decode("utf-8")
        fonts_dir = out / "fonts"
        fonts_dir.mkdir(exist_ok=True)
        for url in sorted(set(re.findall(r"url\((https://fonts\.gstatic\.com/[^)]+)\)", css))):
            name = hashlib.sha1(url.encode()).hexdigest()[:12] + ".woff2"
            (fonts_dir / name).write_bytes(fetch(url))
            css = css.replace(url, name)
        (fonts_dir / "fonts.css").write_text(css, encoding="utf-8")
        html = html.replace(link.group(0), '<link href="fonts/fonts.css" rel="stylesheet">')
        html = re.sub(r'<link rel="preconnect"[^>]*>\n?\s*', "", html)
        print("шрифты:", len(list(fonts_dir.glob("*.woff2"))), "файлов")

    (out / "Sokrat_pitch.html").write_text(html, encoding="utf-8")
    print("готово:", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
