import re

#  !\[.*?\]\(.*?a\.jpg.*?\)
image_file = "a.jpg"
md_content = "blablab![](images/a.jpg)lablabasdasdasd"
pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r".*?\)")
for m in pattern.finditer(md_content):
    print(m)