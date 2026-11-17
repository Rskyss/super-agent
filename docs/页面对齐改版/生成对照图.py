"""把真实窗口放左侧、对应草图放右侧；保留原图比例，不伪造界面。"""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def compose(actual, reference, output, title):
    actual_image = Image.open(actual).convert('RGB')
    reference_image = Image.open(reference).convert('RGB')
    label_height = 42
    result = Image.new('RGB', (actual_image.width + reference_image.width,
                             max(actual_image.height, reference_image.height) + label_height), '#ececec')
    result.paste(actual_image, (0, label_height))
    result.paste(reference_image, (actual_image.width, label_height))
    draw = ImageDraw.Draw(result)
    font_path = Path('/System/Library/Fonts/STHeiti Light.ttc')
    font = ImageFont.truetype(str(font_path), 19) if font_path.exists() else ImageFont.load_default()
    draw.text((16, 10), f'真实窗口 · {title}', fill='#202020', font=font)
    draw.text((actual_image.width + 16, 10), f'设计草图 · {Path(reference).stem}（名字读作超级玩家）', fill='#202020', font=font)
    draw.line((actual_image.width, 0, actual_image.width, result.height), fill='#8a8a8a', width=2)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('actual')
    parser.add_argument('reference')
    parser.add_argument('output')
    parser.add_argument('--title', default='页面对齐')
    options = parser.parse_args()
    compose(options.actual, options.reference, options.output, options.title)
