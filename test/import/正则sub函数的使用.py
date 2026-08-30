import re

image_file = "4e6c.jpg"
md_content = "一些文本![说明](images/4e6c.jpg)一些文本"
summary = r"大模型\1总结\s的图片摘要"
new_url = "http://minioserver:9000/b/u/title/image.jpg"
pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r".*?\)")
md_content_new = pattern.sub(lambda m : f"![{summary}]({new_url})", md_content)
print(md_content_new)