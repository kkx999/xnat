(() => {
  "use strict";

  const root = document.body;
  if (!root) return;

  // Mobile client navigation: the desktop sidebar stays fully expanded, while
  // phones use a real off-canvas drawer with independently collapsible groups.
  // Expanded categories are remembered for the current tab and the active page
  // is always revealed when the drawer is opened on a newly loaded page.
  const clientSidebar = document.getElementById("clientSidebar");
  const clientSidebarToggle = document.querySelector("[data-client-sidebar-toggle]");
  const clientSidebarBackdrop = document.querySelector(".client-sidebar-backdrop");
  const clientSidebarCloseControls = document.querySelectorAll("[data-client-sidebar-close]");
  const clientNavGroups = Array.from(document.querySelectorAll("[data-client-nav-group]"));
  const clientMobileQuery = window.matchMedia("(max-width: 760px)");
  const navStateStorageKey = "xnat-client-mobile-nav-groups";
  let clientSidebarHideTimer = null;

  // Keep the drawer inside the actually visible mobile viewport. 100dvh can
  // still extend behind Android's gesture/navigation bar in edge-to-edge mode.
  // visualViewport is the most accurate value when available; CSS keeps a
  // conservative bottom guard as a fallback.
  const syncClientViewportHeight = () => {
    if (!clientMobileQuery.matches) {
      document.documentElement.style.removeProperty("--xnat-client-viewport-height");
      return;
    }
    const viewportHeight = window.visualViewport?.height || window.innerHeight;
    if (Number.isFinite(viewportHeight) && viewportHeight > 0) {
      document.documentElement.style.setProperty("--xnat-client-viewport-height", `${Math.round(viewportHeight)}px`);
    }
  };

  const readClientNavState = () => {
    try {
      const value = JSON.parse(sessionStorage.getItem(navStateStorageKey) || "[]");
      return Array.isArray(value) ? new Set(value.filter((item) => typeof item === "string")) : new Set();
    } catch (_) {
      return new Set();
    }
  };
  const saveClientNavState = () => {
    if (!clientMobileQuery.matches) return;
    const openNames = clientNavGroups
      .filter((group) => group.classList.contains("is-open"))
      .map((group) => group.dataset.clientNavGroup)
      .filter(Boolean);
    try { sessionStorage.setItem(navStateStorageKey, JSON.stringify(openNames)); } catch (_) {}
  };
  const setClientNavGroupOpen = (group, open) => {
    if (!group) return;
    group.classList.toggle("is-open", open);
    const button = group.querySelector("[data-client-nav-group-toggle]");
    if (button) button.setAttribute("aria-expanded", open ? "true" : "false");
  };
  const syncClientNavGroups = () => {
    if (!clientNavGroups.length) return;
    if (!clientMobileQuery.matches) {
      clientNavGroups.forEach((group) => {
        setClientNavGroupOpen(group, true);
        const button = group.querySelector("[data-client-nav-group-toggle]");
        if (button) button.setAttribute("tabindex", "-1");
      });
      return;
    }
    const saved = readClientNavState();
    const hasSavedState = saved.size > 0;
    clientNavGroups.forEach((group) => {
      const button = group.querySelector("[data-client-nav-group-toggle]");
      if (button) button.removeAttribute("tabindex");
      const containsActive = Boolean(group.querySelector("a.active"));
      const shouldOpen = containsActive || (hasSavedState && saved.has(group.dataset.clientNavGroup));
      setClientNavGroupOpen(group, shouldOpen);
    });
  };
  const setClientSidebarAccessibility = (open) => {
    if (!clientSidebar || !clientMobileQuery.matches) return;
    clientSidebar.setAttribute("aria-hidden", open ? "false" : "true");
    if ("inert" in clientSidebar) clientSidebar.inert = !open;
  };
  const openClientSidebar = () => {
    if (!clientSidebar || !clientMobileQuery.matches) return;
    window.clearTimeout(clientSidebarHideTimer);
    clientSidebar.classList.add("is-open");
    root.classList.add("client-sidebar-open");
    clientSidebarToggle?.setAttribute("aria-expanded", "true");
    if (clientSidebarBackdrop) {
      clientSidebarBackdrop.hidden = false;
      requestAnimationFrame(() => clientSidebarBackdrop.classList.add("is-open"));
    }
    setClientSidebarAccessibility(true);
    window.setTimeout(() => {
      clientSidebar.querySelector("a.active")?.scrollIntoView({block:"nearest"});
      clientSidebar.querySelector("[data-client-nav-group].is-current [data-client-nav-group-toggle]")?.focus({preventScroll:true});
    }, 70);
  };
  const closeClientSidebar = ({restoreFocus = true} = {}) => {
    if (!clientSidebar) return;
    clientSidebar.classList.remove("is-open");
    root.classList.remove("client-sidebar-open");
    clientSidebarToggle?.setAttribute("aria-expanded", "false");
    clientSidebarBackdrop?.classList.remove("is-open");
    setClientSidebarAccessibility(false);
    window.clearTimeout(clientSidebarHideTimer);
    clientSidebarHideTimer = window.setTimeout(() => {
      if (clientSidebarBackdrop && !clientSidebarBackdrop.classList.contains("is-open")) clientSidebarBackdrop.hidden = true;
    }, 250);
    if (restoreFocus && clientMobileQuery.matches) clientSidebarToggle?.focus({preventScroll:true});
  };

  clientNavGroups.forEach((group) => {
    const button = group.querySelector("[data-client-nav-group-toggle]");
    button?.addEventListener("click", () => {
      if (!clientMobileQuery.matches) return;
      setClientNavGroupOpen(group, !group.classList.contains("is-open"));
      saveClientNavState();
    });
  });
  clientSidebarToggle?.addEventListener("click", () => {
    if (clientSidebar?.classList.contains("is-open")) closeClientSidebar();
    else openClientSidebar();
  });
  clientSidebarCloseControls.forEach((control) => control.addEventListener("click", () => closeClientSidebar()));
  clientSidebar?.querySelectorAll(".client-nav-group-items a").forEach((link) => {
    link.addEventListener("click", () => {
      if (clientMobileQuery.matches) closeClientSidebar({restoreFocus:false});
    });
  });

  // A short horizontal swipe inside the drawer closes it; no edge-swipe opener
  // is installed so the browser's native back gesture remains untouched.
  if (clientSidebar) {
    let touchStartX = 0;
    let touchStartY = 0;
    clientSidebar.addEventListener("touchstart", (event) => {
      const touch = event.touches[0];
      if (!touch || !clientMobileQuery.matches) return;
      touchStartX = touch.clientX;
      touchStartY = touch.clientY;
    }, {passive:true});
    clientSidebar.addEventListener("touchend", (event) => {
      const touch = event.changedTouches[0];
      if (!touch || !clientMobileQuery.matches || !clientSidebar.classList.contains("is-open")) return;
      const dx = touch.clientX - touchStartX;
      const dy = touch.clientY - touchStartY;
      if (dx < -58 && Math.abs(dx) > Math.abs(dy) * 1.25) closeClientSidebar();
    }, {passive:true});
  }

  const handleClientNavViewportChange = () => {
    syncClientViewportHeight();
    syncClientNavGroups();
    if (!clientMobileQuery.matches) {
      window.clearTimeout(clientSidebarHideTimer);
      clientSidebar?.classList.remove("is-open");
      root.classList.remove("client-sidebar-open");
      clientSidebar?.removeAttribute("aria-hidden");
      if (clientSidebar && "inert" in clientSidebar) clientSidebar.inert = false;
      clientSidebarToggle?.setAttribute("aria-expanded", "false");
      if (clientSidebarBackdrop) {
        clientSidebarBackdrop.classList.remove("is-open");
        clientSidebarBackdrop.hidden = true;
      }
    } else {
      closeClientSidebar({restoreFocus:false});
    }
  };
  syncClientViewportHeight();
  syncClientNavGroups();
  if (clientMobileQuery.matches) setClientSidebarAccessibility(false);
  window.addEventListener("resize", syncClientViewportHeight, {passive:true});
  window.visualViewport?.addEventListener("resize", syncClientViewportHeight, {passive:true});
  window.visualViewport?.addEventListener("scroll", syncClientViewportHeight, {passive:true});
  if (typeof clientMobileQuery.addEventListener === "function") clientMobileQuery.addEventListener("change", handleClientNavViewportChange);
  else if (typeof clientMobileQuery.addListener === "function") clientMobileQuery.addListener(handleClientNavViewportChange);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && clientSidebar?.classList.contains("is-open")) closeClientSidebar();
  });

  // Customer theme: keep the existing dark appearance as default and provide a
  // brighter, low-glare light palette. The preference is local to this browser
  // and is applied before paint by base.html to avoid a light/dark flash.
  const themeStorageKey = "xnat-client-theme";
  const themeRoot = document.documentElement;
  const themeToggle = document.querySelector("[data-client-theme-toggle]");
  const themeLabel = document.querySelector("[data-client-theme-label]");
  const normalizeTheme = (value) => value === "light" ? "light" : "dark";
  let clientThemeTransitionTimer = null;
  const beginClientThemeTransition = () => {
    if (!root.classList.contains("client-body") || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    window.clearTimeout(clientThemeTransitionTimer);
    themeRoot.classList.add("client-theme-switching");
    clientThemeTransitionTimer = window.setTimeout(() => themeRoot.classList.remove("client-theme-switching"), 340);
  };
  const updateThemeButton = () => {
    if (!themeToggle) return;
    const current = normalizeTheme(themeRoot.dataset.clientTheme);
    const nextLabel = current === "light" ? "深色" : "明亮";
    if (themeLabel) themeLabel.textContent = nextLabel;
    themeToggle.setAttribute("aria-label", `切换为${nextLabel}主题`);
    themeToggle.setAttribute("title", `切换为${nextLabel}主题`);
    themeToggle.setAttribute("aria-pressed", current === "light" ? "true" : "false");
  };
  updateThemeButton();
  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const current = normalizeTheme(themeRoot.dataset.clientTheme);
      const next = current === "light" ? "dark" : "light";
      beginClientThemeTransition();
      themeRoot.dataset.clientTheme = next;
      try { localStorage.setItem(themeStorageKey, next); } catch (_) {}
      updateThemeButton();
    });
  }
  window.addEventListener("storage", (event) => {
    if (event.key !== themeStorageKey) return;
    beginClientThemeTransition();
    themeRoot.dataset.clientTheme = normalizeTheme(event.newValue);
    updateThemeButton();
  });

  // Customer navigation feedback: a very short route hand-off gives pointer/touch
  // interactions time to render their pressed state and a thin top progress line.
  // Only normal same-origin GET links are delayed; modifier clicks, downloads,
  // targets, anchors and external URLs keep native browser behavior.
  if (root.classList.contains("client-body")) {
    const clientContent = document.querySelector(".client-content");
    requestAnimationFrame(() => clientContent?.classList.add("xnat-page-enter"));

    document.addEventListener("click", (event) => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const link = event.target.closest?.("a[href]");
      if (!link || !root.contains(link) || link.hasAttribute("download")) return;
      const target = (link.getAttribute("target") || "").toLowerCase();
      if (target && target !== "_self") return;
      const rawHref = (link.getAttribute("href") || "").trim();
      if (!rawHref || rawHref.startsWith("#") || /^(mailto:|tel:|javascript:)/i.test(rawHref)) return;
      let url;
      try { url = new URL(link.href, window.location.href); } catch (_) { return; }
      if (url.origin !== window.location.origin || url.href === window.location.href) return;

      event.preventDefault();
      link.classList.add("is-route-pending");
      root.classList.add("client-route-leaving");
      window.setTimeout(() => window.location.assign(url.href), 70);
    });

    window.addEventListener("pageshow", () => {
      root.classList.remove("client-route-leaving");
      document.querySelectorAll(".is-route-pending").forEach((link) => link.classList.remove("is-route-pending"));
    });
  }

  // Admin theme uses its own preference so an operator can keep the customer
  // area and control panel in different modes. The light palette mirrors the
  // low-glare customer theme while preserving stronger contrast for dense
  // tables, forms and operational status surfaces.
  const adminThemeStorageKey = "xnat-admin-theme";
  const adminThemeToggle = document.querySelector("[data-admin-theme-toggle]");
  const adminThemeLabel = document.querySelector("[data-admin-theme-label]");
  const updateAdminThemeButton = () => {
    if (!adminThemeToggle) return;
    const current = normalizeTheme(themeRoot.dataset.adminTheme);
    const nextLabel = current === "light" ? "深色" : "明亮";
    if (adminThemeLabel) adminThemeLabel.textContent = nextLabel;
    adminThemeToggle.setAttribute("aria-label", `切换为${nextLabel}主题`);
    adminThemeToggle.setAttribute("title", `切换为${nextLabel}主题`);
    adminThemeToggle.setAttribute("aria-pressed", current === "light" ? "true" : "false");
  };
  updateAdminThemeButton();
  if (adminThemeToggle) {
    adminThemeToggle.addEventListener("click", () => {
      const current = normalizeTheme(themeRoot.dataset.adminTheme);
      const next = current === "light" ? "dark" : "light";
      themeRoot.dataset.adminTheme = next;
      try { localStorage.setItem(adminThemeStorageKey, next); } catch (_) {}
      updateAdminThemeButton();
    });
  }
  window.addEventListener("storage", (event) => {
    if (event.key !== adminThemeStorageKey) return;
    themeRoot.dataset.adminTheme = normalizeTheme(event.newValue);
    updateAdminThemeButton();
  });

  const announcementRead = async (id, csrf) => {
    if (!id || !csrf) return null;
    try { const form = new FormData(); form.append("csrf_token", csrf); const response = await fetch(`/announcements/${id}/read`, {method:"POST", body:form, credentials:"same-origin"}); if (!response.ok) return null; const data = await response.json(); const badge=document.querySelector("[data-announcement-badge]"); if (badge) { const unread=Number(data.unread||0); badge.textContent=unread?String(unread):""; badge.classList.toggle("is-empty",!unread); } const item=document.querySelector(`.announcement-history-item[data-announcement-id="${id}"]`); item?.classList.remove("is-unread"); item?.querySelector(".announcement-unread-dot")?.remove(); return data; } catch (_) { return null; }
  };
  const center=document.getElementById("announcementCenter"), centerToggle=document.querySelector("[data-announcement-center-toggle]"), centerBackdrop=document.querySelector(".announcement-center-backdrop");
  if (center && centerToggle) { const openCenter=()=>{center.classList.add("is-open");center.setAttribute("aria-hidden","false");centerToggle.setAttribute("aria-expanded","true");if(centerBackdrop){centerBackdrop.hidden=false;requestAnimationFrame(()=>centerBackdrop.classList.add("is-open"));}document.body.classList.add("announcement-center-open");}; const closeCenter=()=>{center.classList.remove("is-open");center.setAttribute("aria-hidden","true");centerToggle.setAttribute("aria-expanded","false");centerBackdrop?.classList.remove("is-open");window.setTimeout(()=>{if(centerBackdrop&&!centerBackdrop.classList.contains("is-open"))centerBackdrop.hidden=true;},220);document.body.classList.remove("announcement-center-open");}; centerToggle.addEventListener("click",()=>center.classList.contains("is-open")?closeCenter():openCenter()); document.querySelectorAll("[data-announcement-center-close]").forEach(el=>el.addEventListener("click",closeCenter)); center.querySelectorAll("[data-announcement-open]").forEach(button=>button.addEventListener("click",async()=>{const item=button.closest(".announcement-history-item");if(!item)return;const willOpen=!item.classList.contains("is-expanded");center.querySelectorAll(".announcement-history-item.is-expanded").forEach(other=>{if(other!==item)other.classList.remove("is-expanded");});item.classList.toggle("is-expanded",willOpen);if(willOpen&&item.classList.contains("is-unread"))await announcementRead(item.dataset.announcementId,center.dataset.csrf);})); document.addEventListener("keydown",e=>{if(e.key==="Escape"&&center.classList.contains("is-open"))closeCenter();}); }
  const announcement=document.getElementById("loginAnnouncement");
  if (announcement) { const card=announcement.querySelector(".announcement-card"),confirm=announcement.querySelector(".announcement-confirm");let dismissing=false;const dismiss=async()=>{if(dismissing||announcement.classList.contains("leaving"))return;dismissing=true;await announcementRead(announcement.dataset.announcementId,announcement.dataset.csrf);announcement.classList.add("leaving");window.setTimeout(()=>announcement.remove(),240);};announcement.querySelectorAll("[data-announcement-dismiss]").forEach(button=>button.addEventListener("click",dismiss));announcement.addEventListener("click",event=>{if(event.target===announcement)dismiss();});document.addEventListener("keydown",event=>{if(event.key==="Escape"&&document.body.contains(announcement))dismiss();});window.requestAnimationFrame(()=>{card?.classList.add("is-ready");confirm?.focus({preventScroll:true});});}

  // Flash messages use the same compact top-right toast in both client and admin areas.
  // Keep them non-blocking and dismiss automatically after 3 seconds.
  document.querySelectorAll(".client-body .flash, .admin-body .flash").forEach((toast) => {
    toast.classList.add("xnat-toast");
    let dismissTimer = null;
    const dismiss = () => {
      if (!document.body.contains(toast) || toast.classList.contains("leaving")) return;
      toast.classList.add("leaving");
      window.setTimeout(() => toast.remove(), 220);
    };
    const arm = () => {
      window.clearTimeout(dismissTimer);
      dismissTimer = window.setTimeout(dismiss, 3000);
    };
    toast.addEventListener("mouseenter", () => window.clearTimeout(dismissTimer));
    toast.addEventListener("mouseleave", arm);
    toast.addEventListener("click", dismiss);
    arm();
  });

  const showTransientToast = (message) => {
    const toast = document.createElement("div");
    toast.className = "xnat-toast xnat-toast-created";
    toast.textContent = message;
    document.body.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add("is-ready"));
    window.setTimeout(() => {
      toast.classList.add("leaving");
      window.setTimeout(() => toast.remove(), 220);
    }, 2200);
  };

  // Copy feedback is inline and also confirms success with the shared toast language.
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-value]");
    if (!button) return;
    event.preventDefault();
    const value = button.getAttribute("data-copy-value") || "";
    if (!value) return;
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(value);
      button.classList.add("copied");
      showTransientToast("密码 / 信息已复制");
    } catch (_) {
      const input = document.createElement("textarea");
      input.value = value;
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
      button.classList.add("copied");
      showTransientToast("密码 / 信息已复制");
    }
    window.setTimeout(() => {
      button.textContent = original;
      button.classList.remove("copied");
    }, 1300);
  });

  // XNAT confirmation modal: replaces browser-native confirm() with a themed,
  // non-blocking interaction that works in both client and admin areas.
  const xnatConfirm = (() => {
    let active = null;
    let previousFocus = null;

    const ensureModal = () => {
      let backdrop = document.querySelector("[data-xnat-confirm-backdrop]");
      if (backdrop) return backdrop;
      backdrop = document.createElement("div");
      backdrop.className = "xnat-confirm-backdrop";
      backdrop.hidden = true;
      backdrop.setAttribute("data-xnat-confirm-backdrop", "");
      backdrop.innerHTML = `
        <section class="xnat-confirm-card" role="dialog" aria-modal="true" aria-labelledby="xnatConfirmTitle" aria-describedby="xnatConfirmMessage">
          <button class="xnat-confirm-close" type="button" data-xnat-confirm-cancel aria-label="关闭">×</button>
          <div class="xnat-confirm-icon" aria-hidden="true"><span>↻</span></div>
          <div class="xnat-confirm-kicker" data-xnat-confirm-kicker>CONFIRM ACTION</div>
          <h2 id="xnatConfirmTitle" data-xnat-confirm-title>确认操作</h2>
          <p class="xnat-confirm-message" id="xnatConfirmMessage" data-xnat-confirm-message></p>
          <div class="xnat-confirm-money" data-xnat-confirm-money hidden>
            <div><span>本次扣除</span><strong data-xnat-confirm-fee></strong></div>
            <div><span>当前余额</span><strong data-xnat-confirm-balance></strong></div>
            <div><span>扣除后</span><strong data-xnat-confirm-after></strong></div>
          </div>
          <div class="xnat-confirm-effects" data-xnat-confirm-effects hidden></div>
          <label class="xnat-confirm-input-wrap" data-xnat-confirm-input-wrap hidden>
            <span data-xnat-confirm-input-label>输入确认内容</span>
            <input class="xnat-confirm-input" data-xnat-confirm-input-field autocomplete="off" spellcheck="false">
            <small data-xnat-confirm-input-error></small>
          </label>
          <div class="xnat-confirm-note" data-xnat-confirm-note hidden></div>
          <div class="xnat-confirm-actions">
            <button class="xnat-confirm-cancel" type="button" data-xnat-confirm-cancel>返回</button>
            <button class="xnat-confirm-submit" type="button" data-xnat-confirm-submit>确认</button>
          </div>
        </section>`;
      document.body.appendChild(backdrop);
      return backdrop;
    };

    const close = () => {
      const backdrop = document.querySelector("[data-xnat-confirm-backdrop]");
      if (!backdrop || backdrop.hidden) return;
      backdrop.classList.add("leaving");
      document.body.classList.remove("xnat-confirm-open");
      window.setTimeout(() => {
        backdrop.hidden = true;
        backdrop.classList.remove("is-open", "leaving", "tone-danger", "tone-billing");
        active = null;
        if (previousFocus && document.body.contains(previousFocus)) previousFocus.focus({preventScroll:true});
        previousFocus = null;
      }, 190);
    };

    const open = (button) => {
      const form = button.closest("form");
      if (!form) return;
      const backdrop = ensureModal();
      const card = backdrop.querySelector(".xnat-confirm-card");
      const title = backdrop.querySelector("[data-xnat-confirm-title]");
      const kicker = backdrop.querySelector("[data-xnat-confirm-kicker]");
      const message = backdrop.querySelector("[data-xnat-confirm-message]");
      const fee = backdrop.querySelector("[data-xnat-confirm-fee]");
      const balance = backdrop.querySelector("[data-xnat-confirm-balance]");
      const after = backdrop.querySelector("[data-xnat-confirm-after]");
      const moneyBox = backdrop.querySelector("[data-xnat-confirm-money]");
      const effects = backdrop.querySelector("[data-xnat-confirm-effects]");
      const note = backdrop.querySelector("[data-xnat-confirm-note]");
      const inputWrap = backdrop.querySelector("[data-xnat-confirm-input-wrap]");
      const inputLabel = backdrop.querySelector("[data-xnat-confirm-input-label]");
      const inputField = backdrop.querySelector("[data-xnat-confirm-input-field]");
      const inputError = backdrop.querySelector("[data-xnat-confirm-input-error]");
      const submit = backdrop.querySelector("[data-xnat-confirm-submit]");
      const tone = button.dataset.confirmTone || "default";

      kicker.textContent = button.dataset.confirmKicker || "CONFIRM ACTION";
      title.textContent = button.dataset.confirmTitle || "确认此操作？";
      message.textContent = button.dataset.confirmMessage || "请确认是否继续。";
      submit.textContent = button.dataset.confirmLabel || "确认";

      const hasMoney = Boolean(button.dataset.confirmFee || button.dataset.confirmBalance || button.dataset.confirmAfter);
      moneyBox.hidden = !hasMoney;
      fee.textContent = button.dataset.confirmFee || "-";
      balance.textContent = button.dataset.confirmBalance || "-";
      after.textContent = button.dataset.confirmAfter || "-";

      const items = (button.dataset.confirmItems || "").split("|").map((item) => item.trim()).filter(Boolean);
      effects.hidden = items.length === 0;
      effects.innerHTML = items.map((item) => `<div><span aria-hidden="true">✓</span><p>${item.replace(/[&<>\"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[ch]))}</p></div>`).join("");

      const expectedInput = button.dataset.confirmInput || "";
      inputWrap.hidden = !expectedInput;
      inputLabel.textContent = button.dataset.confirmInputLabel || `输入 ${expectedInput} 确认`;
      inputField.value = "";
      inputField.placeholder = button.dataset.confirmInputPlaceholder || expectedInput;
      inputError.textContent = "";
      inputWrap.classList.remove("has-error");

      const noteText = button.dataset.confirmNote || "";
      note.hidden = !noteText;
      note.textContent = noteText;

      backdrop.classList.toggle("tone-danger", tone === "danger");
      backdrop.classList.toggle("tone-billing", tone === "billing");
      const icon = backdrop.querySelector(".xnat-confirm-icon span");
      icon.textContent = tone === "danger" ? "!" : tone === "billing" ? "↻" : "i";

      active = {form, button, expectedInput};
      previousFocus = document.activeElement;
      backdrop.hidden = false;
      document.body.classList.add("xnat-confirm-open");
      requestAnimationFrame(() => {
        backdrop.classList.add("is-open");
        card.classList.add("is-ready");
        (expectedInput ? inputField : submit).focus({preventScroll:true});
      });
    };

    document.addEventListener("click", (event) => {
      const confirmButton = event.target.closest("[data-xnat-confirm]");
      if (confirmButton && !confirmButton.disabled) {
        event.preventDefault();
        open(confirmButton);
        return;
      }
      if (event.target.closest("[data-xnat-confirm-cancel]")) {
        event.preventDefault();
        close();
        return;
      }
      const submit = event.target.closest("[data-xnat-confirm-submit]");
      if (submit && active) {
        event.preventDefault();
        const {form, button, expectedInput} = active;
        if (expectedInput) {
          const backdrop = document.querySelector("[data-xnat-confirm-backdrop]");
          const inputWrap = backdrop?.querySelector("[data-xnat-confirm-input-wrap]");
          const inputField = backdrop?.querySelector("[data-xnat-confirm-input-field]");
          const inputError = backdrop?.querySelector("[data-xnat-confirm-input-error]");
          const actual = (inputField?.value || "").trim();
          const matches = expectedInput.toLowerCase() === "yes"
            ? actual.toLowerCase() === "yes"
            : actual === expectedInput;
          if (!matches) {
            inputWrap?.classList.add("has-error");
            if (inputError) inputError.textContent = `请输入 ${expectedInput} 后再确认`;
            inputField?.focus({preventScroll:true});
            inputField?.select();
            return;
          }
          const hiddenConfirm = form.querySelector('[name="confirm_text"][data-xnat-confirm-value]');
          if (hiddenConfirm) hiddenConfirm.value = actual;
        }
        close();
        form.dataset.xnatConfirmed = "true";
        window.setTimeout(() => form.requestSubmit(button), 40);
      }
    });

    document.addEventListener("click", (event) => {
      const backdrop = event.target.closest("[data-xnat-confirm-backdrop]");
      if (backdrop && event.target === backdrop) close();
    });
    document.addEventListener("keydown", (event) => {
      const backdrop = document.querySelector("[data-xnat-confirm-backdrop]");
      if (!backdrop || backdrop.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      } else if (event.key === "Enter" && event.target.matches?.("[data-xnat-confirm-input-field]")) {
        event.preventDefault();
        backdrop.querySelector("[data-xnat-confirm-submit]")?.click();
      }
    });
    document.addEventListener("submit", (event) => {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      const submitter = event.submitter;
      if (!submitter?.matches?.("[data-xnat-confirm]")) return;
      if (form.dataset.xnatConfirmed === "true") {
        delete form.dataset.xnatConfirmed;
        return;
      }
      event.preventDefault();
      open(submitter);
    });

    return {open, close};
  })();

  // Normal POSTs keep server-side safety/CSRF semantics but immediately show
  // progress so users know an operation was accepted and cannot double-click.
  document.querySelectorAll(".client-body form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented || form.dataset.noLoading === "true") return;
      const submitter = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
      if (!submitter || submitter.disabled) return;
      if (form.dataset.xnatSubmitting === "true") {
        event.preventDefault();
        return;
      }
      form.dataset.xnatSubmitting = "true";
      submitter.dataset.originalText = submitter.textContent;
      submitter.classList.add("is-loading");
      // Do not disable the native submitter here. A disabled submit button is
      // removed from successful form controls, which would drop name/value
      // pairs such as action=debit before the browser serializes the POST.
      submitter.setAttribute("aria-disabled", "true");
      const label = submitter.getAttribute("data-loading-label") || "处理中…";
      submitter.textContent = label;
    });
  });

  // Animate existing progress values from zero once per page view.
  document.querySelectorAll(".client-body .traffic-progress > span").forEach((bar) => {
    const target = bar.style.width || "0%";
    bar.style.width = "0%";
    requestAnimationFrame(() => requestAnimationFrame(() => {
      bar.style.width = target;
    }));
  });

  // Browser back/forward cache can return a page with disabled submitters.
  window.addEventListener("pageshow", () => {
    document.querySelectorAll(".client-body form").forEach((form) => delete form.dataset.xnatSubmitting);
    document.querySelectorAll(".client-body button.is-loading").forEach((button) => {
      button.removeAttribute("aria-disabled");
      button.classList.remove("is-loading");
      if (button.dataset.originalText) button.textContent = button.dataset.originalText;
    });
  });
})();


