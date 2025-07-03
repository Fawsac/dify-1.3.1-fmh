import ast

from events.record_log import OperationRecordLog
from flask_restful import Resource, inputs, reqparse
from controllers.console import api
from controllers.console.wraps import setup_required, account_initialization_required
from libs.login import login_required
from sqlalchemy import and_,text
from models.model import InstalledApp,OperationLog
from datetime import datetime, timedelta
from extensions.ext_database import db
from flask_login import current_user
from models.account import Account,TenantAccountJoin,TenantAccountRole
from services.errors.account import NoPermissionError
import pandas as pd
from flask import send_file
from io import BytesIO

class OperationLogListApi(Resource):

    def _build_base_query(self, args):
        # 创建基础查询
        base_query = db.session.query(OperationLog)
        current_tenant_id = current_user.current_tenant_id

        tenant_account_role = TenantAccountJoin.query.filter_by(
            account_id = current_user.id,
            tenant_id = current_tenant_id
        ).first()

        #if tenant_account_role.role == TenantAccountRole.ADMIN:
                #base_query = base_query.filter(OperationLog.tenant_id==current_tenant_id)

        #else:  # editor或其他角色
            #return {'error': 'Permission denied'}, 403

        if args['action_by']:
            base_query = base_query.filter(
                OperationLog.content['metadata']['action_by'].astext.ilike(f'%{args["action_by"]}%')
            )

        if args['action']:
            base_query = base_query.filter(
                OperationLog.action.ilike(f'%{args["action"]}%')
            )

        if args['type']:
            # 当 type=app 时查询 app 和 workflow 类型
            if args['type'] == 'app':
                base_query = base_query.filter(
                   text("content->'metadata'->>'type' in ('app', 'workflow')")
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
        return base_query

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

        base_query = self._build_base_query(args)

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

    @setup_required
    @login_required
    @account_initialization_required
    def post(self):
        """下载操作日志为Excel文件"""
        parser = reqparse.RequestParser()
        parser.add_argument('action_by', type=str, location='json')
        parser.add_argument('action', type=str, location='json')
        parser.add_argument('type', type=str, location='json')
        parser.add_argument('start_time', type=str, location='json')
        parser.add_argument('end_time', type=str, location='json')
        args = parser.parse_args()

        # 复用GET方法的查询构建逻辑
        base_query = self._build_base_query(args)
        # 获取全部符合条件的数据（不分页）
        logs = base_query.order_by(OperationLog.created_at.desc()).all()
        # 添加数据量限制
        if len(logs) > 10000:
            return {'error': 'Too many records to export'}, 400

        # 转换为DataFrame
        data = []
        index = 1
        for log in logs:
            # 安全获取 metadata 字段
            metadata = log.content.get('metadata', {})

            data.append({
                '序号': index,
                '应用名称': metadata.get('app_name', ''),
                '应用类型': metadata.get('type', ''),
                '操作类型': log.action,
                '操作人': metadata.get('action_by', ''),
                '操作内容': metadata.get('remark', ''),
                '操作IP': log.created_ip,
                '操作时间': log.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            })
            index += 1
            # 定义固定列顺序
        columns = [
            '序号', '应用名称', '应用类型',
            '操作类型', '操作人', '操作内容',
            '操作IP', '操作时间'
        ]

        df = pd.DataFrame(data)

        # 按指定顺序重排列
        df = df[columns]
        # 生成Excel文件
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='操作日志', index=False)

        output.seek(0)

        # 返回文件响应
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='操作日志.xlsx'
        )


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

        #is_admin = current_user.is_admin

        #if not is_admin:
            #return {'error': 'Permission denied'}, 403

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
            if isinstance(args['log_ids'],dict):
                log_ids_list = args['log_ids'].get['log_ids']
            elif isinstance(args['log_ids'],list):
                log_ids_list = args['log_ids']
            else:
                log_ids_list = ast.literal_eval(args['log_ids'])
        except ValueError:
            return {'error': 'Invalid log_ids format'}, 400

        #is_admin = current_user.is_admin

        #if not is_admin:
            #raise NoPermissionError("无权限")
        # 执行批量删除
        deleted_count = 0
        errors = []

        try:
            for log_id in log_ids_list:
                # 获取要删除的日志
                log = db.session.get(OperationLog, log_id)
                if not log:
                    errors.append({'log_id': log_id, 'error': 'Log not found'})
                    continue


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
