from collections.abc import Sequence

import requests
from yarl import URL

from configs import dify_config
from core.helper.download import download_with_size_limit
from core.plugin.entities.marketplace import MarketplacePluginDeclaration

def get_plugin_pkg_url(plugin_unique_identifier: str):
    return (URL(str(dify_config.MARKETPLACE_API_URL)) / "api/v1/plugins/download").with_query(
        unique_identifier=plugin_unique_identifier
    )


def download_plugin_pkg(plugin_unique_identifier: str):
    url = str(get_plugin_pkg_url(plugin_unique_identifier))
    return download_with_size_limit(url, dify_config.PLUGIN_MAX_PACKAGE_SIZE)
'''

# 假设你的后端接口地址
YOUR_BACKEND_API_URL = 'http://localhost:8000/plugins/download'

def download_plugin_pkg(plugin_unique_identifier: str):
    try:
        # 构建请求参数
        params = {
            'unique_identifier': plugin_unique_identifier
        }
        # 调用你自己的后端接口
        #response = requests.get(YOUR_BACKEND_API_URL, params=params, stream=True)
        response = requests.get(f"{YOUR_BACKEND_API_URL}/{plugin_unique_identifier}", stream=True)

        response.raise_for_status()

        # 处理下载的文件
        file_path = f'/tmp/{plugin_unique_identifier}.difypkg'  # 临时文件路径
        with open(file_path, 'wb') as file:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file.write(chunk)
        return file_path
    except requests.RequestException as e:
        raise Exception(f"Failed to download plugin package: {e}")
'''
def batch_fetch_plugin_manifests(plugin_ids: list[str]) -> Sequence[MarketplacePluginDeclaration]:
    if len(plugin_ids) == 0:
        return []

    url = str(URL(str(dify_config.MARKETPLACE_API_URL)) / "api/v1/plugins/batch")
    response = requests.post(url, json={"plugin_ids": plugin_ids})
    response.raise_for_status()
    return [MarketplacePluginDeclaration(**plugin) for plugin in response.json()["data"]["plugins"]]

def record_install_plugin_event(plugin_unique_identifier: str):
    url = str(URL(str(dify_config.MARKETPLACE_API_URL)) / "api/v1/stats/plugins/install_count")
    response = requests.post(url, json={"unique_identifier": plugin_unique_identifier})
    response.raise_for_status()
