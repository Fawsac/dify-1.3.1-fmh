from events.record_log import OperationRecordLog
from flask_restful import Resource, inputs, reqparse
from controllers.console import api
from controllers.console.wraps import setup_required, account_initialization_required
from libs.login import login_required
from sqlalchemy import and_
from models.model import InstalledApp,OperationLog
from datetime import datetime, timedelta
from extensions.ext_database import db
from flask_login import current_user
from models.account import Account,TenantAccountJoin
class OperationLogListApi(Resource):
    @setup_required
    @login_required
    @account_initialization_required
    def get(self):
        # 创建参数解析器

        parser = reqparse.RequestParser()
        parser.add_argument('page', type=int, default=1, location='args')
        parser.add_argument('per_page', type=int, default=20, location='args')
        parser.add_argument('action_by', type=str, location='args')
        parser.add_argument('action', type=str, location='args')
        parser.add_argument('type', type=str, location='args')
        parser.add_argument('start_time', type=str, location='args')
        parser.add_argument('end_time', type=str, location='args')
        args = parser.parse_args()
        base_query = db.session.query(OperationLog)
        current_tenant_id = current_user.current_tenant_id
        user_role = current_user.role

        if current_user.current_tenant_id == "0000":
            if current_user.role != 'superadmin':
                pass
            elif current_user.role == 'admin':
                managed_tenant_ids = [
                    tj.tenant_id for tj in
                    TenantAccountJoin.query.filter_by(account_id=current_user.id).all()
                ]
                base_query = base_query.filter(
                    OperationLog.tenant_id.in_(managed_tenant_ids)
                )
            else:  # editor或其他角色
                return {'error': 'Permission denied'}, 403
        else:  # editor或其他角色
            return {'error': 'Permission denied'}, 403

        if args['action_by']:
            base_query = base_query.filter(
                OperationLog.content['metadata']['action_by'].astext.ilike(f'%{args["action_by"]}%')
            )

        if args['action']:
            base_query = base_query.filter(
                OperationLog.action.ilike(f'%{args["action"]}%')
            )

        from sqlalchemy import cast, String

        if args['type']:
            # 当 type=app 时查询 app 和 workflow 类型
            if args['type'] == 'app':
                base_query = base_query.filter(
                    OperationLog.content['metadata']['type'].astext.in_(['app', 'workflow'])
                )
            else:
                # 其他类型保持原模糊匹配逻辑
                base_query = base_query.filter(
                    OperationLog.content['metadata']['type'].astext.ilike(f'%{args["type"]}%')
                )

        if args['start_time'] or args['end_time']:
            args['start_time'] = datetime.strptime(args['start_time'], '%Y-%m-%d %H:%M:%S')
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
        log = db.session.get(OperationLog, log_id)
        if not log:
            return {'error': 'Log not found'}, 404

        # 获取租户ID（使用日志中的租户ID）
        #tenant_id = log.tenant_id
        is_admin = current_user.is_admin

        if not is_admin:
            return {'error': 'Permission denied'}, 403

        else:
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
                log = db.session.get(OperationLog, log_id)
                if not log:
                    errors.append({'log_id': log_id, 'error': 'Log not found'})
                    continue

                # 获取租户ID（使用日志中的租户ID）
                tenant_id = log.tenant_id

                # 检查用户权限

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