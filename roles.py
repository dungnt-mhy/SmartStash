"""Role definitions and permission helpers for the article-workflow bot.

4 permission tiers, 5 titles:
    admin          Admin                  -- owner / root
    deputy_admin   Phó Admin
    editor         Biên tập viên
    writer         Nhân viên viết bài
    contributor    Cộng tác viên          -- same tier as writer, but the
                                             intake is restricted to
                                             photos/documents + a short hint
                                             (enforced in handlers, not here)

A user row with ``role IS NULL`` is a *pending* user: they have run /start
but nobody has granted them a role yet.  Pending users cannot create tasks.
"""

from __future__ import annotations

ROLE_ADMIN = "admin"
ROLE_DEPUTY = "deputy_admin"
ROLE_EDITOR = "editor"
ROLE_WRITER = "writer"
ROLE_CONTRIBUTOR = "contributor"

ALL_ROLES = (ROLE_ADMIN, ROLE_DEPUTY, ROLE_EDITOR, ROLE_WRITER, ROLE_CONTRIBUTOR)

ROLE_TITLES = {
    ROLE_ADMIN: "Admin",
    ROLE_DEPUTY: "Phó Admin",
    ROLE_EDITOR: "Biên tập viên",
    ROLE_WRITER: "Nhân viên viết bài",
    ROLE_CONTRIBUTOR: "Cộng tác viên",
}

ROLE_DESCRIPTIONS = {
    ROLE_ADMIN: (
        "Toàn quyền: cấp/thu hồi quyền ngay trong Telegram, duyệt cuối, "
        "bấm đăng public lên website + Facebook + Instagram, xem nhật ký."
    ),
    ROLE_DEPUTY: (
        "Cấp/thu hồi quyền cho Nhân viên viết bài và Cộng tác viên, duyệt "
        "nội dung. Chỉ được đăng bài khi Admin bật quyền đăng (can_publish)."
    ),
    ROLE_EDITOR: (
        "Sửa bài nháp, duyệt nội dung hoặc trả lại kèm lý do. Không tự "
        "đăng, trừ khi Admin bật quyền đăng (can_publish) cho chuyên mục."
    ),
    ROLE_WRITER: (
        "Gửi ảnh/slide/gợi ý để Muse viết bài, hoặc tự gửi bản thảo của "
        "mình. Tạo bài nháp, sửa bài của chính mình, gửi duyệt. "
        "Không duyệt bài người khác, không đăng."
    ),
    ROLE_CONTRIBUTOR: (
        "Chỉ gửi ảnh/tư liệu kèm gợi ý ngắn cho Muse làm bài. "
        "Không duyệt, không đăng public."
    ),
}

#: Roles that may review (approve / reject) submitted tasks.
REVIEWER_ROLES = (ROLE_ADMIN, ROLE_DEPUTY, ROLE_EDITOR)

#: Roles each actor role is allowed to grant / revoke.
GRANTABLE = {
    ROLE_ADMIN: ALL_ROLES,
    ROLE_DEPUTY: (ROLE_WRITER, ROLE_CONTRIBUTOR),
}


def role_title(role: str | None) -> str:
    if role is None:
        return "Chờ cấp quyền"
    return ROLE_TITLES.get(role, role)


def roles_overview_text() -> str:
    """Vietnamese role list used by /vaitro."""
    lines = ["📋 Danh sách vai trò:", ""]
    for idx, role in enumerate(ALL_ROLES, start=1):
        lines.append(f"{idx}. {ROLE_TITLES[role]} — {ROLE_DESCRIPTIONS[role]}")
    lines.append("")
    lines.append(
        "Lưu ý: Cộng tác viên chỉ gửi ảnh/tư liệu cho Muse làm bài, "
        "không được duyệt và không được public bài."
    )
    return "\n".join(lines)


def can_grant(actor_role: str | None, target_role: str) -> bool:
    """May ``actor_role`` grant ``target_role`` to someone?"""
    if actor_role is None:
        return False
    return target_role in GRANTABLE.get(actor_role, ())


def can_revoke(actor_role: str | None, current_role: str | None) -> bool:
    """May ``actor_role`` revoke a user currently holding ``current_role``?"""
    if actor_role is None or current_role is None:
        return False
    if actor_role == ROLE_ADMIN:
        return current_role != ROLE_ADMIN  # never revoke the (root) admin here
    return current_role in GRANTABLE.get(actor_role, ())


def can_review(role: str | None) -> bool:
    return role in REVIEWER_ROLES


def can_publish(user) -> bool:
    """``user`` is a storage row/dict with ``role`` and ``can_publish``."""
    if user is None:
        return False
    role = user["role"] if not isinstance(user, dict) else user.get("role")
    flag = user["can_publish"] if not isinstance(user, dict) else user.get("can_publish")
    if role == ROLE_ADMIN:
        return True
    if role in (ROLE_DEPUTY, ROLE_EDITOR):
        return bool(flag)
    return False
