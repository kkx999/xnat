from pathlib import Path
p=Path(__file__).resolve().parents[1]/'panel/app/static/client.js'
s=p.read_text()
if 'v1.0.4 server live metrics' in s: raise SystemExit('already present')
code=r'''

  // v1.0.4 server live metrics: 5s while visible, no historical storage.
  const liveMetrics=document.querySelector("[data-server-live-metrics]");
  if(liveMetrics){
    const url=liveMetrics.dataset.metricsUrl||"",status=liveMetrics.querySelector("[data-live-status]"),statusText=status?.querySelector("span");
    const cpu=liveMetrics.querySelector("[data-live-cpu]"),mem=liveMetrics.querySelector("[data-live-memory]"),disk=liveMetrics.querySelector("[data-live-disk]");
    const rx=liveMetrics.querySelector("[data-live-rx]"),tx=liveMetrics.querySelector("[data-live-tx]"),net=liveMetrics.querySelector("[data-live-network-summary]");
    const cpuNote=liveMetrics.querySelector("[data-live-cpu-note]"),memNote=liveMetrics.querySelector("[data-live-memory-note]"),diskNote=liveMetrics.querySelector("[data-live-disk-note]");
    const cpuBar=liveMetrics.querySelector("[data-live-cpu-bar]"),memBar=liveMetrics.querySelector("[data-live-memory-bar]"),diskBar=liveMetrics.querySelector("[data-live-disk-bar]");
    let timer=null,busy=false;
    const bytes=(raw)=>{let n=Math.max(0,Number(raw||0)),u=["B","KB","MB","GB","TB"],i=0;if(!Number.isFinite(n))return"--";while(n>=1024&&i<u.length-1){n/=1024;i++}const d=i===0?0:n>=100?0:n>=10?1:2;return`${n.toFixed(d)} ${u[i]}`};
    const rate=(raw)=>raw==null?"采样中":`${bytes(raw)}/s`;
    const pct=(raw)=>{const n=Number(raw);return Number.isFinite(n)?Math.min(100,Math.max(0,n)):null};
    const bar=(el,raw)=>{const box=el?.parentElement,n=pct(raw);if(!el||!box)return;el.style.width=`${n==null?0:n}%`;box.classList.toggle("warning",n!=null&&n>=70&&n<90);box.classList.toggle("critical",n!=null&&n>=90)};
    const state=(kind,text)=>{status?.classList.toggle("is-live",kind==="live");status?.classList.toggle("is-error",kind==="error");if(statusText)statusText.textContent=text};
    const unavailable=(raw)=>{const stopped=["stopped","frozen"].includes(String(raw||"").toLowerCase());state(stopped?"idle":"error",stopped?"实例已关机":"暂不可用");if(cpu)cpu.textContent="--";if(mem)mem.textContent="--";if(disk)disk.textContent="--";if(rx)rx.textContent="--";if(tx)tx.textContent="--";if(net)net.textContent=stopped?"已停止":"无数据";[cpuBar,memBar,diskBar].forEach(x=>bar(x,null))};
    const render=(d)=>{if(!d?.available){unavailable(d?.status);return}state("live","实时 · 刚刚更新");const c=pct(d.cpu_percent),m=pct(d.memory_percent),k=pct(d.disk_percent);if(cpu)cpu.textContent=c==null?"采样中":`${c.toFixed(1)}%`;if(mem)mem.textContent=m==null?"--":`${m.toFixed(1)}%`;if(disk)disk.textContent=k==null?"--":`${k.toFixed(1)}%`;if(cpuNote)cpuNote.textContent=c==null?"等待下一次采样":`当前 CPU 使用 ${c.toFixed(1)}%`;if(memNote)memNote.textContent=`${bytes(d.memory_used_bytes)} / ${bytes(d.memory_total_bytes)}`;if(diskNote)diskNote.textContent=`${bytes(d.disk_used_bytes)} / ${bytes(d.disk_total_bytes)}`;if(rx)rx.textContent=rate(d.network_rx_bps);if(tx)tx.textContent=rate(d.network_tx_bps);if(net)net.textContent=d.network_rx_bps==null||d.network_tx_bps==null?"采样中":"实时速率";bar(cpuBar,c);bar(memBar,m);bar(diskBar,k)};
    const schedule=()=>{clearTimeout(timer);if(!document.hidden)timer=setTimeout(load,5000)};
    const load=async()=>{if(!url||document.hidden||busy){schedule();return}busy=true;try{const r=await fetch(url,{credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"}});if(!r.ok)throw new Error();render(await r.json())}catch(_){unavailable("unavailable")}finally{busy=false;schedule()}};
    document.addEventListener("visibilitychange",()=>{clearTimeout(timer);if(!document.hidden)load()});window.addEventListener("pagehide",()=>clearTimeout(timer),{once:true});load();
  }
'''
mark='\n})();'
i=s.rfind(mark)
if i<0: raise SystemExit('closure anchor')
p.write_text(s[:i]+code+s[i:])
