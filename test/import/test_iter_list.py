list = ["abc","bcd","xyz"]

#按照元素的个数遍历当前列表
for i in range(len(list)):
    print(list[i])

#使用enumerate遍历list
for index, chunk in enumerate( list, start=1):
    print(index, chunk)