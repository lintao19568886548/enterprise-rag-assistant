import re

# re.escape：将原始字符串中的特殊字符进行转义
image_file = "a.jpg"
result = re.escape(image_file)

print(result)