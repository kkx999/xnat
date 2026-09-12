from pathlib import Path
import json


def read(path):
    return Path(path).read_text(encoding='utf-8')


def write(path, text):
    Path(path).write_text(text, encoding='utf-8')


def replace_once(path, old, new):
    text = read(path)
    if old not in text:
        raise SystemExit(f'anchor missing in {path}: {old[:120]!r}')
    write(path, text.replace(old, new, 1))

# Version metadata: Panel/release bump only; Host Agent/API remain unchanged.
write('VERSION', '1.6.1\n')
write('panel/VERSION', '1.6.1\n')
replace_once('panel/app/__init__.py', '__version__ = "1.6.0"', '__version__ = "1.6.1"')
replace_once('panel/app/main.py', '"version": "1.6.0"', '"version": "1.6.1"')
replace_once('panel/app/templates/base.html', 'XNAT v1.6.0 Multi-Node', 'XNAT v1.6.1 Multi-Node')

meta = json.loads(read('release.json'))
meta['release_version'] = '1.6.1'
meta['panel_version'] = '1.6.1'
assert meta['agent_version'] == '1.2.0'
assert str(meta['agent_api_version']) == '1'
write('release.json', json.dumps(meta, ensure_ascii=False, indent=2) + '\n')

# Keep the upgrade path explicitly verified from the immediately previous Panel.
replace_once(
    'scripts/upgrade-panel.sh',
    'case "$CURRENT_VERSION" in\n  1.5.0) UPGRADE_PATH="verified-v1.5.0" ;;',
    'case "$CURRENT_VERSION" in\n  1.6.0) UPGRADE_PATH="verified-v1.6.0" ;;\n  1.5.0) UPGRADE_PATH="verified-v1.5.0" ;;',
)

# Panel-side capacity estimator. It deliberately uses the same conservative
# logical/physical capacity already used by scheduling and does not require a
# Host Agent/API change.
nodes_anchor = '''def select_host_for_plan(db, plan: Plan) -> HostNode:\n'''
nodes_helper = r'''def host_plan_capacity_estimates(db, host: HostNode, plans) -> list[dict]:
    """Estimate how many additional VPS instances this Host can accept per active plan.

    This is a Panel-only display helper. It uses the same Host scheduling state
    and conservative remaining memory/storage values as real placement, then
    also caps by max_vps and remaining NAT ports (one SSH port is required per
    newly provisioned instance). CPU stays a live watermark just like the
    scheduler; without changing Agent API v1 we intentionally do not invent a
    physical-core count.
    """
    rows = [plan for plan in (plans or []) if bool(getattr(plan, "is_active", False))]
    if not rows:
        return []

    plan_ids = [int(plan.id) for plan in rows]
    bindings: dict[int, set[int]] = {plan_id: set() for plan_id in plan_ids}
    links = db.execute(
        select(PlanHost.plan_id, PlanHost.host_id).where(
            PlanHost.plan_id.in_(plan_ids),
            PlanHost.enabled == True,
        )
    ).all()
    for plan_id, host_id in links:
        bindings.setdefault(int(plan_id), set()).add(int(host_id))

    active_count = host_active_server_count(db, host.id)
    max_vps_remaining = max(0, int(host.max_vps) - active_count) if int(host.max_vps or 0) > 0 else None
    port_stats = host_port_pool_stats(db, host)
    port_remaining = int(port_stats.get("remaining") or 0) if port_stats.get("configured") else 0

    estimates: list[dict] = []
    for plan in rows:
        bound_hosts = bindings.get(int(plan.id), set())
        # Existing XNAT semantics: a plan with no explicit Host links is global.
        if bound_hosts and int(host.id) not in bound_hosts:
            continue

        mode = str(plan.virtualization_type or "lxc").strip().lower()
        spec = f"{int(plan.cpu or 0)}C / {int(plan.memory_mb or 0)}MB / {float(plan.disk_gb or 0):g}GB"
        state = host_schedule_state(db, host, plan)
        item = {
            "plan_id": int(plan.id),
            "plan_name": str(plan.name),
            "mode": mode,
            "spec": spec,
            "count": 0,
            "limiter": "-",
            "reason": str(state.get("reason") or "当前不可调度"),
        }
        if not state.get("allowed"):
            estimates.append(item)
            continue

        cap = state.get("capacity") or {}
        if not int(host.memory_total_mb or 0) or not float(host.storage_total_gb or 0):
            item["reason"] = "节点资源数据尚未完整上报"
            estimates.append(item)
            continue

        memory_mb = max(1, int(plan.memory_mb or 0))
        disk_gb = max(0.001, float(plan.disk_gb or 0))
        limits = {
            "内存": max(0, int(float(cap.get("remaining_memory_mb") or 0) // memory_mb)),
            "存储": max(0, int(float(cap.get("remaining_disk_gb") or 0) // disk_gb)),
            "NAT端口": max(0, port_remaining),
        }
        if max_vps_remaining is not None:
            limits["VPS上限"] = max_vps_remaining

        count = min(limits.values()) if limits else 0
        limiting = [name for name, value in limits.items() if value == count]
        item["count"] = max(0, int(count))
        item["limiter"] = " / ".join(limiting) if limiting else "-"
        item["reason"] = "" if count > 0 else f"{item['limiter']}余量不足"
        estimates.append(item)

    return estimates


'''
replace_once('panel/app/nodes.py', nodes_anchor, nodes_helper + nodes_anchor)
replace_once('panel/app/nodes.py', '"User-Agent": "XNAT-Panel/1.5.0",', '"User-Agent": "XNAT-Panel/1.6.1",')

