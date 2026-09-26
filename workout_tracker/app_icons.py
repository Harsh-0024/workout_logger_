"""Every icon and launch screen the app ships, built from one square source image.

The admin "app icon" upload and a one-off local run both go through
write_icon_set(), so the browser tab, home screen and launch screens never
drift apart.
"""
from pathlib import Path

from PIL import Image, ImageChops

# Launch screens and the manifest's background_color are pure black; the
# figure is cut out onto it so no square edge shows around it.
BACKGROUND = (0, 0, 0, 255)
DARK_CUTOFF = (30, 60)  # brightness range over which the background fades out

PLAIN_SIZES = [
    (512, 'app-icon-512.png'),
    (192, 'app-icon-192.png'),
    (180, 'apple-touch-icon.png'),
    (32, 'favicon-32.png'),
]

# Android crops "maskable" icons to a circle or squircle; the figure has to sit
# inside the middle 80% to survive that.
MASKABLE_SIZES = [
    (512, 'maskable-512.png'),
    (192, 'maskable-192.png'),
]
MASKABLE_SCALE = 0.88

# iPhone launch screens (portrait): CSS width, CSS height, pixel ratio.
# base.html links each one with the media query iOS matches it against.
IPHONE_SCREENS = [
    (440, 956, 3),  # 16 Pro Max, 17 Pro Max
    (420, 912, 3),  # Air
    (402, 874, 3),  # 16 Pro, 17, 17 Pro
    (430, 932, 3),  # 14 Pro Max, 15 Plus / Pro Max, 16 Plus
    (393, 852, 3),  # 14 Pro, 15, 15 Pro, 16, 16e
    (428, 926, 3),  # 12 / 13 Pro Max, 14 Plus
    (390, 844, 3),  # 12, 13, 14 and their Pros
    (375, 812, 3),  # X, XS, 11 Pro, 12 / 13 mini
    (414, 896, 3),  # XS Max, 11 Pro Max
    (414, 896, 2),  # XR, 11
    (414, 736, 3),  # 6 / 7 / 8 Plus
    (375, 667, 2),  # SE 2nd / 3rd gen, 6 / 7 / 8
    (320, 568, 2),  # SE 1st gen
]
SPLASH_ICON_FRACTION = 0.5  # icon width as a share of the screen width


def splash_filename(css_w, css_h, ratio):
    return f'splash-{css_w * ratio}x{css_h * ratio}.png'


def _knocked_out(image, size):
    """The icon resized to `size` with its dark background made transparent.

    The icon's own background has a faint warm glow; on a pure-black launch
    screen that glow shows as a square. Anything darker than DARK_CUTOFF fades
    out, while the bright figure is kept exactly as drawn.
    """
    icon = image.resize((size, size), Image.LANCZOS)
    r, g, b, a = icon.split()
    brightest = ImageChops.lighter(r, ImageChops.lighter(g, b))
    low, high = DARK_CUTOFF
    keep = brightest.point(lambda v: 0 if v <= low else 255 if v >= high else round(255 * (v - low) / (high - low)))
    icon.putalpha(ImageChops.multiply(a, keep))
    return icon


def _on_black(canvas_size, icon):
    canvas = Image.new('RGBA', canvas_size, BACKGROUND)
    x = (canvas_size[0] - icon.width) // 2
    y = (canvas_size[1] - icon.height) // 2
    canvas.alpha_composite(icon, (x, y))
    return canvas.convert('RGB')


def write_icon_set(image, static_folder):
    image = image.convert('RGBA')
    static = Path(static_folder)
    icon_dir = static / 'icons'
    splash_dir = icon_dir / 'splash'
    splash_dir.mkdir(parents=True, exist_ok=True)

    for size, filename in PLAIN_SIZES:
        resized = image.resize((size, size), Image.LANCZOS)
        resized.save(icon_dir / filename, format='PNG', optimize=True)

    image.save(static / 'favicon.ico', format='ICO', sizes=[(16, 16), (32, 32), (48, 48)])

    for size, filename in MASKABLE_SIZES:
        icon = _knocked_out(image, round(size * MASKABLE_SCALE))
        _on_black((size, size), icon).save(icon_dir / filename, format='PNG', optimize=True)

    for css_w, css_h, ratio in IPHONE_SCREENS:
        width, height = css_w * ratio, css_h * ratio
        icon = _knocked_out(image, round(width * SPLASH_ICON_FRACTION))
        _on_black((width, height), icon).save(
            splash_dir / splash_filename(css_w, css_h, ratio), format='PNG', optimize=True
        )
