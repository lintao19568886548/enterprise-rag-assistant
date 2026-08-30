import re

title_pattern = r'\s*#{1,6}\s+.+'
line = "###测试标题"
result = re.match(title_pattern, line)
print(result)