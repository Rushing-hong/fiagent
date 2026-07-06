from pathlib import Path


class PathError(ValueError):
    pass


def resolve_path(ctx, path: str) -> Path:
    if not path or not str(path).strip():
        raise PathError("路径不能为空")
    raw = Path(path)
    if not raw.is_absolute():
        raw = ctx.root / raw
    resolved = raw.resolve()
    root = ctx.root.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise PathError(f"路径超出工作区: {path}") from None
    return resolved


def format_lines(content: str, start_line: int = 1) -> str:
    lines = content.splitlines()
    width = len(str(start_line + len(lines) - 1)) if lines else 1
    formatted = []
    for i, line in enumerate(lines, start=start_line):
        formatted.append(f"{i:>{width}}|{line}")
    return "\n".join(formatted)
