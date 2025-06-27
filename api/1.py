import requests


# 测试数组格式
response_arr = requests.post(
    'http://localhost:5001/test-endpoint',
    json={'name': '数组测试', 'account_id': "[201,202,203]"}
)
print("\n混合格式测试结果:")

response_arr = requests.post(
    'http://localhost:5001/test-endpoint',
    json={'name': '测试', 'account_id': "101"}
)
print("\n混合格式测试结果:")


i= ['1','2']
for t in i:
    print(t)
