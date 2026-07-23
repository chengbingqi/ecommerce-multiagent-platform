def validate_admin_password(password: str) -> str:
    """Enforce the local/production administrator bootstrap password policy."""
    categories = (
        any(char.islower() for char in password),
        any(char.isupper() for char in password),
        any(char.isdigit() for char in password),
        any(not char.isalnum() for char in password),
    )
    if len(password) < 12 or sum(categories) < 3:
        raise RuntimeError("密码至少12位，并至少包含大小写字母、数字、特殊字符中的三类")
    return password
