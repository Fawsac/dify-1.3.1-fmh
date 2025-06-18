
from fastapi import APIRouter, Depends, HTTPException
from services.plugin.plugin_service import PluginService
#from app.dependencies import get_current_active_user
from typing import List
router = APIRouter()

@router.post("/plugins/install-local")
async def install_local_plugins(
    tenant_id="06422d98-2915-4b37-8f50-fcc1ad419e1c",
    plugin_identifiers="1"
):
    try:
        # 调用安装方法
        result = PluginService.install_from_local_pkg(
            tenant_id=tenant_id,
            plugin_unique_identifiers=plugin_identifiers
        )
        return {"status": "success", "task_id": result.task_id}
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"安装失败: {str(e)}"
        )
