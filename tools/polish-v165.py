from pathlib import Path

p = Path('scripts/install-host.sh')
s = p.read_text()

old = '''apt-get update
apt-get install -y incus
systemctl enable --now incus
apt-get clean
rm -f /tmp/xnat-zabbly.asc
sleep 2

# This resource read happens after the large package installation and cache
# cleanup, so MAX_SAFE_GB is based on the space a real installed Host has.
select_virtualization_mode
'''
new = '''apt-get update
apt-get install -y incus
systemctl enable --now incus
sleep 2

# Install the Host Agent runtime before sizing natpool as well. The Python
# virtualenv and dependencies live on /, outside natpool, so they must be part
# of the real installed-Host baseline rather than consuming the reserve later.
install -d -m 0755 /opt/xnat
rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"
cp -a "${SRC_DIR}/." "${DEST_DIR}/"
cd "${DEST_DIR}"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
deactivate
cd "${REPO_ROOT}"

apt-get clean
rm -f /tmp/xnat-zabbly.asc

# This resource read happens after Incus and the Agent runtime are installed
# and the APT download cache is cleaned. MAX_SAFE_GB therefore protects the
# requested long-running Host reserve after the actual software footprint.
select_virtualization_mode
'''
assert s.count(old) == 1
s = s.replace(old, new, 1)

old_late = '''info "5/7 安装 XNAT Host Agent"
install -d -m 0755 /opt/xnat
rm -rf "${DEST_DIR}"
mkdir -p "${DEST_DIR}"
cp -a "${SRC_DIR}/." "${DEST_DIR}/"

cd "${DEST_DIR}"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

PUBLIC_IP="$(curl -4fsS --max-time 10 https://api.ipify.org || true)"
'''
new_late = '''info "5/7 配置 XNAT Host Agent"
PUBLIC_IP="$(curl -4fsS --max-time 10 https://api.ipify.org || true)"
'''
assert s.count(old_late) == 1
s = s.replace(old_late, new_late, 1)

plan = '\nselect_virtualization_mode\nRECOMMENDED_GB="${MAX_SAFE_GB}"\n'
plan_pos = s.index(plan)
assert s.index('apt-get install -y incus') < plan_pos
assert s.index('python -m pip install -r requirements.txt') < plan_pos
assert s.count('python -m pip install -r requirements.txt') == 1
assert 'info "5/7 安装 XNAT Host Agent"' not in s
assert 'info "5/7 配置 XNAT Host Agent"' in s
p.write_text(s)

# Tighten README wording so the stated reserve really means after the Agent runtime exists.
readme_p = Path('README.md')
readme = readme_p.read_text()
readme = readme.replace(
    '先安装 Incus/LVM/Python 等基础依赖并清理 APT 缓存，再按真实剩余空间计算 natpool；',
    '先安装 Incus/LVM/Python 与 Host Agent Runtime 并清理 APT 缓存，再按真实剩余空间计算 natpool；',
    1,
)
readme = readme.replace(
    '先完成 Host 基础依赖安装，再按真实剩余空间计算 natpool；natpool 满载后仍为 Host 保留约 2GiB',
    '先完成 Host 基础依赖与 Agent Runtime 安装，再按真实剩余空间计算 natpool；natpool 满载后仍为 Host 保留约 2GiB',
    1,
)
readme = readme.replace(
    '依赖安装完成后再计算，natpool 满载后仍为 Host 保留约 3GiB，natpool 至少 4GiB',
    '依赖与 Agent Runtime 安装完成后再计算，natpool 满载后仍为 Host 保留约 3GiB，natpool 至少 4GiB',
    1,
)
readme = readme.replace(
    '完成 Host 基础依赖 / Incus 安装后重新读取真实可用硬盘，再计算 natpool 安全上限。',
    '完成 Host 基础依赖 / Incus / Agent Runtime 安装后重新读取真实可用硬盘，再计算 natpool 安全上限。',
    1,
)
readme = readme.replace(
    '重型依赖安装完成后才读取根分区真实剩余空间；',
    'Incus 与 Host Agent Runtime 安装完成后才读取根分区真实剩余空间；',
    1,
)
readme_p.write_text(readme)

changelog_p = Path('CHANGELOG.md')
changelog = changelog_p.read_text()
changelog = changelog.replace(
    '全新 Host 现在先完成基础依赖与 Incus 安装、清理 APT 下载缓存，再重新读取根分区真实可用空间后计算 natpool。',
    '全新 Host 现在先完成基础依赖、Incus 与 Host Agent Runtime 安装、清理 APT 下载缓存，再重新读取根分区真实可用空间后计算 natpool。',
    1,
)
changelog_p.write_text(changelog)

check_p = Path('scripts/check.sh')
check = check_p.read_text()
old_check = '''assert s.index('apt-get install -y incus') < plan_pos
assert s.index('apt-get clean') < plan_pos
assert s.count('apt-get install -y incus') == 1, 'Incus dependency install duplicated'
'''
new_check = '''assert s.index('apt-get install -y incus') < plan_pos
assert s.index('python -m pip install -r requirements.txt') < plan_pos
assert s.index('apt-get clean') < plan_pos
assert s.count('apt-get install -y incus') == 1, 'Incus dependency install duplicated'
assert s.count('python -m pip install -r requirements.txt') == 1, 'Agent runtime install duplicated'
'''
assert check.count(old_check) == 1
check = check.replace(old_check, new_check, 1)
check = check.replace(
    "assert 'info \"1/7 安装系统 / Incus 依赖\"' not in s, 'old post-planning dependency block returned'\n",
    "assert 'info \"1/7 安装系统 / Incus 依赖\"' not in s, 'old post-planning dependency block returned'\nassert 'info \"5/7 配置 XNAT Host Agent\"' in s\n",
    1,
)
check_p.write_text(check)
