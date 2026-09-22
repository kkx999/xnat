(() => {
  "use strict";

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-auth-password-toggle]");
    if (!button) return;
    const wrap = button.closest(".auth-v106-password");
    const input = wrap?.querySelector('input[type="password"], input[type="text"]');
    if (!input) return;
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    button.textContent = reveal ? "隐藏" : "显示";
    button.setAttribute("aria-pressed", reveal ? "true" : "false");
    button.setAttribute("aria-label", reveal ? "隐藏密码" : "显示密码");
    input.focus({preventScroll:true});
  });

  document.querySelectorAll("[data-auto-renew-form]").forEach((form) => {
    const input = form.querySelector(".server-auto-renew-input");
    const status = form.querySelector("[data-auto-renew-status]");
    if (!input || !status) return;
    input.addEventListener("change", async () => {
      const desired = input.checked;
      form.classList.remove("is-error");
      form.classList.add("is-saving");
      status.textContent = desired ? "正在开启…" : "正在关闭…";
      const data = new FormData(form);
      data.set("enabled", desired ? "true" : "false");
      try {
        const response = await fetch(form.action, {
          method: "POST",
          body: data,
          credentials: "same-origin",
          cache: "no-store",
          headers: {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) throw new Error(payload.detail || payload.message || "保存失败");
        input.checked = Boolean(payload.enabled);
        status.textContent = input.checked ? "已开启 · 到期前 24 小时自动续费" : "已关闭 · 手动续费不受影响";
      } catch (_) {
        input.checked = !desired;
        form.classList.add("is-error");
        status.textContent = "保存失败 · 已恢复原设置";
        window.setTimeout(() => {
          form.classList.remove("is-error");
          status.textContent = input.checked ? "已开启 · 到期前 24 小时自动续费" : "已关闭 · 手动续费不受影响";
        }, 2200);
      } finally {
        form.classList.remove("is-saving");
      }
    });
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-machine-copy]");
    if (!button) return;
    event.preventDefault();
    const value = button.getAttribute("data-machine-copy") || "";
    if (!value) return;
    const original = button.textContent;
    const fallback = () => {
      const area = document.createElement("textarea");
      area.value = value;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    };
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(value);
      else fallback();
    } catch (_) {
      fallback();
    }
    button.classList.add("is-copied");
    button.textContent = "✓ 已复制";
    window.setTimeout(() => {
      button.textContent = original;
      button.classList.remove("is-copied");
    }, 1300);
  });
})();
