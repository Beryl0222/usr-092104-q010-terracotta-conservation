"""分级授权。

角色与许可：
- duty_officer / conservator / registrar（现场与库房职工）：
  可见全部运行数据，含敏感坑位坐标与未发布发现；
- researcher（研究者）：默认可见已发布资料；
  经授权追加 ``unpublished_finds`` 才能查看未发布发现，
  追加 ``sensitive_locations`` 才能查看敏感坑位坐标；
- public（公众）：仅已发布对象的公开字段，任何情况下都看不到
  敏感坑位坐标、未发布发现、内部处置备注与精确库位。

授权失败统一抛 :class:`AccessDenied`；普通列表接口对不可见对象
直接不返回，而不是返回半截数据。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.contract import (
    ROLE_CONSERVATOR, ROLE_DUTY_OFFICER, ROLE_PUBLIC, ROLE_REGISTRAR,
    ROLE_RESEARCHER, ROLE_SYSTEM, SCOPE_SENSITIVE_LOCATION,
    SCOPE_UNPUBLISHED,
)

STAFF_ROLES = frozenset({ROLE_DUTY_OFFICER, ROLE_CONSERVATOR, ROLE_REGISTRAR})


class AccessDenied(Exception):
    """当前身份无权查看所请求的资料。"""


@dataclass(frozen=True)
class AccessContext:
    role: str
    scopes: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def staff(cls, role: str) -> "AccessContext":
        if role not in STAFF_ROLES:
            raise ValueError(f"非现场职工角色：{role}")
        return cls(role=role, scopes=frozenset(
            {SCOPE_UNPUBLISHED, SCOPE_SENSITIVE_LOCATION}))

    @property
    def is_staff(self) -> bool:
        return self.role in STAFF_ROLES or self.role == ROLE_SYSTEM

    @property
    def can_see_unpublished(self) -> bool:
        return self.is_staff or SCOPE_UNPUBLISHED in self.scopes

    @property
    def can_see_sensitive_locations(self) -> bool:
        return self.is_staff or SCOPE_SENSITIVE_LOCATION in self.scopes


def context(role: str, scopes: set[str] | frozenset[str] | None = None) -> AccessContext:
    return AccessContext(role=role, scopes=frozenset(scopes or ()))


def can_view_object(ctx: AccessContext, obj: dict) -> bool:
    if obj.get("publication_state") == "published":
        return True
    return ctx.can_see_unpublished


def require_object_visible(ctx: AccessContext, obj: dict) -> None:
    if not can_view_object(ctx, obj):
        # 未发布发现对普通接口表现为“查无此项”，避免存在性泄露
        raise AccessDenied("无权查看该对象（未发布发现）")


def trench_view(ctx: AccessContext, trench: dict) -> dict:
    """探方视图：敏感坑位坐标按许可裁剪。"""
    view = {k: v for k, v in trench.items() if k != "grid_coordinates"}
    if trench.get("sensitive") and not ctx.can_see_sensitive_locations:
        view["grid_coordinates"] = None
        view["location_restricted"] = True
    else:
        view["grid_coordinates"] = trench.get("grid_coordinates")
    return view


def object_summary(ctx: AccessContext, obj: dict) -> dict:
    """对象对外摘要；公众视图只保留稳定公开字段。"""
    require_object_visible(ctx, obj)
    if ctx.is_staff:
        return dict(obj)
    base = {
        "object_id": obj["object_id"],
        "object_kind": obj["object_kind"],
        "figure_id": obj.get("figure_id"),
        "status": obj.get("status"),
        "publication_state": obj.get("publication_state"),
        "lifted_at": obj.get("lifted_at"),
        "accession_code": obj.get("accession_code") if ctx.role != ROLE_PUBLIC else None,
    }
    return {k: v for k, v in base.items() if v is not None}
