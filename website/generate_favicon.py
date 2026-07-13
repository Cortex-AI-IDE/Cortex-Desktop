"""Generate favicon.ico and favicon.svg matching the gold C brand mark."""
from PIL import Image, ImageDraw, ImageFont
import os

# Create favicon sizes: 16x16, 32x32, 48x48 (standard ICO sizes)
sizes = [16, 32, 48]
images = []

for size in sizes:
    # Create image with dark background matching the site (#060607)
    img = Image.new('RGBA', (size, size), (6, 6, 7, 255))
    draw = ImageDraw.Draw(img)

    # Draw rounded rectangle background (gold gradient approximated)
    margin = max(1, size // 16)
    radius = max(2, size // 4)

    # Gold color: --gold #E8C547
    gold_color = (232, 197, 71, 255)
    draw.rounded_rectangle(
        [margin, margin, size - margin - 1, size - margin - 1],
        radius=radius,
        fill=gold_color,
    )

    # Draw 'C' letter in dark color (#1a1400)
    letter_color = (26, 20, 0, 255)
    font_size = int(size * 0.55)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf', font_size)
    except Exception:
        try:
            font = ImageFont.truetype('arial.ttf', font_size)
        except Exception:
            font = ImageFont.load_default()

    # Center the C letter
    bbox = draw.textbbox((0, 0), 'C', font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size - text_w) // 2 - bbox[0]
    y = (size - text_h) // 2 - bbox[1]
    draw.text((x, y), 'C', fill=letter_color, font=font)

    images.append(img)

# Save as ICO with multiple sizes
ico_path = os.path.join(os.path.dirname(__file__), 'cortex', 'static', 'cortex', 'img', 'favicon_new.ico')
images[0].save(ico_path, format='ICO', sizes=[(s, s) for s in sizes], append_images=images[1:])
print(f'Favicon saved: {ico_path}')
print(f'Size: {os.path.getsize(ico_path)} bytes')

# Also save as favicon.svg for modern browsers
svg_content = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
  <defs>
    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#E8C547"/>
      <stop offset="100%" stop-color="#C5A028"/>
    </linearGradient>
  </defs>
  <rect width="32" height="32" rx="7" fill="url(#g)"/>
  <text x="16" y="23" text-anchor="middle" font-family="Arial,sans-serif" font-weight="900" font-size="20" fill="#1a1400">C</text>
</svg>'''

svg_path = os.path.join(os.path.dirname(__file__), 'cortex', 'static', 'cortex', 'img', 'favicon_new.svg')
with open(svg_path, 'w') as f:
    f.write(svg_content)
print(f'SVG favicon saved: {svg_path}')
