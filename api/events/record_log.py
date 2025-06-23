from events.app_event import app_was_created
from extensions.ext_database import db
from models.model import InstalledApp,OperationLog
from models.model import Account
from flask_login import current_user
from flask import request
from datetime import datetime

from requests import delete


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
from controllers.console.wraps import setup_required, account_initialization_required
from libs.login import login_required
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


class OperationLogDetailApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    def delete(self, log_id):
        # 获取要删除的日志
        log = OperationLog.query.get(log_id)
        if not log:
            return {'error': 'Log not found'}, 404

        # 获取租户ID（使用日志中的租户ID）
        tenant_id = log.tenant_id

        # 检查用户权限
        '''user_role = Tenant.get_user_role(current_user, tenant_id)
        if user_role not in [Tenant.owner.value]:
            return {'error': 'Permission denied'}, 403'''
        # 执行删除操作

        try:
            db.session.delete(log)
            db.session.commit()
            return {'result': 'success', 'message': 'Log deleted successfully', 'log_id': log_id}, 200
        except Exception as e:
            db.session.rollback()
            return {'error': str(e)}, 500


class OperationLogBatchApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    def delete(self):
        # 创建参数解析器
        parser = reqparse.RequestParser()
        parser.add_argument('log_ids', required=True, location='json', help='Log IDs are required')
        args = parser.parse_args()

        # 转换 log_ids 字符串为整数列表
        try:
            log_ids = args['log_ids'].split(',')
        except ValueError:
            return {'error': 'Invalid log_ids format'}, 400

        # 执行批量删除
        deleted_count = 0
        errors = []

        try:
            for log_id in log_ids:
                # 获取要删除的日志
                log = OperationLog.query.get(log_id)
                if not log:
                    errors.append({'log_id': log_id, 'error': 'Log not found'})
                    continue

                # 获取租户ID（使用日志中的租户ID）
                tenant_id = log.tenant_id

                # 检查用户权限
                '''user_role = TenantService.get_user_role(current_user.id, tenant_id)
                if user_role not in [Tenant.owner.value]:
                    errors.append({'log_id': log_id, 'error': 'Permission denied'})
                    continue'''

                # 执行删除操作
                db.session.delete(log)
                deleted_count += 1

            # 提交所有成功的删除操作
            if deleted_count > 0:
                db.session.commit()

            # 构造响应
            response = {
                'result': 'success' if not errors else 'partial_success',
                'message': f'Deleted {deleted_count} logs, failed {len(errors)}',
                'deleted_count': deleted_count,
                'error_count': len(errors),
                'errors': errors
            }

            return response, 200 if deleted_count > 0 else 400

        except Exception as e:
            db.session.rollback()
            return {'error': str(e), 'deleted_count': deleted_count}, 500


# 注册路由
api.add_resource(OperationLogDetailApi, '/operation_logs/<string:log_id>')
api.add_resource(OperationLogBatchApi, '/operation_logs/batch')
api.add_resource(OperationLogListApi, '/operation_logs')