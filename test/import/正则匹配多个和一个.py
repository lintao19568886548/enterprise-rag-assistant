import re

image_file = "a.jpg"
md_content = "![](images/a.jpg)lablabasdasdasd![](images/a.jpg)"
pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r".*?\)")
# match = pattern.search(md_content) #匹配第一个
# print(match)

for m in pattern.finditer(md_content):
    print(m)
