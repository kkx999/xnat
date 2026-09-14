from __future__ import annotations

from dataclasses import dataclass


class ImagePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class ImageRequirement:
    min_disk_gb: float
    reason: str


def minimum_disk_for_alias(alias: str, family: str = "", virtualization_type: str = "lxc") -> ImageRequirement:
    alias_l = str(alias or "").strip().lower()
    family_l = str(family or "").strip().lower()
    mode = str(virtualization_type or "lxc").strip().lower()

    if alias_l.startswith("images:alpine/") or family_l == "alpine":
        base = ImageRequirement(1.0, "Alpine 精简镜像")
    elif alias_l.startswith("images:ubuntu/"):
        base = ImageRequirement(2.0, "Ubuntu 镜像")
    elif alias_l.startswith("images:debian/") or family_l == "apt":
        base = ImageRequirement(2.0, "Debian/Ubuntu 类镜像")
    else:
        base = ImageRequirement(1.0, "通用 LXC 镜像")

    if mode == "kvm" and base.min_disk_gb < 4.0:
        return ImageRequirement(4.0, "KVM 虚拟机")
    return base


def image_min_disk_gb(image, virtualization_type: str = "lxc") -> float:
    configured = float(getattr(image, "min_disk_gb", 0) or 0)
    inferred = minimum_disk_for_alias(
        getattr(image, "alias", ""),
        getattr(image, "family", ""),
        virtualization_type,
    ).min_disk_gb
    if str(virtualization_type or "lxc").strip().lower() == "kvm":
        return max(configured, inferred, 4.0)
    return max(configured, inferred)


def validate_image_resources(image, disk_gb: float, virtualization_type: str = "lxc") -> None:
    required = image_min_disk_gb(image, virtualization_type)
    current = float(disk_gb or 0)
    if current + 1e-9 < required:
        name = str(getattr(image, "name", None) or getattr(image, "alias", None) or "该系统镜像")
        raise ImagePolicyError(
            f"{name} 最低需要 {required:g} GiB 系统盘，当前配置为 {current:g} GiB。"
        )
