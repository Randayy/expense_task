"use strict";

// --- дрібні помічники -------------------------------------------------------

const $ = (sel) => document.querySelector(sel);

// Вхід і реєстрація — єдині запити, де 401 означає «невірні дані»,
// а не «сесія скінчилась». На них не можна викидати людину на форму входу,
// і не можна підміняти повідомлення сервера своїм.
const AUTH_PATHS = ["/api/login", "/api/register"];

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  const data = res.status === 204 ? null : await res.json();

  if (!res.ok) {
    if (res.status === 401 && !AUTH_PATHS.includes(path)) showLogin();
    throw new Error(errorText(data));
  }
  return data;
}

// FastAPI повертає помилки валідації списком — дістаємо звідти людський текст
function errorText(data) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length) return detail[0].msg.replace(/^Value error, /, "");
  return "Щось пішло не так";
}

const money = (n) => "$" + Number(n).toFixed(2);

const STATUS_LABEL = {
  pending: "очікує",
  approved: "погоджено",
  rejected: "відхилено",
  withdrawn: "відкликано",
};

const ROLE_LABEL = {
  employee: "співробітник",
  approver: "погоджувач",
  admin: "адміністратор",
};

function esc(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

// --- стан -------------------------------------------------------------------

let me = null;
let activeTab = "mine";
let poller = null;

// Скільки заявок зараз показано в кожному списку. «Показати ще» збільшує це
// число, а фонове оновлення перечитує рівно стільки ж — щоб список не стрибав.
const PAGE_SIZE = 20;
let shown = {
  "mine-pending": PAGE_SIZE,
  "mine-decided": PAGE_SIZE,
  "queue-pending": PAGE_SIZE,
  "queue-decided": PAGE_SIZE,
};

// --- вхід і реєстрація ------------------------------------------------------

document.querySelectorAll(".switch").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".switch").forEach((b) => b.classList.toggle("active", b === button));
    $("#login-form").hidden = button.dataset.mode !== "login";
    $("#register-form").hidden = button.dataset.mode !== "register";
    $("#auth-error").hidden = true;
  });
});

async function submitAuth(event, path, body) {
  event.preventDefault();
  const error = $("#auth-error");
  error.hidden = true;
  try {
    me = await api(path, { method: "POST", body: JSON.stringify(body) });
    await startApp();
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  }
}

$("#login-form").addEventListener("submit", (event) => {
  const form = new FormData(event.target);
  submitAuth(event, "/api/login", {
    email: form.get("email"),
    password: form.get("password"),
  });
});

$("#register-form").addEventListener("submit", (event) => {
  const form = new FormData(event.target);
  submitAuth(event, "/api/register", {
    name: form.get("name"),
    email: form.get("email"),
    password: form.get("password"),
  });
});

$("#logout").addEventListener("click", async () => {
  await fetch("/api/logout", { method: "POST" });
  showLogin();
});

function showLogin() {
  me = null;
  clearInterval(poller);
  $("#app-screen").hidden = true;
  $("#login-screen").hidden = false;
}

// --- запуск -----------------------------------------------------------------

async function startApp() {
  $("#login-screen").hidden = true;
  $("#app-screen").hidden = false;

  $("#user-name").textContent = me.name;
  $("#user-roles").textContent = " · " + me.roles.map((r) => ROLE_LABEL[r]).join(", ");

  // Вкладки показуємо лише ті, що людині доступні
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.hidden = !me.roles.includes({ mine: "employee", queue: "approver", admin: "admin" }[tab.dataset.tab]);
  });

  if (me.roles.includes("employee")) await loadCategories();

  const firstVisible = [...document.querySelectorAll(".tab")].find((t) => !t.hidden);
  switchTab(firstVisible ? firstVisible.dataset.tab : "mine");

  // «Реальний час» для MVP — тихе опитування раз на 4 секунди.
  clearInterval(poller);
  poller = setInterval(refresh, 4000);
  window.addEventListener("focus", refresh);
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchTab(tab.dataset.tab));
});

