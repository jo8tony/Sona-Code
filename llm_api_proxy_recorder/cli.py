"""CLI 入口：加载配置 → 应用覆盖 → 启动 uvicorn。"""

import argparse
import sys

import uvicorn

from llm_api_proxy_recorder.app import create_app
from llm_api_proxy_recorder.config import CONFIG_PATH, load_config, resolved_records_dir


def _harden_stdio() -> None:
    """统一管道输出为 UTF-8；冻结版 Python 可能忽略 PYTHONIOENCODING。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if encoding != "utf8":
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-api-proxy-recorder",
        description="本地大模型 API 代理记录器：透明转发并落盘调用明细",
    )
    parser.add_argument("--host", help="监听地址（覆盖配置中的 server.host，仅本次生效）")
    parser.add_argument("--port", type=int, help="监听端口（覆盖配置中的 server.port，仅本次生效）")
    parser.add_argument("--config", default=CONFIG_PATH, help=f"配置文件路径（默认 {CONFIG_PATH}）")
    parser.add_argument("--records-dir", help="记录目录（覆盖 recording.dir，仅本次生效）")
    parser.add_argument("--admin-prefix", help="管理路径前缀（覆盖 server.admin_prefix，仅本次生效）")
    return parser


def main(argv: list[str] | None = None) -> None:
    _harden_stdio()
    args = build_parser().parse_args(argv)

    cfg = load_config(args.config)
    # 覆盖仅本次运行生效，不回写配置文件
    overrides: dict[str, str] = {}
    if args.host:
        cfg.server.host = args.host
        overrides["server.host"] = args.host
    if args.port:
        cfg.server.port = args.port
        overrides["server.port"] = str(args.port)
    if args.admin_prefix:
        cfg.server.admin_prefix = args.admin_prefix
        overrides["server.admin_prefix"] = args.admin_prefix
    if args.records_dir:
        cfg.recording.dir = args.records_dir
        overrides["recording.dir"] = args.records_dir

    host, port, prefix = cfg.server.host, cfg.server.port, cfg.server.admin_prefix
    print(_banner(host, port, prefix, resolved_records_dir(cfg), args.config, overrides))

    app = create_app(cfg, config_path=args.config)
    uvicorn.run(app, host=host, port=port, log_level="info")


def _banner(
    host: str, port: int, admin_prefix: str, records_dir, config_path: str, overrides: dict[str, str]
) -> str:
    sep = "=" * 58
    lines = [
        sep,
        "  llm-api-proxy-recorder  本地大模型 API 代理记录器",
        sep,
        f"  代理接入地址 : http://{host}:{port}",
        f"  Web UI 地址  : http://{host}:{port}{admin_prefix}/",
        f"  记录目录     : {records_dir}",
        f"  配置文件     : {config_path}",
    ]
    if overrides:
        lines.append(f"  本次覆盖     : {', '.join(f'{k}={v}' for k, v in overrides.items())}")
    lines.append(sep)
    return "\n".join(lines)


if __name__ == "__main__":
    main()
