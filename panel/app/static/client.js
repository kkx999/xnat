(() => {
  "use strict";

  const root = document.body;
  if (!root) return;

  const announcementRead = async (id, csrf) => {
    if (!id || !csrf) return null;
    try { const form = new FormData(); form.append("csrf_token", csrf); const response = await fetch(`/announcements/${id}/read`, {method:"POST", body:form, credentials:"same-origin"}); if (!response.ok) return null; const data = await response.json(); const badge=document.querySelector("[data-announcement-badge]"); if (badge) { const unread=Number(data.unread||0); badge.textContent=unread?String(unread):""; badge.classList.toggle("is-empty",!unread); } const item=document.querySelector(`.announcement-history-item[data-announcement-id="${id}"]`); item?.classList.remove("is-unread"); item?.querySelector(".announcement-unread-dot")?.remove(); return data; } catch (_) { return null; }
  };
  const center=document.getElementById("announcementCenter"), centerToggle=document.querySelector("[data-announcement-center-toggle]"), centerBackdrop=document.querySelector(".announcement-center-backdrop");
  if (center && centerToggle) { const openCenter=()=>{center.classList.add("is-open");center.setAttribute("aria-hidden","false");centerToggle.setAttribute("aria-expanded","true");if(centerBackdrop){centerBackdrop.hidden=false;requestAnimationFrame(()=>centerBackdrop.classList.add("is-open"));}document.body.classList.add("announcement-center-open");}; const closeCenter=()=>{center.classList.remove("is-open");center.setAttribute("aria-hidden","true");centerToggle.setAttribute("aria-expanded","false");centerBackdrop?.classList.remove("is-open");window.setTimeout(()=>{if(centerBackdrop&&!centerBackdrop.classList.contains("is-open"))centerBackdrop.hidden=true;},220);document.body.classList.remove("announcement-center-open");}; centerToggle.addEventListener("click",()=>center.classList.contains("is-open")?closeCenter():openCenter()); document.querySelectorAll("[data-announcement-center-close]").forEach(el=>el.addEventListener("click",closeCenter)); center.querySelectorAll("[data-announcement-open]").forEach(button=>button.addEventListener("click",async()=>{const item=button.closest(".announcement-history-item");if(!item)return;const willOpen=!item.classList.contains("is-expanded");center.querySelectorAll(".announcement-history-item.is-expanded").forEach(other=>{if(other!==item)other.classList.remove("is-expanded");});item.classList.toggle("is-expanded",willOpen);if(willOpen&&item.classList.contains("is-unread"))await announcementRead(item.dataset.announcementId,center.dataset.csrf);})); document.addEventListener("keydown",e=>{if(e.key==="Escape"&&center.classList.contains("is-open"))closeCenter();}); }
  const announcement=document.getElementById("loginAnnouncement");
  if (announcement) { const card=announcement.querySelector(".announcement-card"),confirm=announcement.querySelector(".announcement-confirm");let dismissing=false;const dismiss=async()=>{if(dismissing||announcement.classList.contains("leaving"))return;dismissing=true;await announcementRead(announcement.dataset.announcementId,announcement.dataset.csrf);announcement.classList.add("leaving");window.setTimeout(()=>announcement.remove(),240);};announcement.querySelectorAll("[data-announcement-dismiss]").forEach(button=>button.addEventListener("click",dismiss));announcement.addEventListener("click",event=>{if(event.target===announcement)dismiss();});document.addEventListener("keydown",event=>{if(event.key==="Escape"&&document.body.contains(announcement))dismiss();});window.requestAnimationFrame(()=>{card?.classList.add("is-ready");confirm?.focus({preventScroll:true});});}

  // Flash messages behave like compact toasts instead of blocking the layout.
  document.querySelectorAll(".client-body .flash").forEach((toast) => {
    toast.classList.add("xnat-toast");
    const timeout = toast.classList.contains("error") ? 6500 : 4200;
    window.setTimeout(() => {
      toast.classList.add("leaving");
      window.setTimeout(() => toast.remove(), 220);
    }, timeout);
  });

  // Copy feedback is inline and never opens an intrusive modal.
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-value]");
    if (!button) return;
    event.preventDefault();
    const value = button.getAttribute("data-copy-value") || "";
    if (!value) return;
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(value);
      button.textContent = "已复制";
      button.classList.add("copied");
    } catch (_) {
      const input = document.createElement("textarea");
      input.value = value;
      input.style.position = "fixed";
      input.style.opacity = "0";
      document.body.appendChild(input);
      input.select();
      document.execCommand("copy");
      input.remove();
      button.textContent = "已复制";
      button.classList.add("copied");
    }
    window.setTimeout(() => {
      button.textContent = original;
      button.classList.remove("copied");
    }, 1300);
  });

  // Normal POSTs keep server-side safety/CSRF semantics but immediately show
  // progress so users know an operation was accepted and cannot double-click.
  document.querySelectorAll(".client-body form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented || form.dataset.noLoading === "true") return;
      const submitter = event.submitter || form.querySelector('button[type="submit"], button:not([type])');
      if (!submitter || submitter.disabled) return;
      submitter.dataset.originalText = submitter.textContent;
      submitter.classList.add("is-loading");
      submitter.disabled = true;
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
    document.querySelectorAll(".client-body button.is-loading").forEach((button) => {
      button.disabled = false;
      button.classList.remove("is-loading");
      if (button.dataset.originalText) button.textContent = button.dataset.originalText;
    });
  });
})();
