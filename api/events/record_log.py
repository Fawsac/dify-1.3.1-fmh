from events.app_event import app_was_created
from extensions.ext_database import db
from models.model import InstalledApp,OperationLog
from models.model import Account
from flask_login import current_user
from flask import request
from datetime import datetime

class OperationRecordLog:
    @staticmethod
    def get_client_ip():
        if request.headers.get("Connecting-IP"):
            return request.headers.get("Connecting-IP")[0].split(',')[0].strip()

        return request.remote_addr

    def Operation_log(app,action,type):
        tenant_id = getattr(app, 'tenant', None)  # 如果 app 不存在或 app.tenant 为空，返回 None
        tenant_id = tenant_id.id if tenant_id else "00000000-0000-0000-0000-000000000000"  # 如果 tenant 存在，取其 id，否则为 None

        #account_id = getattr(current_user, 'id', None)  # 如果 current_user 不存在或没有 id，返回 None

        operation_log = OperationLog(
            tenant_id = tenant_id,
            account_id = current_user.id,
            action=action,
            content={"metadata":
                         {"action_by":getattr(current_user, 'name', ''),
                          "app_id":getattr(app, 'id', None ),
                          "app_name":getattr(app, 'name', None),
                          "type":type,
                          "created_by":getattr(app, 'created_by', None)
                          }
                     },
            created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            updated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            created_ip = OperationRecordLog.get_client_ip()
        )
        db.session.add(operation_log)
        db.session.commit()

from flask import jsonify

@app.route('/operation_logs', methods=['GET'])
def get_operation_logs():
    logs = OperationLog.query.order_by(OperationLog.created_at.desc()).all()
    log_list = []
    for log in logs:
        log_list.append({
            'id': log.id,
            'tenant_id': log.tenant_id,
            'account_id': log.account_id,
            'action': log.action,
            'content': log.content,  # 包含metadata对象
            'created_at': log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            'created_ip': log.created_ip
        })
    return jsonify(log_list)