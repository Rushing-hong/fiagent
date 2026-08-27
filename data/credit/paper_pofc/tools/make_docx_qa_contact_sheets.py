from pathlib import Path

from PIL import Image, ImageDraw


SOURCE = Path(
    r"D:\cvnotenough\data\experiments\a1_oracle\paper\OR_submission_expanded"
    r"\qa_citation_redistribution\anonymous3"
)
OUTPUT = SOURCE.parent
GROUPS = [range(1, 9), range(19, 27), range(31, 36)]


for group_index, group in enumerate(GROUPS, start=1):
    page_numbers = list(group)
    images = [
        Image.open(SOURCE / f"page-{page_number}.png").convert("RGB").resize((408, 528))
        for page_number in page_numbers
    ]
    columns = 2
    rows = (len(images) + columns - 1) // columns
    sheet = Image.new("RGB", (408 * columns, 528 * rows), "white")
    draw = ImageDraw.Draw(sheet)
    for index, (image, page_number) in enumerate(zip(images, page_numbers)):
        x = (index % columns) * 408
        y = (index // columns) * 528
        sheet.paste(image, (x, y))
        draw.text((x + 12, y + 12), f"p{page_number}", fill="red", stroke_width=2, stroke_fill="white")
    sheet.save(OUTPUT / f"contact_{group_index}.png")