# Import and calculate once on the nodes admin page.
replace_once(
    'panel/app/main.py',
    'HostAPIError, allocate_host_port, host_request, host_summary, host_port_pool_stats, host_schedule_state,\n    refresh_all_hosts, refresh_host, select_host_for_plan,',
    'HostAPIError, allocate_host_port, host_request, host_summary, host_port_pool_stats, host_schedule_state, host_plan_capacity_estimates,\n    refresh_all_hosts, refresh_host, select_host_for_plan,',
)
replace_once(
    'panel/app/main.py',
    'dashboard_recent_orders=[]; dashboard_recent_servers=[]; dashboard_stock=[]\n        inventories={}; user_stats={}; site_settings={}; backup_rows=[]; backup_preview=None; backup_total_bytes=0; plan_host_map={}; host_port_stats={}; host_schedule_states={}',
    'dashboard_recent_orders=[]; dashboard_recent_servers=[]; dashboard_stock=[]\n        inventories={}; user_stats={}; site_settings={}; backup_rows=[]; backup_preview=None; backup_total_bytes=0; plan_host_map={}; host_port_stats={}; host_schedule_states={}; host_plan_estimates={}',
)
replace_once(
    'panel/app/main.py',
    'host_schedule_states = {row.id: host_schedule_state(db, row) for row in hosts}\n            links = db.scalars(select(PlanHost).where(PlanHost.enabled == True)).all()',
    'host_schedule_states = {row.id: host_schedule_state(db, row) for row in hosts}\n            host_plan_estimates = {row.id: host_plan_capacity_estimates(db, row, plans) for row in hosts}\n            links = db.scalars(select(PlanHost).where(PlanHost.enabled == True)).all()',
)
replace_once(
    'panel/app/main.py',
    'inventories=inventories,user_stats=user_stats,site_settings=site_settings,payment_cfg=payment_cfg,env_status=env_status,deployment=deployment,plan_host_map=plan_host_map,host_port_stats=host_port_stats,host_schedule_states=host_schedule_states,',
    'inventories=inventories,user_stats=user_stats,site_settings=site_settings,payment_cfg=payment_cfg,env_status=env_status,deployment=deployment,plan_host_map=plan_host_map,host_port_stats=host_port_stats,host_schedule_states=host_schedule_states,host_plan_estimates=host_plan_estimates,',
)

