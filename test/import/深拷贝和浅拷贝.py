import copy
dict1 = {'a': 1, 'b': {'c': 2}}
#赋值
# dict0 = dict1
# dict1['b']['c'] = 999
# dict1['a'] = 666
# print(dict0)
# 字典的浅拷贝

# dict2 = dict1.copy()  # 浅拷贝
# dict2['b']['c'] = 999
# dict2['a'] = 666
# print(dict1)  # 999 ⚠️ 被改了！

# 字典的深拷贝
dict3 = {'a': 1, 'b': {'c': 2}}
dict4 = copy.deepcopy(dict3)  # 深拷贝
dict3['b']['c'] = 999
dict3['a'] = 666
print(dict4)  # 2 ✅ 没事！