// v1.2.0: while an asynchronous lifecycle job is active, refresh the
// detail page periodically so the yellow/red transient state returns to the
// confirmed provider state immediately after the job finishes.
document.addEventListener('DOMContentLoaded', () => {
  const page = document.querySelector('.server-detail-page[data-server-ui-status]');
  if (!page) return;
  const state = page.dataset.serverUiStatus;
  if (!['provisioning', 'reinstalling', 'deleting'].includes(state)) return;
  window.setInterval(() => {
    if (document.visibilityState === 'visible') window.location.reload();
  }, 5000);
});


// v1.3.0: keep KVM plan resource minimums visible in the admin UI.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('[data-virtualization-form]').forEach((form) => {
    const select = form.querySelector('[data-virt-select]');
    const memory = form.querySelector('[data-virt-memory]');
    const disk = form.querySelector('[data-virt-disk]');
    if (!select || !memory || !disk) return;

    const sync = () => {
      const kvm = select.value === 'kvm';
      memory.min = kvm ? '512' : '64';
      disk.min = kvm ? '4' : '1';
      memory.setCustomValidity('');
      disk.setCustomValidity('');
    };
    select.addEventListener('change', sync);
    form.addEventListener('submit', (event) => {
      sync();
      if (select.value === 'kvm' && Number(memory.value) < 512) {
        memory.setCustomValidity('KVM 套餐内存最低为 512 MB。');
        memory.reportValidity();
        event.preventDefault();
        return;
      }
      if (select.value === 'kvm' && Number(disk.value) < 4) {
        disk.setCustomValidity('KVM 套餐磁盘最低为 4 GB。');
        disk.reportValidity();
        event.preventDefault();
      }
    });
    memory.addEventListener('input', () => memory.setCustomValidity(''));
    disk.addEventListener('input', () => disk.setCustomValidity(''));
    sync();
  });
});