# Visible, non-collapsible UI: summary directly after virtualization plus an
# always-visible per-plan capacity strip. No details/accordion hides this data.
tpl = 'panel/app/templates/admin.html'
replace_once(
    tpl,
    '''        {% set hs = host_summary(h) if host_summary is defined else {} %}\n        {% set sched = host_schedule_states.get(h.id, {}) %}\n        <article class="node-admin-card''',
    '''        {% set hs = host_summary(h) if host_summary is defined else {} %}\n        {% set sched = host_schedule_states.get(h.id, {}) %}\n        {% set estimates = host_plan_estimates.get(h.id, []) %}\n        <article class="node-admin-card''',
)
replace_once(
    tpl,
    '''            <div><span>虚拟化</span><strong>{% if "lxc" in (hs.get("virtualization_modes") or []) and "kvm" in (hs.get("virtualization_modes") or []) %}LXC + KVM{% elif "kvm" in (hs.get("virtualization_modes") or []) %}KVM{% else %}LXC{% endif %}</strong><small>{% if "kvm" in (hs.get("virtualization_modes") or []) %}KVM {{ "可用" if hs.get("kvm_available") else "当前不可用" }}{% else %}LXC Host{% endif %}</small></div>\n          </div>\n\n          {% set cap = sched.get('capacity', {}) %}''',
    '''            <div><span>虚拟化</span><strong>{% if "lxc" in (hs.get("virtualization_modes") or []) and "kvm" in (hs.get("virtualization_modes") or []) %}LXC + KVM{% elif "kvm" in (hs.get("virtualization_modes") or []) %}KVM{% else %}LXC{% endif %}</strong><small>{% if "kvm" in (hs.get("virtualization_modes") or []) %}KVM {{ "可用" if hs.get("kvm_available") else "当前不可用" }}{% else %}LXC Host{% endif %}</small></div>\n            <div class="node-estimate-metric"><span>预计可开</span>{% if estimates %}{% set best = estimates|max(attribute='count') %}<strong>≈ {{ best.count }} 台</strong><small>{{ best.plan_name }} · 当前资源</small>{% else %}<strong>0 台</strong><small>暂无在售可调度套餐</small>{% endif %}</div>\n          </div>\n\n          <div class="node-plan-capacity-visible">\n            <div class="node-plan-capacity-head"><div><strong>按套餐预计可开</strong><span>当前实时余量，直接显示</span></div><small>内存 / 存储 / VPS上限 / NAT端口取最小值；CPU按调度水位实时判定</small></div>\n            <div class="node-plan-capacity-grid">\n              {% for item in estimates %}\n              <div class="node-plan-capacity-item {% if item.count <= 0 %}blocked{% endif %}">\n                <div><strong>{{ item.plan_name }}</strong><small>{{ item.mode|upper }} · {{ item.spec }}</small></div>\n                <div class="node-plan-capacity-count"><b>≈ {{ item.count }} 台</b><small>{% if item.count > 0 %}瓶颈：{{ item.limiter }}{% else %}{{ item.reason }}{% endif %}</small></div>\n              </div>\n              {% else %}\n              <div class="node-plan-capacity-empty">暂无在售且可作用于此节点的套餐</div>\n              {% endfor %}\n            </div>\n          </div>\n\n          {% set cap = sched.get('capacity', {}) %}''',
)