function switchTab(name) {
  activeTab = name;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  ["mine", "queue", "admin"].forEach((t) => { $("#tab-" + t).hidden = t !== name; });
  refresh();
}

async function refresh() {
  if (!me) return;
  try {
    if (me.roles.includes("employee")) {
      await renderSection("mine-pending", "/api/claims/mine", "pending", "Нових заявок немає");
      await renderSection("mine-decided", "/api/claims/mine", "decided", "Поки нічого не розглянуто");
    }
    if (me.roles.includes("approver")) {
      const pending = await renderSection(
        "queue-pending", "/api/claims/queue", "pending", "Черга порожня — усе розглянуто");
      await renderSection("queue-decided", "/api/claims/queue", "decided", "Ви ще нічого не розглядали");

      // лічильник на вкладці рахує всі нерозглянуті, а не лише завантажену сторінку
      const badge = $("#queue-badge");
      badge.textContent = pending.total;
      badge.hidden = pending.total === 0;
    }
    if (me.roles.includes("admin") && activeTab === "admin") await renderAdmin();
  } catch (err) {
    // мовчимо: фонове оновлення не має смикати користувача повідомленнями
  }
}

// --- подача заявки ----------------------------------------------------------

let categories = [];

async function loadCategories() {
  categories = await api("/api/categories");
  const select = $("#claim-form select[name=category]");
  select.innerHTML = categories
    .map((c) => `<option ${c.approver ? "" : "disabled"}>${esc(c.category)}</option>`)
    .join("");
  $("#claim-form input[name=expense_date]").max = new Date().toISOString().slice(0, 10);
  select.addEventListener("change", showRoute);
  showRoute();
}

function showRoute() {
  const chosen = $("#claim-form select[name=category]").value;
  const found = categories.find((c) => c.category === chosen);
  const hint = $("#route-hint");
  if (!found) {
    hint.textContent = "";
  } else if (found.approver) {
    hint.textContent = `Погоджує: ${found.approver}`;
    hint.classList.remove("error");
  } else {
    hint.textContent = "Для цієї категорії ще немає погоджувача";
    hint.classList.add("error");
  }
}

$("#claim-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const error = $("#claim-error");
  error.textContent = "";

  try {
    await api("/api/claims", {
      method: "POST",
      body: JSON.stringify({
        amount_usd: form.get("amount_usd"),
        category: form.get("category"),
        description: form.get("description"),
        expense_date: form.get("expense_date"),
        payment_details: form.get("payment_details"),
      }),
    });
    event.target.reset();
    showRoute();
    $("#new-claim-block").open = false;
    refresh();
  } catch (err) {
    error.textContent = err.message;
  }
});

// --- списки заявок ----------------------------------------------------------

function statusChip(status) {
  return `<span class="status status-${status}">${STATUS_LABEL[status]}</span>`;
}

function claimCard(claim, { forApprover }) {
  const comment = claim.decision_comment
    ? `<div class="comment"><b>Коментар погоджувача:</b> ${esc(claim.decision_comment)}</div>`
    : "";

  const canWithdraw = !forApprover && claim.status === "pending";
  const withdrawBtn = canWithdraw
    ? `<button class="secondary" data-withdraw="${claim.id}">Відкликати</button>`
    : "";

  const who = forApprover
    ? `від ${esc(claim.employee_name)}`
    : `погоджує ${esc(claim.approver_name)}`;

  return `
    <div class="claim ${forApprover ? "clickable" : ""}" ${forApprover ? `data-open="${claim.id}"` : ""}>
      <div class="claim-main">
        <div class="claim-amount">${money(claim.amount_usd)} · ${esc(claim.category)}</div>
        <div class="claim-desc">${esc(claim.description)}</div>
        <div class="muted">${esc(claim.expense_date)} · ${who}</div>
        ${comment}
      </div>
      <div class="claim-side">
        ${statusChip(claim.status)}
        <div style="margin-top:8px">${withdrawBtn}</div>
      </div>
    </div>`;
}

