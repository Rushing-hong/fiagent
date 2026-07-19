from pathlib import Path

class PathError(ValueError):
    pass


# 工作区内仍禁止读写的敏感文件名（小写比较）
_BLOCKED_NAMES = frozenset({
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.staging",
    ".env.test",
    "credentials.json",
    "credentials.csv",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    "service_account.json",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
})
_BLOCKED_SUFFIXES = (".pem", ".key", ".p12", ".pfx")


def _is_blocked_secret(resolved: Path, root: Path) -> bool:
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        return True
    for part in rel.parts:
        low = part.lower()
        if low in _BLOCKED_NAMES or low.startswith(".env"):
            return True
        if low.endswith(_BLOCKED_SUFFIXES):
            return True
    name = resolved.name.lower()
    if name in _BLOCKED_NAMES or name.startswith(".env"):
        return True
    if name.endswith(_BLOCKED_SUFFIXES):
        return True
    return False


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
    if _is_blocked_secret(resolved, root):
        raise PathError(f"拒绝访问敏感文件: {path}")
    return resolved


def format_lines(content: str, start_line: int = 1) -> str:
    lines = content.splitlines()
    width = len(str(start_line + len(lines) - 1)) if lines else 1
    formatted = []
    for i, line in enumerate(lines, start=start_line):
        formatted.append(f"{i:>{width}}|{line}")
    return "\n".join(formatted)