# Compact always-visible styling, responsive and compatible with both admin themes.
css_add = r'''

/* XNAT v1.6.1: Host per-plan capacity is intentionally always visible. */
body.admin-body .node-plan-capacity-visible{
  margin:0 0 8px;padding:9px 10px;border:1px solid #2d4058;border-radius:8px;background:#101923;
}
body.admin-body .node-plan-capacity-head{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:7px}
body.admin-body .node-plan-capacity-head>div{display:flex;align-items:baseline;gap:8px;min-width:0}
body.admin-body .node-plan-capacity-head strong{color:#dfe8f3;font-size:11px}
body.admin-body .node-plan-capacity-head span,body.admin-body .node-plan-capacity-head>small{color:#7f8da1;font-size:8.5px;line-height:1.3}
body.admin-body .node-plan-capacity-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
body.admin-body .node-plan-capacity-item{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:46px;padding:7px 9px;border:1px solid #294a40;border-radius:7px;background:#0f211d}
body.admin-body .node-plan-capacity-item>div:first-child{min-width:0}
body.admin-body .node-plan-capacity-item strong,body.admin-body .node-plan-capacity-item small{display:block}
body.admin-body .node-plan-capacity-item strong{overflow:hidden;color:#dfe7ef;font-size:10.5px;text-overflow:ellipsis;white-space:nowrap}
body.admin-body .node-plan-capacity-item small{margin-top:2px;color:#8291a5;font-size:8.5px;line-height:1.25}
body.admin-body .node-plan-capacity-count{flex:0 0 auto;text-align:right}
body.admin-body .node-plan-capacity-count b{display:block;color:#84dfb7;font-size:13px;white-space:nowrap}
body.admin-body .node-plan-capacity-item.blocked{border-color:#4b3439;background:#20171a}
body.admin-body .node-plan-capacity-item.blocked .node-plan-capacity-count b{color:#d99aa4}
body.admin-body .node-plan-capacity-empty{grid-column:1/-1;padding:8px;color:#8490a2;font-size:9px;text-align:center}
body.admin-body .node-estimate-metric small{display:block;margin-top:2px;overflow:hidden;color:#8290a6;font-size:8px;text-overflow:ellipsis;white-space:nowrap}
html[data-admin-theme="light"] body.admin-body .node-plan-capacity-visible{background:#f2f6fa;border-color:#c4d3e1}
html[data-admin-theme="light"] body.admin-body .node-plan-capacity-head strong{color:#20374e}
html[data-admin-theme="light"] body.admin-body .node-plan-capacity-head span,
html[data-admin-theme="light"] body.admin-body .node-plan-capacity-head>small{color:#657b91}
html[data-admin-theme="light"] body.admin-body .node-plan-capacity-item{background:#edf8f3;border-color:#bddbce}
html[data-admin-theme="light"] body.admin-body .node-plan-capacity-item strong{color:#22384d}
html[data-admin-theme="light"] body.admin-body .node-plan-capacity-item small{color:#667b90}
html[data-admin-theme="light"] body.admin-body .node-plan-capacity-item.blocked{background:#fbf1f2;border-color:#e2c7cb}
@media(max-width:760px){
  body.admin-body .node-plan-capacity-head{align-items:flex-start;flex-direction:column;gap:3px}
  body.admin-body .node-plan-capacity-grid{grid-template-columns:1fr}
}
'''
write('panel/app/static/style.css', read('panel/app/static/style.css') + css_add)

# Release docs.
replace_once('README.md', '当前版本：**v1.6.0**<br>\n最新正式版本：**v1.6.0**', '当前版本：**v1.6.1**<br>\n最新正式版本：**v1.6.1**')
replace_once('README.md', '当前正式源码关系：**XNAT Release v1.6.0 / Panel v1.6.0 / Mobile API v1 / Host Agent v1.2.0 / Agent API v1**。', '当前正式源码关系：**XNAT Release v1.6.1 / Panel v1.6.1 / Mobile API v1 / Host Agent v1.2.0 / Agent API v1**。')
replace_once('README.md', '- 宿主机剩余可分配资源展示与紧凑节点管理', '- 宿主机剩余可分配资源展示、按套餐预计可开数量与紧凑节点管理')
replace_once('docs/MOBILE_API.md', 'Panel v1.6.0', 'Panel v1.6.1')

changelog = read('CHANGELOG.md')
entry = '''## v1.6.1\n\n- Host 管理卡片新增始终可见的“按套餐预计可开”容量，不需要展开任何折叠菜单。\n- 每个在售且对当前 Host 生效的套餐直接显示 LXC/KVM、CPU/内存/磁盘规格、预计可开台数与当前瓶颈。\n- 预计数量使用 Panel 已有的保守可分配内存/存储，并继续受 Host 最大 VPS、NAT 端口余量、维护/离线/虚拟化兼容与 CPU 调度水位保护。\n- 未修改 Host Agent；Host Agent 保持 v1.2.0、Agent API v1、Mobile API v1。\n- Panel 升级至 v1.6.1，支持 v1.6.0 → v1.6.1 原地升级。\n\n'''
replace_once('CHANGELOG.md', '# Changelog\n\n', '# Changelog\n\n' + entry)

