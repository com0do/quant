# -*- coding: utf-8 -*-
"""
聚宽「投资研究」根目录模块 — 日志 tee + write_file 落盘（可插拔）

用法（策略文件顶部或 initialize 里）：

    try:
        import jq_observation_log as obs
        obs.bind(log=log, g=g, write_file=write_file, get_context=get_context_fn)
        obs.configure(
            enabled=True,
            path="wufu_run.log",
            per_mode=True,
            tail_lines=5000,
        )
        setup_run_log = obs.setup_run_log
        flush_run_log = obs.flush_run_log
    except ImportError:
        setup_run_log = lambda: None
        flush_run_log = lambda context=None: None

研究环境需把本文件放在「投资研究」根目录；策略里 import 失败则走空实现，零侵入。

get_context_fn: 可选，返回 context 或 None（用于日志行前缀时间戳、run_params.type）。
"""

_log = None
_g = None
_write_file = None
_get_context = None

_enabled = True
_path = "strategy_run.log"
_append_intraday = False
_tail_lines = 5000
_per_mode = True


def bind(log, g, write_file, get_context=None):
    """注入聚宽运行时对象（策略侧调用一次）。"""
    global _log, _g, _write_file, _get_context
    _log = log
    _g = g
    _write_file = write_file
    _get_context = get_context


def configure(
    enabled=True,
    path="strategy_run.log",
    append_intraday=False,
    tail_lines=5000,
    per_mode=True,
):
    global _enabled, _path, _append_intraday, _tail_lines, _per_mode
    _enabled = enabled
    _path = path
    _append_intraday = append_intraday
    _tail_lines = tail_lines
    _per_mode = per_mode


def _resolve_log_path(path):
    if not _per_mode:
        return path
    mode = "backtest"
    try:
        ctx = _get_context() if _get_context else None
        run_params = getattr(ctx, "run_params", None) if ctx is not None else None
        t = getattr(run_params, "type", None)
        if t:
            mode = str(t)
    except Exception:
        pass
    stem_ext = path.rsplit(".", 1)
    return f"{stem_ext[0]}.{mode}.{stem_ext[1]}" if len(stem_ext) == 2 else f"{path}.{mode}"


def setup_run_log():
    """挂钩 log.info/warn/error：平台日志照常 + 追加到 g.run_log_buffer。"""
    if _log is None or _g is None:
        return
    if getattr(_g, "_run_log_ready", False):
        return
    if not _enabled:
        _g._run_log_ready = True
        return

    existing = getattr(_g, "run_log_buffer", None)
    _g.run_log_buffer = list(existing) if isinstance(existing, list) else []

    orig_info = _log.info
    orig_warn = getattr(_log, "warn", None) or _log.info
    orig_err = getattr(_log, "error", _log.info)

    def _tee(prefix, orig):
        def _wrapper(msg, *args, **kwargs):
            try:
                text = msg % args if args else str(msg)
            except Exception:
                text = str(msg)
            ts = ""
            try:
                ctx = _get_context() if _get_context else None
                if ctx is not None:
                    ts = ctx.current_dt.strftime("%Y-%m-%d %H:%M") + " "
            except Exception:
                pass
            buf = getattr(_g, "run_log_buffer", None)
            if isinstance(buf, list):
                buf.append(f"{ts}{prefix}{text}")
            return orig(msg, *args, **kwargs)
        return _wrapper

    _log.info = _tee("", orig_info)
    if hasattr(_log, "warn"):
        _log.warn = _tee("[WARN] ", orig_warn)
    if hasattr(_log, "warning"):
        _log.warning = _tee("[WARN] ", getattr(_log, "warning", orig_warn))
    _log.error = _tee("[ERROR] ", orig_err)
    _g._run_log_ready = True


def flush_run_log(context=None):
    """盘后 write_file 落盘；context 参数保留兼容，实际用 bind 的 get_context。"""
    if not _enabled or _write_file is None or _g is None:
        return
    setup_run_log()
    buf = getattr(_g, "run_log_buffer", None)
    if not buf:
        return
    if _tail_lines and len(buf) > _tail_lines:
        buf = buf[-_tail_lines:]
        _g.run_log_buffer = list(buf)
    path = _resolve_log_path(_path)
    content = "\n".join(buf) + "\n"
    try:
        if _append_intraday:
            _write_file(path, content, append=True)
            _g.run_log_buffer = []
        else:
            _write_file(path, content, append=False)
        _log.info("📝 【日志落盘】write_file → %s (共 %d 行)" % (path, len(buf)))
    except Exception as e:
        _log.info("📝 【日志落盘】write_file 异常: %s" % e)
