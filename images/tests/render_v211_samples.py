"""Local visual QA fixtures, no model or supplier request."""
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from amazon_image_brief.creative_planner import plan_modules
from amazon_image_brief.image_composer import create_german_composite
from amazon_image_brief.typography import poster_panel
from test_v211 import poster_project, concise
from amazon_image_brief.brief_generator import BriefGenerator


root = Path(__file__).resolve().parents[1] / 'qa_v211'
root.mkdir(exist_ok=True)
project = poster_project(root)
source = Image.new('RGB', (1200, 1200), '#F5F3ED')
draw = ImageDraw.Draw(source)
draw.rounded_rectangle((400, 240, 800, 1030), radius=55, fill='#397980', outline='#1F535A', width=10)
for x in range(445, 790, 65):
    draw.line((x, 270, x, 985), fill='#52929A', width=12)
draw.rounded_rectangle((510, 110, 690, 245), radius=15, outline='#1F3840', width=24)
for x in (445, 730):
    draw.ellipse((x-25, 1010, x+35, 1090), fill='#23333A')
source.save(root/'fixture.jpg', quality=95)
briefs = concise(BriefGenerator().generate(project))
plan = plan_modules(project)['detail1']
asset = source.crop((380, 90, 825, 1110))
main_canvas = Image.new('RGB', (1200, 1200), '#F5F3ED')
x, y, w, h = plan['pc']['product_box']
product = ImageOps.contain(asset, (int(w*1200), int(h*1200)))
main_canvas.paste(product, (int(x*1200)+(int(w*1200)-product.width)//2, int(y*1200)))
main_canvas.save(root/'planned-main.jpg')
create_german_composite(root/'planned-main.jpg', root/'detail-typography.jpg', briefs[1].copy_for('de'), creative_plan=plan)
panels = []
for index, brief in enumerate([b for b in briefs if b.channel == '高级A+']):
    text = brief.copy_for('de') if index == 0 else 'Durchdachte Details\nFür die nächste Reise\nGriff im Blick\nPlatz für unterwegs'
    panel = poster_panel(asset, brief.creative_plan, (1464, 600), 'pc', text)
    panels.append(panel)
    if index == 0:
        poster_panel(asset, brief.creative_plan, (1200, 900), 'mobile', text).save(root/'mobile-panel.jpg', quality=95)
master = Image.new('RGB', (1464, 600*len(panels)))
for index, panel in enumerate(panels):
    master.paste(panel, (0, index*600))
master.save(root/'aplus-master.jpg', quality=95)
print(root)
