import os
import requests
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def test_prometheus_endpoint():
    # 从环境变量获取实际端口
    port = os.getenv('PROMETHEUS_METRICS_PORT', '9464')
    url = f"http://localhost:{port}/metrics"

    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.3,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)

    try:
        print(f"尝试连接 Prometheus 端点: {url}")
        response = session.get(url, timeout=5)  # 增加超时时间
        if response.status_code == 200:
            print("✅ Prometheus 端点响应正常")
            # 检查 OpenTelemetry 指标
            otel_found = False
            for line in response.text.split('\n'):
                if 'otel_' in line:
                    otel_found = True
                    break

            if otel_found:
                print("✅ 检测到 OpenTelemetry 指标")
            else:
                print("❌ 未找到 OpenTelemetry 指标")
                print("指标内容示例:")
                for line in response.text.split('\n')[:10]:
                    print(f"  {line}")
        else:
            print(f"❌ 端点返回异常状态码: {response.status_code}")
    except requests.exceptions.ConnectionError as e:
        print(f"❌ 无法连接到 Prometheus 端点: {str(e)}")
    except requests.exceptions.Timeout:
        print("❌ 连接超时")


# 增加等待时间确保服务器完全启动
time.sleep(10)  # 增加到10秒
test_prometheus_endpoint()
