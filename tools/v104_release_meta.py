from pathlib import Path
import json
R=Path(__file__).resolve().parents[1]
def text(path): return (R/path).read_text()
def put(path,data): (R/path).write_text(data)
def one(path,a,b):
 s=text(path)
 if s.count(a)!=1: raise SystemExit(f'{path}: {s.count(a)} matches for {a[:60]!r}')
 put(path,s.replace(a,b,1))
put('VERSION','1.0.4\n'); put('panel/VERSION','1.0.4\n'); put('agent/VERSION','1.0.4\n')
one('panel/app/__init__.py','__version__ = "1.0.3"','__version__ = "1.0.4"')
one('agent/natvps_agent/__init__.py','__version__ = "1.0.3"','__version__ = "1.0.4"')
meta=json.loads(text('release.json')); meta.update(release_version='1.0.4',panel_version='1.0.4',agent_version='1.0.4',agent_api_version='2',mobile_api_version='1'); put('release.json',json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
one('panel/app/main.py','"version": "1.0.3"','"version": "1.0.4"')
one('panel/app/templates/base.html','XNAT v1.0.3 Multi-Node','XNAT v1.0.4 Multi-Node')
one('README.md','**当前正式版本：v1.0.3**','**当前正式版本：v1.0.4**')
s=text('README.md').replace('更新 Panel 到 v1.0.3','更新 Panel 到 v1.0.4').replace('更新 Host Agent 到 v1.0.3','更新 Host Agent 到 v1.0.4').replace('Panel 已经是 v1.0.3','Panel 已经是 v1.0.4'); put('README.md',s)
s=text('scripts/upgrade-panel.sh'); a='  1.0.2) UPGRADE_PATH="verified-v1.0.2" ;;\n'; b='  1.0.3) UPGRADE_PATH="verified-v1.0.3" ;;\n'+a
if a not in s: raise SystemExit('upgrade anchor'); put('scripts/upgrade-panel.sh',s.replace(a,b,1))
s=text('docs/INTRODUCTION.md'); s=s.replace('| XNAT Release | v1.0.3 |','| XNAT Release | v1.0.4 |').replace('| XNAT Panel | v1.0.3 |','| XNAT Panel | v1.0.4 |').replace('| XNAT Host Agent | v1.0.3 |','| XNAT Host Agent | v1.0.4 |')
needle='## 版本说明\n\n'; addition='## 版本说明\n\nv1.0.4 新增服务器实时资源监控：服务器详情页在原有概览下方增加一个整体监控区域，以 2×2 对称布局展示 CPU、内存、硬盘和实时上下行网络速率。CPU / 内存 / 硬盘使用全圆角胶囊进度条。Web 每 5 秒刷新，页面进入后台后停止轮询；Host Agent 仅保留数秒内存采样，不写监控数据库、不保存历史。\n\nAgent API 继续保持 v2，Mobile API 继续保持 v1；Mobile API v1 可通过服务器详情接口的 `?metrics=1` 获取同一套实时指标，为后续 Android 接入保留兼容能力。\n\n'
if needle not in s: raise SystemExit('intro version anchor')
s=s.replace(needle,addition,1)
s=s.replace('## 更新记录\n\n### v1.0.3','## 更新记录\n\n### v1.0.4\n\n- 服务器详情新增 CPU / 内存 / 硬盘 / 实时下载与上传速率。\n- 一个整体监控容器，内部 2×2 对称布局；CPU、内存、硬盘使用全圆角胶囊进度条。\n- 页面每 5 秒刷新，不可见时停止请求。\n- Host Agent 使用约 3 秒内存短缓存；CPU 与网速通过相邻采样计算，磁盘必要时低频兜底采样。\n- 不写监控数据库、不保存历史曲线。\n- Panel Web 与 Mobile API v1 共用同一套实时指标来源。\n- v1.0.3 → v1.0.4 为正式验证的直接 Panel 升级路径。\n\n### v1.0.3',1)
put('docs/INTRODUCTION.md',s)
s=text('CHANGELOG.md'); entry='''## v1.0.4 - 2026-09-14\n\n- XNAT Release 1.0.4 / Panel 1.0.4 / Host Agent 1.0.4；Agent API 保持 v2，Mobile API 保持 v1。\n- 服务器详情新增一个整体实时资源监控区域，以 2×2 对称布局显示 CPU、内存、硬盘与实时下载 / 上传速率。\n- CPU、内存、硬盘使用全圆角胶囊进度条；网络速率单独显示。\n- Web 每 5 秒刷新，页面不可见时停止轮询。\n- Host Agent 只使用内存短缓存和相邻采样，不写监控数据库、不保存历史。\n- v1.0.3 → v1.0.4 为正式验证的直接 Panel 升级路径。\n\n**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**\n\n'''
if not s.startswith('# Changelog\n\n'): raise SystemExit('changelog header')
put('CHANGELOG.md',s.replace('# Changelog\n\n','# Changelog\n\n'+entry,1))
s=text('docs/MOBILE_API.md').replace('XNAT Panel v1.0.3','XNAT Panel v1.0.4')
if '?metrics=1' not in s: s += '\n\n## 实时资源监控（v1.0.4）\n\n`GET /api/v1/servers/{server_id}?metrics=1` 返回当前 CPU、内存、硬盘和上下行网络速率。该能力属于 Mobile API v1 的向后兼容扩展，服务端不保存监控历史。\n'
put('docs/MOBILE_API.md',s)
