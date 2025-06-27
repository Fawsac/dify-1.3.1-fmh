from extensions.ext_database import db
from models.model import InstalledApp,OperationLog
from models.account import Account
from flask_login import current_user
from flask import request
from datetime import datetime

class OperationRecordLog:
    @staticmethod
    def get_client_ip():
        if request.headers.get("Connecting-IP"):
            return request.headers.get("Connecting-IP")[0].split(',')[0].strip()

        return request.remote_addr

    def Operation_log(app,action,type,remark):
        tenant_id = getattr(current_user, 'current_tenant_id', None)

        if not tenant_id:
            tenant_id = getattr(app, 'tenant_id', None)

        if not tenant_id:
            tenant_id = "00000000-0000-0000-0000-000000000000"

        operation_log = OperationLog(
            tenant_id = tenant_id,
            account_id = current_user.id,
            action=action,
            content={"metadata":
                         {"action_by":getattr(current_user, 'name', ''),
                          "app_id":getattr(app, 'id', None ),
                          "app_name":getattr(app, 'name', None),
                          "type":type,
                          "created_by":getattr(app, 'created_by', None),
                          "remark": remark
                          }
                     },
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            created_ip = OperationRecordLog.get_client_ip()
        )
        db.session.add(operation_log)
        db.session.commit()