function moreButton(page, which) {
  const left = page.total - page.items.length;
  return left > 0
    ? `<button class="secondary more" data-more="${which}">Показати ще (${left})</button>`
    : "";
}

async function renderSection(which, path, state, emptyText) {
  const page = await api(`${path}?state=${state}&limit=${shown[which]}`);
  const forApprover = which.startsWith("queue");
  const list = $("#" + which);

  list.innerHTML = page.items.length
    ? page.items.map((c) => claimCard(c, { forApprover })).join("") + moreButton(page, which)
    : `<div class="empty">${emptyText}</div>`;

  $("#" + which + "-count").textContent = page.total || "";

  const more = list.querySelector("[data-more]");
  if (more) {
    more.addEventListener("click", () => {
      shown[which] += PAGE_SIZE;
      refresh();
    });
  }

  list.querySelectorAll("[data-withdraw]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await api(`/api/claims/${btn.dataset.withdraw}/withdraw`, { method: "POST" });
      } catch (err) {
        alert(err.message);
      }
      refresh();
    });
  });

  list.querySelectorAll("[data-open]").forEach((card) => {
    card.addEventListener("click", (event) => {
      if (event.target.tagName !== "BUTTON") openClaim(Number(card.dataset.open));
    });
  });

  return page;
}

// --- картка заявки для погоджувача -----------------------------------------

