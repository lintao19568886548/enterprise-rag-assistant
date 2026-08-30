import re

content = "ab123dasdbjsdkjab"

# 贪婪模式：尽可能多的匹配字符
pattern = re.compile(r"a.*b")
# 非贪婪模式：尽可能少的匹配字符
# pattern = re.compile(r"a.*?b")

for m in pattern.finditer(content):
    print(m)