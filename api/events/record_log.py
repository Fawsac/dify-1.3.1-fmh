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

    def Operation_log(app,action,type,remark):
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


from flask_restful import Resource, inputs, reqparse
from controllers.console import api
from controllers.console.wraps import setup_required, login_required, account_initialization_required
from sqlalchemy import and_

class OperationLogListApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    def get(self):
        # 创建参数解析器
        parser = reqparse.RequestParser()
        parser.add_argument('page', type=inputs.int, default=1, location='args')
        parser.add_argument('per_page', type=inputs.int, default=20, location='args')
        parser.add_argument('action_by', type=str, location='args')
        parser.add_argument('action', type=str, location='args')
        parser.add_argument('type', type=str, location='args')
        parser.add_argument('start_time', type=str, location='args')
        parser.add_argument('end_time', type=str, location='args')
        args = parser.parse_args()

        # 原始查询逻辑保持不变
        base_query = OperationLog.query

        if args['action_by']:
            base_query = base_query.filter(
                OperationLog.content['metadata']['action_by'].astext.ilike(f'%{args["action_by"]}%')
            )

        if args['action']:
            base_query = base_query.filter(
                OperationLog.action.ilike(f'%{args["action"]}%')
            )

        if args['type']:
            base_query = base_query.filter(
                OperationLog.content['metadata']['type'].astext.ilike(f'%{args["type"]}%')
            )

        if args['start_time'] or args['end_time']:
            if args['start_time'] and not args['end_time']:
                base_query = base_query.filter(
                    OperationLog.created_at >= args['start_time']
                )
            elif args['end_time'] and not args['start_time']:
                base_query = base_query.filter(
                    OperationLog.created_at <= args['end_time']
                )
            elif args['start_time'] and args['end_time']:
                base_query = base_query.filter(
                    and_(
                        OperationLog.created_at >= args['start_time'],
                        OperationLog.created_at <= args['end_time']
                    )
                )

        logs = base_query.order_by(OperationLog.created_at.desc()).paginate(
            page=args['page'],
            per_page=args['per_page'],
            error_out=False
        )

        log_list = []
        for log in logs.items:
            log_list.append({
                'id': log.id,
                'tenant_id': log.tenant_id,
                'account_id': log.account_id,
                'action': log.action,
                'content': log.content,
                'created_at': log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                'created_ip': log.created_ip
            })

        return {
            'data': log_list,
            'total': logs.total,
            'page': logs.page,
            'per_page': logs.per_page,
            'pages': logs.pages
        }
api.add_resource(OperationLogListApi, '/operation_logs')