# v1.6.1 release notes generated by build script.
build = read('scripts/build-release.sh')
start = build.index('cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES')
end = build.index('EOF_NOTES\n', start) + len('EOF_NOTES\n')
notes_block = '''cat > "$DIST/RELEASE_NOTES.md" <<EOF_NOTES\n# XNAT v${RELEASE_VERSION}\n\n本次版本增强 Panel 的 Host 容量可视化：按当前真实可分配资源，直接显示每个套餐预计还能开多少台小鸡。\n\n- Panel：v${PANEL_VERSION}\n- Host Agent：v${AGENT_VERSION}\n- Agent API：v${AGENT_API_VERSION}\n- Mobile API：v1\n- Host 卡片“预计可开”常驻显示，不放入折叠菜单\n- 每个在售/有效套餐显示虚拟化类型、规格、预计台数和当前瓶颈\n- 计算继续使用保守的逻辑/物理内存与存储余量，并受最大 VPS、NAT 端口、调度状态和虚拟化兼容约束\n- CPU 继续按实时调度水位保护，不伪造 Host Agent v1 未上报的物理核心总数\n- v1.6.0 → v${PANEL_VERSION} 支持原地升级，业务数据保持不变\n- Host Agent 核心保持 v${AGENT_VERSION} / Agent API v${AGENT_API_VERSION}\n\n**由 𝐍𝐀𝐌𝐄𝐋𝐄𝐒𝐒 和 GPT 倾力打造**\n\nPanel 推荐升级命令：\n\n    xnat update ${RELEASE_VERSION}\nEOF_NOTES\n'''
write('scripts/build-release.sh', build[:start] + notes_block + build[end:])

# Update current-version regression guards and add the new UI/logic contract.
check = read('scripts/check.sh')
check = check.replace("assert release == '1.6.0'", "assert release == '1.6.1'", 1)
check = check.replace("assert panel == '1.6.0'", "assert panel == '1.6.1'", 1)
check = check.replace("assert '\"version\": \"1.6.0\"' in main", "assert '\"version\": \"1.6.1\"' in main", 1)
check = check.replace("assert 'XNAT v1.6.0 Multi-Node' in base", "assert 'XNAT v1.6.1 Multi-Node' in base", 1)
check = check.replace("'Mobile API v1' in docs and 'v1.6.0' in docs", "'Mobile API v1' in docs and 'v1.6.1' in docs", 1)
check = check.replace("assert '最新正式版本：**v1.6.0**' in readme", "assert '最新正式版本：**v1.6.1**' in readme", 1)
contract = '''\n# v1.6.1 visible per-plan Host capacity contract.\ngrep -q 'def host_plan_capacity_estimates' panel/app/nodes.py\ngrep -q 'host_plan_estimates' panel/app/main.py\ngrep -q '按套餐预计可开' panel/app/templates/admin.html\ngrep -q 'node-plan-capacity-visible' panel/app/templates/admin.html\ngrep -q 'node-plan-capacity-visible' panel/app/static/style.css\ngrep -q '1.6.0) UPGRADE_PATH="verified-v1.6.0"' scripts/upgrade-panel.sh\npython3 - <<'PYV161'\nfrom pathlib import Path\nadmin=Path('panel/app/templates/admin.html').read_text()\nsegment=admin.split('按套餐预计可开',1)[1].split('{% set cap',1)[0]\nassert '<details' not in segment, 'capacity estimate must remain visible, not folded'\nassert '≈ {{ item.count }} 台' in admin, 'per-plan count missing'\nassert '瓶颈：{{ item.limiter }}' in admin, 'visible limiter missing'\nprint('v1.6.1 visible Host plan capacity contract: ok')\nPYV161\n\n'''
needle = '# v1.3.2 Mobile API v1 contract for XNAT Android v1.0.0.\n'
if needle not in check:
    raise SystemExit('check.sh insertion anchor missing')
check = check.replace(needle, contract + needle, 1)
write('scripts/check.sh', check)

print('v1.6.1 patch applied')
