import base64
import os

from app.utils.path_util import PROJECT_ROOT

# 组装文件路径
image_file= os.path.join("output/hak180产品安全手册/images", "4e6c79160aa0edb9873bb4cd722f511d5b6850712754e457edc9716f2f509736.jpg")
image_path = os.path.join(PROJECT_ROOT, image_file)

with open(image_path, "rb") as img_file:
    base64_image = base64.b64encode(img_file.read()).decode("utf-8")


print(base64_image)