// v1.4.0: admin console micro-interactions. This is deliberately UI-only:
// routes, CSRF handling and backend form semantics remain unchanged.
document.addEventListener('DOMContentLoaded', () => {
  const adminBody = document.body?.classList.contains('admin-body') ? document.body : null;
  if (!adminBody) return;

  // Give admin theme changes the same soft hand-off as the approved client UI.
  const adminThemeToggle = document.querySelector('[data-admin-theme-toggle]');
  adminThemeToggle?.addEventListener('click', () => {
    document.documentElement.classList.add('xnat-admin-theme-shifting');
    window.setTimeout(() => document.documentElement.classList.remove('xnat-admin-theme-shifting'), 360);
  }, {capture:true});

  // Normal admin POSTs show immediate progress and are protected from double-clicks.
  // Buttons that still need the shared confirmation modal are left untouched until
  // the modal has actually confirmed the request.
  document.querySelectorAll('.admin-body form').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (event.defaultPrevented || form.dataset.noLoading === 'true') return;
      const submitter = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
      if (!submitter || submitter.disabled) return;
      if (submitter.matches('[data-xnat-confirm]') && form.dataset.xnatConfirmed !== 'true') return;
      if (form.dataset.xnatSubmitting === 'true') {
        event.preventDefault();
        return;
      }
      form.dataset.xnatSubmitting = 'true';
      submitter.dataset.originalText = submitter.textContent;
      submitter.classList.add('is-loading');
      // Keep the submitter enabled until native form serialization completes so
      // button name/value fields (for example action=debit) are never lost.
      submitter.setAttribute('aria-disabled', 'true');
      submitter.textContent = submitter.getAttribute('data-loading-label') || '处理中…';
    });
  });

  // Internal GET navigation gets a very short visual acknowledgement before the
  // next server-rendered admin page loads. Native modifier-click/new-tab/download
  // behaviour is never intercepted.
  let navigating = false;
  let routeProgress = null;
  const ensureRouteProgress = () => {
    if (routeProgress) return routeProgress;
    routeProgress = document.createElement('div');
    routeProgress.className = 'xnat-route-progress';
    routeProgress.setAttribute('aria-hidden', 'true');
    document.body.appendChild(routeProgress);
    return routeProgress;
  };
  document.addEventListener('click', (event) => {
    if (navigating || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('.admin-body a[href]');
    if (!link || link.target || link.hasAttribute('download')) return;
    const raw = link.getAttribute('href') || '';
    if (!raw || raw.startsWith('#') || raw.startsWith('javascript:') || raw.startsWith('mailto:') || raw.startsWith('tel:')) return;
    let url;
    try { url = new URL(link.href, window.location.href); } catch (_) { return; }
    if (url.origin !== window.location.origin) return;
    if (/(?:\/download|\/export)(?:\/|$)/.test(url.pathname)) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) return;
    event.preventDefault();
    navigating = true;
    adminBody.classList.add('xnat-route-leaving');
    const bar = ensureRouteProgress();
    requestAnimationFrame(() => bar.classList.add('is-active'));
    window.setTimeout(() => { window.location.href = url.href; }, 70);
  });

  window.addEventListener('pageshow', () => {
    navigating = false;
    adminBody.classList.remove('xnat-route-leaving');
    document.querySelectorAll('.admin-body form').forEach((form) => delete form.dataset.xnatSubmitting);
    document.querySelectorAll('.admin-body button.is-loading').forEach((button) => {
      button.removeAttribute('aria-disabled');
      button.classList.remove('is-loading');
      if (button.dataset.originalText) button.textContent = button.dataset.originalText;
    });
    routeProgress?.classList.remove('is-active');
  });
});


// v1.4.0: keep the client interaction layer isolated from admin
// enhancements. Native <details> handles settings/record folding; this block only
// adds a tiny state marker for styling/accessibility and does not change form logic.
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.admin-body details.settings-fold, .admin-body details.admin-record-fold').forEach((details) => {
    const sync = () => details.classList.toggle('is-expanded', details.open);
    sync();
    details.addEventListener('toggle', sync);
  });
});