async function openClaim(claimId) {
  const dialog = $("#claim-dialog");
  const claim = await api(`/api/claims/${claimId}`);
  const decided = claim.status !== "pending";

  $("#dialog-body").innerHTML = `
    <h2>${money(claim.amount_usd)} · ${esc(claim.category)}</h2>
    <p class="muted">Заявник: ${esc(claim.employee_name)} · ${statusChip(claim.status)}</p>

    <div class="field-row"><span>Дата витрати</span><span>${esc(claim.expense_date)}</span></div>
    <div class="field-row"><span>Опис</span><span>${esc(claim.description)}</span></div>
    <div class="field-row"><span>Деталі для оплати</span><span>${esc(claim.payment_details)}</span></div>

    <div class="ai" id="ai-block">
      <div class="ai-title">AI-підказка</div>
      <div id="ai-content" class="muted">читаю заявку…</div>
    </div>

    ${decided ? `<p class="muted" style="margin-top:16px">Рішення вже ухвалено${
        claim.decision_comment ? `: ${esc(claim.decision_comment)}` : ""}</p>
      <div class="dialog-actions"><button id="btn-close" class="secondary">Закрити</button></div>` : `
      <label class="wide" style="display:block;margin-top:16px;font-size:13px;color:var(--muted)">
        Коментар <small>(обов'язковий при відхиленні)</small>
        <textarea id="decision-comment" rows="2" style="width:100%;margin-top:4px"></textarea>
      </label>
      <p class="error" id="decision-error"></p>
      <div class="dialog-actions">
        <button id="btn-approve">Погодити</button>
        <button id="btn-reject" class="danger">Відхилити</button>
        <button id="btn-close" class="secondary">Закрити</button>
      </div>`}
  `;

  if (!dialog.open) dialog.showModal();

  $("#btn-close").addEventListener("click", () => dialog.close());
  if (!decided) {
    $("#btn-approve").addEventListener("click", () => decide(claimId, "approve"));
    $("#btn-reject").addEventListener("click", () => decide(claimId, "reject"));
  }

  // AI вантажимо окремо і вже після того, як кнопки стали активні:
  // погоджувач може ухвалити рішення, не чекаючи на підказку.
  loadAiReview(claimId);
}

async function loadAiReview(claimId) {
  let review;
  try {
    review = await api(`/api/claims/${claimId}/ai-review`);
  } catch (err) {
    review = { available: false, reason: "AI-сервіс недоступний" };
  }

  const block = $("#ai-block");
  const content = $("#ai-content");
  if (!block || !content) return; // діалог уже закрили

  if (!review.available) {
    block.classList.add("off");
    content.className = "muted";
    content.textContent = review.reason + ". Рішення ухвалюєте ви — це нічого не блокує.";
    return;
  }

  content.className = "";
  content.innerHTML =
    esc(review.summary) +
    (review.flagged
      ? `<div class="ai-flag">⚠️ Схоже на неузгодженість: ${esc(review.flag_reason)}</div>`
      : "");
}

async function decide(claimId, action) {
  const error = $("#decision-error");
  const comment = $("#decision-comment").value.trim();
  error.textContent = "";

  // Та сама вимога, що й на сервері — просто щоб не ганяти запит даремно
  if (action === "reject" && comment.length < 3) {
    error.textContent = "Щоб відхилити заявку, напишіть коментар";
    return;
  }

  try {
    await api(`/api/claims/${claimId}/${action}`, {
      method: "POST",
      body: action === "reject" ? JSON.stringify({ comment }) : null,
    });
    $("#claim-dialog").close();
    refresh();
  } catch (err) {
    error.textContent = err.message;
  }
}

// --- адміністрування --------------------------------------------------------

async function renderAdmin() {
  const [routing, users] = await Promise.all([
    api("/api/admin/routing"),
    api("/api/admin/users"),
  ]);
  renderRouting(routing);
  renderUsers(users);
}

function renderRouting({ categories: rows, approvers }) {
  const options = (selected) =>
    [`<option value="">— не призначено —</option>`]
      .concat(approvers.map((a) =>
        `<option value="${a.id}" ${a.id === selected ? "selected" : ""}>${esc(a.name)}</option>`))
      .join("");

  $("#routing-list").innerHTML = rows
    .map((row) => `
      <div class="admin-row">
        <span>${esc(row.category)}</span>
        <select data-category="${esc(row.category)}" ${approvers.length ? "" : "disabled"}>
          ${options(row.approver_id)}
        </select>
      </div>`)
    .join("") + (approvers.length ? "" :
      `<p class="muted">Спершу призначте комусь роль погоджувача нижче.</p>`);

  $("#routing-list").querySelectorAll("select[data-category]").forEach((select) => {
    select.addEventListener("change", async () => {
      if (!select.value) return;
      await adminAction(() =>
        api("/api/admin/routing", {
          method: "PUT",
          body: JSON.stringify({
            category: select.dataset.category,
            approver_id: Number(select.value),
          }),
        }));
    });
  });
}

function renderUsers(users) {
  $("#users-list").innerHTML = users
    .map((user) => `
      <div class="admin-row">
        <span>${esc(user.name)} <small class="muted">${esc(user.email)}</small></span>
        <span class="role-boxes">
          ${["employee", "approver", "admin"].map((role) => `
            <label class="role-box">
              <input type="checkbox" data-user="${user.id}" data-role="${role}"
                     ${user.roles.includes(role) ? "checked" : ""}>
              ${ROLE_LABEL[role]}
            </label>`).join("")}
        </span>
      </div>`)
    .join("");

  $("#users-list").querySelectorAll("input[data-user]").forEach((box) => {
    box.addEventListener("change", async () => {
      const userId = box.dataset.user;
      const roles = [...$("#users-list").querySelectorAll(`input[data-user="${userId}"]:checked`)]
        .map((b) => b.dataset.role);

      if (!roles.length) {
        box.checked = true;
        $("#admin-error").textContent = "Хоча б одна роль має лишитися";
        return;
      }
      await adminAction(() =>
        api(`/api/admin/users/${userId}/roles`, { method: "PUT", body: JSON.stringify({ roles }) }));
    });
  });
}

async function adminAction(action) {
  const error = $("#admin-error");
  error.textContent = "";
  try {
    await action();
  } catch (err) {
    error.textContent = err.message;
  }
  await renderAdmin();
  if (me.roles.includes("employee")) await loadCategories();
}

// --- старт ------------------------------------------------------------------

api("/api/me")
  .then((user) => {
    me = user;
    return startApp();
  })
  .catch(() => showLogin());
