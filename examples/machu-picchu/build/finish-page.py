"""Set the title, frame and credits on the page `likeness web` made.    python finish-page.py <site dir>"""
from pathlib import Path
import sys

page = Path(sys.argv[1]) / "index.html"
html = page.read_text(encoding="utf-8")
if '<details id="credit">' in html:
    raise SystemExit(0)
html = html.replace("<title>Walk</title>", "<title>Machu Picchu</title>")
html = html.replace('quality: Number(query.get("width")) || 0,',
    'quality: Number(query.get("width")) || (data.video.some(v => v.width === 1920) ? 1920 : 0),')
html = html.replace('<canvas id="film" tabindex="0">', '<canvas id="film" tabindex="0" aria-label="Machu Picchu">')
html = html.replace('</style>', '''
  body { background: #141611; }
  #stage { width: min(100vw, 177.77778dvh); height: auto; aspect-ratio: 16/9;
    inset: 50% auto auto 50%; transform: translate(-50%, -50%); overflow: hidden; }
  #credit { position: fixed; z-index: 3; left: 14px; bottom: 12px; font-size: 12px; }
  #credit summary { cursor: pointer; color: #eee9de; text-shadow: 0 1px 4px #171511; }
  #credit[open] { padding: 12px; max-width: min(32em, 75vw); background: #23231eee; }
  #credit p { margin: 8px 0 0; line-height: 1.55; } #credit a { color: inherit; }
</style>''')
html = html.replace('<pre id="debug">', '''<details id="credit"><summary>署名 / Credits</summary>
  <p><a href="https://commons.wikimedia.org/wiki/File:80_-_Machu_Picchu_-_Juin_2009_-_edit.2.jpg">Martin St-Amant, Wikipedia</a>,
  <a href="https://creativecommons.org/licenses/by-sa/3.0/">CC BY-SA 3.0</a> (adapted mountain and city relief).</p>
  <p><a href="https://www.flickr.com/photos/beggs/49472067753/">Brian Jeffery Beggerly</a> (valley composite),
  <a href="https://www.flickr.com/photos/mckaysavage/7183500885/">McKay Savage</a> (gate texture),
  <a href="https://creativecommons.org/licenses/by/2.0/">CC BY 2.0</a>.</p>
  <p>© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>,
  <a href="https://registry.opendata.aws/terrain-tiles/">Terrain Tiles</a>,
  <a href="https://polyhaven.com/">Poly Haven, CC0</a>.</p></details>
<pre id="debug">''')
page.write_text(html, encoding="utf-8")
