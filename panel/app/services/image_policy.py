from __future__ import annotations

from dataclasses import dataclass

DEFAULT_IMAGE_MIN_DISK_GB = 1.0
KVM_MIN_DISK_GB = 3.0

class ImagePolicyError(ValueError):
    pass

@dataclass(frozen=True)
class ImageRequirement:
    min_disk_gb: float
    reason: str

def minimum_disk_for_alias(alias: str, family: str = "", virtualization_type: str = "lxc") -> ImageRequirement:
    mode = str(virtualization_type or "lxc").strip().lower()
    required = KVM_MIN_DISK_GB if mode == "kvm" else DEFAULT_IMAGE_MIN_DISK_GB
    return ImageRequirement(required, "KVM 全局最低系统盘" if mode == "kvm" else "系统镜像后台配置")

def image_min_disk_gb(image, virtualization_type: str = "lxc") -> float:
    configured = max(float(getattr(image, "min_disk_gb", 0) or DEFAULT_IMAGE_MIN_DISK_GB), 0.125)
    if str(virtualization_type or "lxc").strip().lower() == "kvm":
        return max(configured, KVM_MIN_DISK_GB)
    return configured

def validate_image_resources(image, disk_gb: float, virtualization_type: str = "lxc") -> None:
    required = image_min_disk_gb(image, virtualization_type)
    current = float(disk_gb or 0)
    if current + 1e-9 < required:
        name = str(getattr(image, "name", None) or getattr(image, "alias", None) or "该系统镜像")
        raise ImagePolicyError(f"{name} 最低需要 {required:g} GiB 系统盘，当前配置为 {current:g} GiB。")
