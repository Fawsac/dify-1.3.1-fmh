import atexit
import logging
import os
import platform
import socket
import sys
import time
from typing import Union

import requests
from celery.signals import worker_init  # type: ignore
from flask_login import user_loaded_from_request, user_logged_in  # type: ignore
from prometheus_client import REGISTRY

from configs import dify_config
from dify_app import DifyApp


@user_logged_in.connect
@user_loaded_from_request.connect
def on_user_loaded(_sender, user):
    if dify_config.ENABLE_OTEL:
        from opentelemetry.trace import get_current_span

        if user:
            current_span = get_current_span()
            if current_span:
                current_span.set_attribute("service.tenant.id", user.current_tenant_id)
                current_span.set_attribute("service.user.id", user.id)


def init_app(app: DifyApp):

    def is_celery_worker():
        return "celery" in sys.argv[0].lower()
    print("🧾 当前进程命令:", sys.argv)
    print("🧾 是否是 Celery Worker:", is_celery_worker())

    def instrument_exception_logging():
        exception_handler = ExceptionLoggingHandler()
        logging.getLogger().addHandler(exception_handler)

    def init_flask_instrumentor(app: DifyApp):
        print("🔧 初始化 FlaskInstrumentor")  # 添加调试输出

        meter = get_meter("http_metrics", version=dify_config.CURRENT_VERSION)
        _http_response_counter = meter.create_counter(
            "http.server.response.count", description="Total number of HTTP responses by status code", unit="{response}"
        )
        # 添加请求持续时间直方图
        _http_duration_histogram = meter.create_histogram(
            "http.server.duration",
            description="HTTP request duration in milliseconds",
            unit="ms"
        )

        # 添加活跃请求数量的上下文仪表
        _http_active_requests = meter.create_up_down_counter(
            "http.server.active_requests",
            description="Number of active HTTP requests"
        )
        def response_hook(span: Span, status: str, response_headers: list):
            if span and span.is_recording():
                start_time = getattr(span, '_start_time', time.time_ns())
                duration = (time.time_ns() - start_time) / 1_000_000  # 转换为毫秒

                if status.startswith("2"):
                    span.set_status(StatusCode.OK)
                else:
                    span.set_status(StatusCode.ERROR, status)

                status = status.split(" ")[0]
                status_code = int(status)
                status_class = f"{status_code // 100}xx"
                _http_response_counter.add(1, {"status_code": status_code, "status_class": status_class})

                _http_duration_histogram.record(duration, {"status_code": status_code})

        # 在请求开始时增加活跃请求数量
        def before_request():
            _http_active_requests.add(1)

        # 在请求结束时减少活跃请求数量
        def after_request(response):
            _http_active_requests.add(-1)
            return response

        app.before_request(before_request)
        app.after_request(after_request)

        instrumentor = FlaskInstrumentor()
        if dify_config.DEBUG:
            logging.info("Initializing Flask instrumentor")
        instrumentor.instrument_app(app, response_hook=response_hook)

    def init_sqlalchemy_instrumentor(app: DifyApp):
        with app.app_context():
            engines = list(app.extensions["sqlalchemy"].engines.values())
            SQLAlchemyInstrumentor().instrument(enable_commenter=True, engines=engines)

    def setup_context_propagation():
        # Configure propagators
        set_global_textmap(
            CompositePropagator(
                [
                    TraceContextTextMapPropagator(),  # W3C trace context
                    B3Format(),  # B3 propagation (used by many systems)
                ]
            )
        )

    def shutdown_tracer():
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush()

    class ExceptionLoggingHandler(logging.Handler):
        """Custom logging handler that creates spans for logging.exception() calls"""

        def emit(self, record):
            try:
                if record.exc_info:
                    tracer = get_tracer_provider().get_tracer("dify.exception.logging")
                    with tracer.start_as_current_span(
                        "log.exception",
                        attributes={
                            "log.level": record.levelname,
                            "log.message": record.getMessage(),
                            "log.logger": record.name,
                            "log.file.path": record.pathname,
                            "log.file.line": record.lineno,
                        },
                    ) as span:
                        span.set_status(StatusCode.ERROR)
                        span.record_exception(record.exc_info[1])
                        span.set_attribute("exception.type", record.exc_info[0].__name__)
                        span.set_attribute("exception.message", str(record.exc_info[1]))
            except Exception:
                pass

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.celery import CeleryInstrumentor
    from opentelemetry.instrumentation.flask import FlaskInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.metrics import get_meter, get_meter_provider, set_meter_provider
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.b3 import B3Format
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatio
    from opentelemetry.semconv.resource import ResourceAttributes
    from opentelemetry.trace import Span, get_tracer_provider, set_tracer_provider
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    from opentelemetry.trace.status import StatusCode

    setup_context_propagation()
    # Initialize OpenTelemetry
    # Follow Semantic Convertions 1.32.0 to define resource attributes
    resource = Resource(
        attributes={
            ResourceAttributes.SERVICE_NAME: dify_config.APPLICATION_NAME,
            ResourceAttributes.SERVICE_VERSION: f"dify-{dify_config.CURRENT_VERSION}-{dify_config.COMMIT_SHA}",
            ResourceAttributes.PROCESS_PID: os.getpid(),
            ResourceAttributes.DEPLOYMENT_ENVIRONMENT: f"{dify_config.DEPLOY_ENV}-{dify_config.EDITION}",
            ResourceAttributes.HOST_NAME: socket.gethostname(),
            ResourceAttributes.HOST_ARCH: platform.machine(),
            "custom.deployment.git_commit": dify_config.COMMIT_SHA,
            ResourceAttributes.HOST_ID: platform.node(),
            ResourceAttributes.OS_TYPE: platform.system().lower(),
            ResourceAttributes.OS_DESCRIPTION: platform.platform(),
            ResourceAttributes.OS_VERSION: platform.version(),
        }
    )
    sampler = ParentBasedTraceIdRatio(dify_config.OTEL_SAMPLING_RATE)
    provider = TracerProvider(resource=resource, sampler=sampler)
    set_tracer_provider(provider)
    exporter: Union[OTLPSpanExporter, ConsoleSpanExporter]
    metric_exporter: Union[OTLPMetricExporter, ConsoleMetricExporter]
    #if dify_config.OTEL_EXPORTER_TYPE == "otlp":

    if dify_config.OTEL_EXPORTER_TYPE == "prometheus":
        from opentelemetry.exporter.prometheus import PrometheusMetricReader
        from prometheus_client import start_http_server
        # 确保指标被注册到 Prometheus 全局 registry
        def find_available_port(start_port=9464, end_port=9500):
            for port in range(start_port, end_port + 1):
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        s.bind(("localhost", port))
                    return port
                except OSError:
                    continue
            return 0  # 如果找不到可用端口，使用随机端口

        port = find_available_port()
        if port == 0:
            print("⚠️ 未找到9464-9500范围内的可用端口，使用随机端口")

        # 启动服务器并获取服务器对象
        server_info = start_http_server(port)
        http_server = server_info[0]  # 获取第一个元素：HTTPServer 实例
        actual_port = http_server.server_port

        print(f"Prometheus服务器已启动，端口: {actual_port}")
        # 设置环境变量供测试脚本使用
        os.environ['PROMETHEUS_METRICS_PORT'] = str(actual_port)

        # 立即检查服务器是否在监听
        def check_port_listening():
            """检查端口是否真正在监听"""
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    s.connect(('localhost', actual_port))
                print(f"✅ 端口 {actual_port} 正在监听")
                return True
            except Exception as e:
                print(f"❌ 端口 {actual_port} 未在监听: {str(e)}")
                return False

        # 执行端口检查
        if not check_port_listening():
            print("⚠️ 服务器可能未正确启动")
        # 先设置 MeterProvider 再创建 PrometheusMetricReader
        reader = PrometheusMetricReader()
        set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

        # 验证服务器是否运行
        def check_server():
            time.sleep(1)
            try:
                response = requests.get(f"http://localhost:{actual_port}/metrics", timeout=1)
                print(f"服务器验证: {'✅ 成功' if response.status_code == 200 else f'❌ 状态码 {response.status_code}'}")
            except Exception as e:
                print(f"服务器验证失败: {str(e)}")
        #reader = PrometheusMetricReader()
        import threading
        threading.Thread(target=check_server).start()

        # 设置跟踪导出器
        exporter = OTLPSpanExporter(
            endpoint=dify_config.OTLP_BASE_ENDPOINT + "/v1/traces",
            headers={"Authorization": f"Bearer {dify_config.OTLP_API_KEY}"},
        )

        # 不需要 metric_exporter，因为指标由 PrometheusMetricReader 处理
        metric_exporter = None
        '''exporter = OTLPSpanExporter(
            endpoint=dify_config.OTLP_BASE_ENDPOINT + "/v1/traces",
            headers={"Authorization": f"Bearer {dify_config.OTLP_API_KEY}"},
        )
        metric_exporter = OTLPMetricExporter(
            endpoint=dify_config.OTLP_BASE_ENDPOINT + "/v1/metrics",
            headers={"Authorization": f"Bearer {dify_config.OTLP_API_KEY}"},
        )'''
    else:
        # Fallback to console exporter
        exporter = ConsoleSpanExporter()
        metric_exporter = ConsoleMetricExporter()

    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            max_queue_size=dify_config.OTEL_MAX_QUEUE_SIZE,
            schedule_delay_millis=dify_config.OTEL_BATCH_EXPORT_SCHEDULE_DELAY,
            max_export_batch_size=dify_config.OTEL_MAX_EXPORT_BATCH_SIZE,
            export_timeout_millis=dify_config.OTEL_BATCH_EXPORT_TIMEOUT,
        )
    )
    if dify_config.OTEL_EXPORTER_TYPE != "prometheus":
        reader = PeriodicExportingMetricReader(
            metric_exporter,
            export_interval_millis=dify_config.OTEL_METRIC_EXPORT_INTERVAL,
            export_timeout_millis=dify_config.OTEL_METRIC_EXPORT_TIMEOUT,
        )
        set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
    else:
    # Prometheus 分支中已经设置了 MeterProvider
        pass

    def init_custom_metrics():
        meter = get_meter("dify.business", version=dify_config.CURRENT_VERSION)

        # 用户操作计数器
        _user_operations_counter = meter.create_counter(
            "dify.user.operations",
            description="Count of user operations"
        )

        # 模型调用计数器
        _model_calls_counter = meter.create_counter(
            "dify.model.calls",
            description="Count of model API calls"
        )

        # 模型调用延迟直方图
        _model_latency_histogram = meter.create_histogram(
            "dify.model.latency",
            description="Model API call latency in milliseconds",
            unit="ms"
        )

        # 存储用量仪表
        _storage_usage_gauge = meter.create_observable_gauge(
            "dify.storage.usage",
            description="Current storage usage in bytes",
            callbacks=[get_storage_usage]
        )

        # 将指标存储在全局变量中，以便在应用中使用
        app.extensions['metrics'] = {
            'user_operations': _user_operations_counter,
            'model_calls': _model_calls_counter,
            'model_latency': _model_latency_histogram
        }

    def get_storage_usage(callback_options):
        # 这里应该实现实际的存储用量获取逻辑
        # 作为示例返回0
        yield {
            "value": 0,
            "attributes": {"storage_type": "database"}
        }
    if not is_celery_worker():
        print("🔧 正在初始化 FlaskInstrumentor")  # 添加调试输出
        meter = get_meter("http_metrics", version=dify_config.CURRENT_VERSION)
        init_flask_instrumentor(app)
        init_custom_metrics()
        CeleryInstrumentor(tracer_provider=get_tracer_provider(), meter_provider=get_meter_provider()).instrument()
    instrument_exception_logging()
    init_sqlalchemy_instrumentor(app)
    atexit.register(shutdown_tracer)


def is_enabled():
    return dify_config.ENABLE_OTEL


@worker_init.connect(weak=False)
def init_celery_worker(*args, **kwargs):
    if dify_config.ENABLE_OTEL:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor
        from opentelemetry.metrics import get_meter_provider
        from opentelemetry.trace import get_tracer_provider

        tracer_provider = get_tracer_provider()
        metric_provider = get_meter_provider()
        if dify_config.DEBUG:
            logging.info("Initializing OpenTelemetry for Celery worker")
        CeleryInstrumentor(tracer_provider=tracer_provider, meter_provider=metric_provider).instrument()
