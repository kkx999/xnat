(() => {
  "use strict";
  const liveMetrics = document.querySelector("[data-server-live-metrics]");
  if (!liveMetrics) return;

  const url = liveMetrics.dataset.metricsUrl || "";
  const status = liveMetrics.querySelector("[data-live-status]");
  const statusText = status?.querySelector("span");
  const cpu = liveMetrics.querySelector("[data-live-cpu]");
  const mem = liveMetrics.querySelector("[data-live-memory]");
  const disk = liveMetrics.querySelector("[data-live-disk]");
  const rx = liveMetrics.querySelector("[data-live-rx]");
  const tx = liveMetrics.querySelector("[data-live-tx]");
  const net = liveMetrics.querySelector("[data-live-network-summary]");
  const cpuNote = liveMetrics.querySelector("[data-live-cpu-note]");
  const memNote = liveMetrics.querySelector("[data-live-memory-note]");
  const diskNote = liveMetrics.querySelector("[data-live-disk-note]");
  const cpuBar = liveMetrics.querySelector("[data-live-cpu-bar]");
  const memBar = liveMetrics.querySelector("[data-live-memory-bar]");
  const diskBar = liveMetrics.querySelector("[data-live-disk-bar]");

  let timer = null;
  let busy = false;

  const bytes = (raw) => {
    let value = Number(raw);
    if (!Number.isFinite(value) || value < 0) return "--";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    const digits = index === 0 ? 0 : value >= 100 ? 0 : value >= 10 ? 1 : 2;
    return `${value.toFixed(digits)} ${units[index]}`;
  };

  const rate = (raw) => raw == null ? "采样中" : `${bytes(raw)}/s`;
  const pct = (raw) => {
    const value = Number(raw);
    return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : null;
  };

  const renderBar = (bar, raw) => {
    const track = bar?.parentElement;
    const value = pct(raw);
    if (!bar || !track) return;
    bar.style.width = `${value == null ? 0 : value}%`;
    track.classList.toggle("warning", value != null && value >= 70 && value < 90);
    track.classList.toggle("critical", value != null && value >= 90);
  };

  const renderState = (kind, text) => {
    status?.classList.toggle("is-live", kind === "live");
    status?.classList.toggle("is-error", kind === "error");
    if (statusText) statusText.textContent = text;
  };

  const renderUnavailable = (rawStatus) => {
    const stopped = ["stopped", "frozen"].includes(String(rawStatus || "").toLowerCase());
    renderState(stopped ? "idle" : "error", stopped ? "实例已关机" : "暂不可用");
    if (cpu) cpu.textContent = "--";
    if (mem) mem.textContent = "--";
    if (disk) disk.textContent = "--";
    if (rx) rx.textContent = "--";
    if (tx) tx.textContent = "--";
    if (net) net.textContent = stopped ? "已停止" : "无数据";
    if (cpuNote) cpuNote.textContent = stopped ? "实例未运行" : "等待恢复";
    if (memNote) memNote.textContent = "--";
    if (diskNote) diskNote.textContent = "--";
    [cpuBar, memBar, diskBar].forEach((bar) => renderBar(bar, null));
  };

  const render = (data) => {
    if (!data?.available) {
      renderUnavailable(data?.status);
      return;
    }
    renderState("live", "实时 · 刚刚更新");
    const cpuPercent = pct(data.cpu_percent);
    const memoryPercent = pct(data.memory_percent);
    const diskPercent = pct(data.disk_percent);
    if (cpu) cpu.textContent = cpuPercent == null ? "采样中" : `${cpuPercent.toFixed(1)}%`;
    if (mem) mem.textContent = memoryPercent == null ? "--" : `${memoryPercent.toFixed(1)}%`;
    if (disk) disk.textContent = diskPercent == null ? "--" : `${diskPercent.toFixed(1)}%`;
    if (cpuNote) cpuNote.textContent = cpuPercent == null ? "等待下一次采样" : `当前 CPU 使用 ${cpuPercent.toFixed(1)}%`;
    if (memNote) memNote.textContent = `${bytes(data.memory_used_bytes)} / ${bytes(data.memory_total_bytes)}`;
    if (diskNote) diskNote.textContent = `${bytes(data.disk_used_bytes)} / ${bytes(data.disk_total_bytes)}`;
    if (rx) rx.textContent = rate(data.network_rx_bps);
    if (tx) tx.textContent = rate(data.network_tx_bps);
    if (net) net.textContent = data.network_rx_bps == null || data.network_tx_bps == null ? "采样中" : "实时速率";
    renderBar(cpuBar, cpuPercent);
    renderBar(memBar, memoryPercent);
    renderBar(diskBar, diskPercent);
  };

  const schedule = () => {
    window.clearTimeout(timer);
    if (!document.hidden) timer = window.setTimeout(load, 5000);
  };

  const load = async () => {
    if (!url || document.hidden || busy) {
      schedule();
      return;
    }
    busy = true;
    try {
      const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        headers: {Accept: "application/json"},
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch (_) {
      renderUnavailable("unavailable");
    } finally {
      busy = false;
      schedule();
    }
  };

  document.addEventListener("visibilitychange", () => {
    window.clearTimeout(timer);
    if (!document.hidden) load();
  });
  window.addEventListener("pagehide", () => window.clearTimeout(timer), {once: true});
  load();
})();
