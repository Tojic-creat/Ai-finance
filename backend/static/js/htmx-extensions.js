/*
backend/templates/static/js/htmx-extensions.js
Небольшие расширения и удобства для работы с HTMX в проекте FinAssist.

Функции:
- Автоподстановка CSRF-токена (Django) в заголовки htmx запросов.
- Показ/скрытие глобального спиннера при AJAX-запросах.
- Автодебаунс для полей поиска (data-debounce).
- Автоподача формы при выборе файла (data-auto-submit).
- Простая система *confirm* атрибута (data-confirm) — перехватывает клики/submit.
- Простая система toasts (уведомления об ошибках/успехе).
- Автоматический фокус на первом поле после swap.
- Включение/отключение кнопок при запросах (предотвращает двойные отправки).

Подключение:
<script src="{% static 'js/htmx-extensions.js' %}"></script>

Автор: сгенерировано помощником (адаптируйте при необходимости)
*/

(function () {
  "use strict";

  /* ----------------------------- Утилиты ----------------------------- */

  function getMetaToken() {
    // ищем мета-тег с CSRF-токеном (удобно, если в шаблонах вставлен)
    var m = document.querySelector(
      'meta[name="csrf-token"], meta[name="csrfmiddlewaretoken"]'
    );
    return m ? m.getAttribute("content") : null;
  }

  function getCookie(name) {
    // простая функция получения cookie по имени
    var value = "; " + document.cookie;
    var parts = value.split("; " + name + "=");
    if (parts.length === 2) return parts.pop().split(";").shift();
    return null;
  }

  function getCsrfToken() {
    // порядок: meta > cookie
    return (
      getMetaToken() ||
      getCookie("csrftoken") ||
      getCookie("csrf_token") ||
      null
    );
  }

  function createSpinnerOverlay() {
    if (document.getElementById("htmx-spinner-overlay")) return;
    var overlay = document.createElement("div");
    overlay.id = "htmx-spinner-overlay";
    overlay.style = [
      "position:fixed",
      "inset:0",
      "display:none",
      "align-items:center",
      "justify-content:center",
      "z-index:9999",
      "background:rgba(15,23,42,0.25)",
    ].join(";");
    overlay.innerHTML = `
      <div aria-hidden="true" style="display:flex;flex-direction:column;align-items:center;gap:0.5rem">
        <svg width="48" height="48" viewBox="0 0 50 50" fill="none" aria-hidden="true">
          <circle cx="25" cy="25" r="20" stroke="rgba(255,255,255,0.9)" stroke-width="4" stroke-opacity="0.15"/>
          <path d="M45 25a20 20 0 0 1-20 20" stroke="white" stroke-width="4" stroke-linecap="round"/>
        </svg>
        <div style="color:white;font-weight:600">Загрузка…</div>
      </div>
    `;
    document.body.appendChild(overlay);
  }

  function showSpinner() {
    createSpinnerOverlay();
    var el = document.getElementById("htmx-spinner-overlay");
    if (el) el.style.display = "flex";
  }

  function hideSpinner() {
    var el = document.getElementById("htmx-spinner-overlay");
    if (el) el.style.display = "none";
  }

  /* --------------------------- Toasts (уведомления) --------------------------- */
  function ensureToastContainer() {
    var id = "htmx-toast-container";
    var container = document.getElementById(id);
    if (!container) {
      container = document.createElement("div");
      container.id = id;
      container.style = [
        "position: fixed",
        "right: 1rem",
        "top: 1rem",
        "z-index: 11000",
        "display: flex",
        "flex-direction: column",
        "gap: 0.5rem",
        "max-width: 360px",
        "pointer-events: none",
      ].join(";");
      document.body.appendChild(container);
    }
    return container;
  }

  function toast(message, opts) {
    opts = opts || {};
    var container = ensureToastContainer();
    var item = document.createElement("div");
    item.className = "htmx-toast";
    item.style = [
      "pointer-events:auto",
      "background: white",
      "border: 1px solid rgba(15,23,42,0.06)",
      "box-shadow: 0 6px 18px rgba(15,23,42,0.08)",
      "padding: 0.6rem 0.9rem",
      "border-radius: 10px",
      "font-size: 0.95rem",
      "color: #0f172a",
      "display:flex",
      "gap:0.6rem",
      "align-items:flex-start",
    ].join(";");
    item.innerHTML = `<div style="flex:1">${message}</div><button aria-label="Close" style="background:none;border:none;cursor:pointer;font-weight:700">✕</button>`;
    var closeBtn = item.querySelector("button");
    closeBtn.addEventListener("click", function () {
      container.removeChild(item);
    });
    container.appendChild(item);
    var timeout = opts.timeout || 5000;
    if (timeout > 0) {
      setTimeout(function () {
        if (item.parentNode) item.parentNode.removeChild(item);
      }, timeout);
    }
    return item;
  }

  /* -------------------------- HTMX конфигурация -------------------------- */

  // Подставляем CSRF-токен в каждую конфигурацию запроса HTMX
  document.body.addEventListener("htmx:configRequest", function (evt) {
    var token = getCsrfToken();
    if (token) {
      evt.detail.headers["X-CSRFToken"] = token;
      // совместимость с некоторыми бекендами:
      evt.detail.headers["X-CSRF-Token"] = token;
    }
  });

  // Перед запросом — показать спиннер и дизейблить кнопки/inputs с data-htmx-disable
  document.body.addEventListener("htmx:beforeRequest", function (evt) {
    // показать глобальный спиннер (можно переопределить через data attribute)
    var src = evt.detail.elt;
    // Если на элементе есть data-no-overlay, пропускаем глобальный overlay
    if (!src.closest || !src.closest("[data-no-overlay]")) {
      showSpinner();
    }

    // отключаем кнопки внутри формы, если есть data-disable-on-submit
    var form = src.closest && src.closest("form");
    if (form) {
      var disables = form.querySelectorAll("[data-disable-on-submit]");
      disables.forEach(function (el) {
        el.dataset._wasDisabled = el.disabled ? "1" : "0";
        el.disabled = true;
        el.classList && el.classList.add("opacity-60", "cursor-not-allowed");
      });
    }

    // добавим небольшой aria-атрибут загрузки
    src.setAttribute("aria-busy", "true");
  });

  // После запроса — прячем спиннер, возвращаем кнопки
  document.body.addEventListener("htmx:afterRequest", function (evt) {
    hideSpinner();
    var src = evt.detail.elt;
    var form = src.closest && src.closest("form");
    if (form) {
      var disables = form.querySelectorAll("[data-disable-on-submit]");
      disables.forEach(function (el) {
        if (el.dataset._wasDisabled === "0") {
          el.disabled = false;
        }
        delete el.dataset._wasDisabled;
        el.classList && el.classList.remove("opacity-60", "cursor-not-allowed");
      });
    }
    src.removeAttribute("aria-busy");
  });

  // Обработка ошибок — показываем toast с сообщением
  document.body.addEventListener("htmx:onError", function (evt) {
    hideSpinner();
    var status = evt.detail.xhr ? evt.detail.xhr.status : null;
    var msg = "Ошибка при выполнении запроса";
    if (status) msg += " — код " + status;
    // в теле ответа может быть текст с ошибкой
    try {
      var text = evt.detail.xhr && evt.detail.xhr.responseText;
      if (text) {
        // ограничим длину
        msg += ": " + (text.length > 240 ? text.slice(0, 240) + "…" : text);
      }
    } catch (e) {
      /* ignore */
    }
    toast(msg, { timeout: 7000 });
  });

  // После swap — фокусируем первое поле и реинициализируем (если нужно)
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    try {
      var target = evt.detail.elt;
      // если в ответе есть контейнер, фокусируем первое поле
      var first =
        target.querySelector &&
        target.querySelector(
          "input:not([type=hidden]), textarea, select, button"
        );
      if (first) {
        first.focus({ preventScroll: true });
      }
    } catch (e) {
      /* ignore */
    }
  });

  /* ------------------------- data-confirm (подтверждение) ------------------------- */
  // Перехватываем клики и сабмиты для элементов/форм с data-confirm
  function handleConfirmClick(e) {
    var el = e.target;
    // поднимаемся вверх, если клик был по внутреннему элементу
    while (el && el !== document) {
      if (el.hasAttribute && el.hasAttribute("data-confirm")) {
        var msg = el.getAttribute("data-confirm") || "Вы уверены?";
        var useNative = el.getAttribute("data-confirm-native") === "true";
        // Если требуется native confirm (не кастомный), используем его
        if (!confirm(msg)) {
          e.preventDefault();
          e.stopImmediatePropagation();
          return false;
        }
        // иначе — подтверждено, просто продолжаем
        return true;
      }
      el = el.parentNode;
    }
    return true;
  }

  function handleConfirmSubmit(e) {
    var form = e.target;
    if (form && form.hasAttribute && form.hasAttribute("data-confirm")) {
      var msg = form.getAttribute("data-confirm") || "Вы уверены?";
      if (!confirm(msg)) {
        e.preventDefault();
        e.stopImmediatePropagation();
        return false;
      }
    }
    return true;
  }

  document.addEventListener(
    "click",
    function (e) {
      // обрабатываем кнопки / ссылки с data-confirm (предпочтительно раньше чем htmx)
      handleConfirmClick(e);
    },
    { capture: true }
  );

  document.addEventListener(
    "submit",
    function (e) {
      handleConfirmSubmit(e);
    },
    { capture: true }
  );

  /* ------------------------- data-auto-submit (файлы) ------------------------- */
  // При изменении input[type=file][data-auto-submit] — отправляем форму (htmx)
  document.addEventListener(
    "change",
    function (e) {
      var el = e.target;
      if (!el) return;
      if (el.matches && el.matches("input[type=file][data-auto-submit]")) {
        // найдем ближайшую форму
        var form = el.closest("form");
        if (form) {
          // триггерим событие submit, HTMX подхватит hx-post/hx-attrs
          // если у формы стоит hx-attr, можно использовать htmx.trigger
          try {
            // Если форма настроена для htmx (hx-post), используем htmx.submit
            if (typeof htmx !== "undefined" && htmx.closest && htmx.ajax) {
              // htmx.trigger(form, "submit") — не всегда вызывает htmx.ajax; используем native submit
              form.dispatchEvent(new Event("submit", { cancelable: true }));
              // если нативный submit не запустил htmx (нет hx attrs), вызываем htmx.ajax manually (fallback)
              // (не делаем автоматического htmx.ajax без атрибутов — risk)
            } else {
              form.submit();
            }
          } catch (err) {
            // last resort
            form.submit();
          }
        }
      }
    },
    true
  );

  /* --------------------------- data-debounce (поле поиска) --------------------------- */
  // Для полей с data-debounce реализуем debounce и триггерим htmx 'input' (или указанный evt)
  var debounceMap = new WeakMap();

  function debounce(fn, wait) {
    var t;
    return function () {
      var ctx = this,
        args = arguments;
      clearTimeout(t);
      t = setTimeout(function () {
        fn.apply(ctx, args);
      }, wait);
    };
  }

  function wireDebounceInputs(root) {
    root = root || document;
    var nodes = root.querySelectorAll("[data-debounce]");
    nodes.forEach(function (node) {
      var wait = parseInt(node.getAttribute("data-debounce"), 10) || 300;
      var eventName = node.getAttribute("data-debounce-event") || "input";
      if (debounceMap.has(node)) return; // уже подвязано
      var handler = debounce(function (ev) {
        // используем htmx.trigger чтобы HTMX обработал hx-trigger правильно
        if (typeof htmx !== "undefined") {
          htmx.trigger(node, eventName);
        } else {
          // fallback: dispatch native event
          var e = new Event(eventName, { bubbles: true });
          node.dispatchEvent(e);
        }
      }, wait);
      node.addEventListener("input", handler);
      debounceMap.set(node, handler);
    });
  }

  // Wire на загрузке и после HTMX swap
  document.addEventListener("DOMContentLoaded", function () {
    wireDebounceInputs(document);
  });
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    // после swap нужно привязать debounce к вновь вставленным узлам (evt.detail.target is swap target)
    var swapped = evt.detail.target;
    if (swapped) wireDebounceInputs(swapped);
  });

  /* -------------------- Инициализация: небольшие helper'ы -------------------- */

  // expose небольшой API на window для использования в шаблонах / отладки
  window.htmxExtensions = {
    toast: toast,
    showSpinner: showSpinner,
    hideSpinner: hideSpinner,
    getCsrfToken: getCsrfToken,
  };

  // Создаём контейнеры заранее
  createSpinnerOverlay();
  ensureToastContainer();

  // Легкий лог в режиме разработки (опционально)
  if (
    window.location &&
    window.location.search.indexOf("dev_htmx_log=1") !== -1
  ) {
    document.body.addEventListener("htmx:beforeRequest", function (e) {
      console.debug("[htmx] beforeRequest", e);
    });
    document.body.addEventListener("htmx:afterRequest", function (e) {
      console.debug("[htmx] afterRequest", e);
    });
  }

  // Документ готов — небольшая подсказка в консоль
  try {
    console.info(
      "HTMX extensions loaded — CSRF token present:",
      !!getCsrfToken()
    );
  } catch (e) {
    /* ignore */
  }
})();
