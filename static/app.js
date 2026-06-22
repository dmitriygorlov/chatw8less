function $(id) {
    return document.getElementById(id);
}

const state = {
    tool: "food",
    statsView: "today",
    bootstrap: null,
    locale: window.CHATW8LESS_LOCALE || { language_code: "en", messages: {}, languages: [] },
    pendingSaveItems: null,
    savingPendingAnalysis: false,
    visibleNutritionDays: 1,
    visibleStatsDays: 10,
    lastReplyText: "",
    assistantHistoryExpanded: false,
    activeMessageHistory: "nutrition",
    visibleMessageDialogs: {
        assistant: 10,
        nutrition: 10,
    },
    lastBootstrapRefreshAt: 0,
    bootstrapRefreshPromise: null,
    sections: {
        stats: true,
        settings: true,
        nutrition: true,
        messages: true,
    },
    lastMobileLayout: null,
    importVisible: false,
};

const assistantPrompts = [
    { label: "web.prompt_cook_label", prompt: "web.prompt_cook" },
    { label: "web.prompt_nutrition_label", prompt: "web.prompt_nutrition" },
    { label: "web.prompt_tasty_label", prompt: "web.prompt_tasty" },
];

const assistantContextMessageLimit = 10;
const assistantDefaultMessageLimit = 2;

function t(key, params = {}) {
    let text = state.locale.messages[key] || key;
    const mergedParams = {
        assistant_name: assistantDisplayName(),
        ...params,
    };
    Object.entries(mergedParams).forEach(([name, value]) => {
        text = text.replaceAll(`{${name}}`, String(value));
    });
    return text;
}

function assistantDisplayName() {
    return state.bootstrap?.settings?.assistant_name || "Alex";
}

function speakerLabel(role) {
    return role === "user" ? t("common.user") : assistantDisplayName();
}

function applyStaticTranslations() {
    document.documentElement.lang = state.locale.language_code || "en";
    document.querySelectorAll("[data-i18n]").forEach((node) => {
        node.textContent = t(node.dataset.i18n);
    });
    const chatInput = $("chat-input");
    const photoCaption = $("photo-caption");
    const customLanguage = $("custom-language-input");
    if (photoCaption) {
        photoCaption.placeholder = t("web.photo_caption");
    }
    if (customLanguage) {
        customLanguage.placeholder = t("language.generate_placeholder");
    }
    if (chatInput) {
        updateToolUI();
    }
}

function renderAssistantPrompts() {
    const container = $("assistant-prompts");
    if (!container) {
        return;
    }
    container.innerHTML = assistantPrompts.map(({ label, prompt }) => `
        <button type="button" class="quick-prompt-button" data-assistant-prompt="${prompt}">
            ${escapeHtml(t(label))}
        </button>
    `).join("");
}

function assistantHistoryMessages(messages = []) {
    return messages.filter((message) => (
        message.source === "web_assistant"
        && (message.role === "user" || message.role === "assistant")
    )).slice(-assistantContextMessageLimit);
}

function renderAssistantHistory(messages = state.bootstrap?.messages || []) {
    const card = $("assistant-history-card");
    const list = $("assistant-history-list");
    const toggle = $("toggle-assistant-history-button");
    if (!card || !list || !toggle) {
        return;
    }

    const contextMessages = assistantHistoryMessages(messages);
    const hasHistory = contextMessages.length > 0;
    card.classList.toggle("hidden", state.tool !== "online" || !hasHistory);
    if (!hasHistory) {
        list.innerHTML = "";
        toggle.classList.add("hidden");
        return;
    }

    const visibleLimit = state.assistantHistoryExpanded
        ? assistantContextMessageLimit
        : assistantDefaultMessageLimit;
    const visibleMessages = contextMessages.slice(-visibleLimit);
    list.innerHTML = visibleMessages.map((message) => `
        <article class="assistant-history-message ${message.role}">
            <div class="message-meta">${speakerLabel(message.role)} · ${formatTime(message.created_at)}</div>
            <div class="message-text">${renderMessageContent(message)}</div>
        </article>
    `).join("");

    const canExpand = contextMessages.length > assistantDefaultMessageLimit;
    toggle.classList.toggle("hidden", !canExpand);
    toggle.textContent = state.assistantHistoryExpanded
        ? t("web.show_last_dialog")
        : t("web.show_more_dialogs");
}

function showToast(message) {
    const toast = $("toast");
    toast.textContent = message;
    toast.classList.remove("hidden");
    window.clearTimeout(showToast.timer);
    showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 3200);
}

function setActionLoading(isLoading, label = t("web.processing")) {
    const primary = $("primary-action");
    const loading = $("action-loading");
    const loadingText = $("action-loading-text");
    if (primary) {
        primary.disabled = isLoading;
    }
    if (loading) {
        loading.classList.toggle("hidden", !isLoading);
    }
    if (loadingText) {
        loadingText.textContent = label;
    }
}

async function apiFetch(url, options = {}) {
    const headers = { ...(options.headers || {}) };
    if (!(options.body instanceof FormData)) {
        headers["Content-Type"] = headers["Content-Type"] || "application/json";
    }

    const response = await fetch(url, {
        cache: "no-store",
        credentials: "same-origin",
        ...options,
        headers,
    });
    const payload = await response.json();
    if (!response.ok) {
        throw new Error(payload.detail || "Request failed");
    }
    return payload;
}

function sourceLabel(source) {
    if (source === "telegram") {
        return t("common.telegram");
    }
    if (source === "web") {
        return t("common.site");
    }
    if (source === "web_assistant") {
        return t("web.ask_assistant");
    }
    return source || t("common.system");
}

function formatTime(iso) {
    try {
        return new Date(iso).toLocaleString(state.locale.language_code || "en", {
            hour: "2-digit",
            minute: "2-digit",
            day: "2-digit",
            month: "2-digit",
        });
    } catch {
        return iso || "";
    }
}

function escapeHtml(text) {
    return String(text || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function escapeAttribute(text) {
    return escapeHtml(text).replaceAll("`", "&#96;");
}

function isSafeUrl(url) {
    try {
        const parsed = new URL(url, window.location.origin);
        return ["http:", "https:", "mailto:"].includes(parsed.protocol);
    } catch {
        return false;
    }
}

function renderInlineMarkdown(text) {
    let html = escapeHtml(text);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g, (match, label, url) => {
        if (!isSafeUrl(url)) {
            return match;
        }
        return `<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
    });
    html = html.replace(/\*\*([^*\n][\s\S]*?[^*\n])\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__([^_\n][\s\S]*?[^_\n])__/g, "<strong>$1</strong>");
    html = html.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    html = html.replace(/(^|[\s(])_([^_\n]+)_/g, "$1<em>$2</em>");
    return html;
}

function renderMarkdown(text) {
    const blocks = [];
    let paragraph = [];
    let list = [];

    function flushParagraph() {
        if (!paragraph.length) {
            return;
        }
        blocks.push(`<p>${renderInlineMarkdown(paragraph.join("\n")).replaceAll("\n", "<br>")}</p>`);
        paragraph = [];
    }

    function flushList() {
        if (!list.length) {
            return;
        }
        blocks.push(`<ul>${list.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
        list = [];
    }

    String(text || "").split(/\r?\n/).forEach((line) => {
        const trimmed = line.trim();
        const bullet = trimmed.match(/^[-*]\s+(.+)$/);
        if (!trimmed) {
            flushParagraph();
            flushList();
            return;
        }
        if (bullet) {
            flushParagraph();
            list.push(bullet[1]);
            return;
        }
        flushList();
        paragraph.push(line);
    });

    flushParagraph();
    flushList();
    return blocks.join("");
}

function renderMessageContent(message) {
    if (message.role === "assistant") {
        return renderMarkdown(message.content);
    }
    return escapeHtml(message.content).replaceAll("\n", "<br>");
}

function formatMacroLine(total) {
    return `${total.calories} ${t("common.calories")} · ${t("common.protein_short")} ${total.protein} ${t("common.grams")} · ${t("common.fat_short")} ${total.fat} ${t("common.grams")} · ${t("common.carbs_short")} ${total.carbs} ${t("common.grams")}`;
}

function isMobileLayout() {
    return window.matchMedia("(max-width: 720px)").matches;
}

function initializeSectionState() {
    const mobile = isMobileLayout();
    state.lastMobileLayout = mobile;
    state.sections = mobile
        ? { stats: true, settings: true, nutrition: false, messages: false }
        : { stats: true, settings: true, nutrition: true, messages: true };
}

function syncSectionStateWithViewport() {
    const mobile = isMobileLayout();
    if (state.lastMobileLayout === mobile) {
        return;
    }
    initializeSectionState();
    renderSectionVisibility();
}

function renderSectionVisibility() {
    Object.entries(state.sections).forEach(([key, isOpen]) => {
        const content = $(`section-${key}-content`);
        const button = document.querySelector(`[data-toggle-section="${key}"]`);
        if (content) {
            content.classList.toggle("hidden", !isOpen);
        }
        if (button) {
            button.textContent = isOpen ? t("web.hide") : t("web.show");
        }
    });
}

function messageHistoryCategory(message) {
    return message.source === "web_assistant" ? "assistant" : "nutrition";
}

function groupMessagesIntoDialogs(messages) {
    const grouped = { assistant: [], nutrition: [] };
    messages.forEach((message) => {
        const category = messageHistoryCategory(message);
        const dialogs = grouped[category];
        const previous = dialogs[dialogs.length - 1];
        const startsDialog = (
            message.role === "user"
            || !previous
            || previous.messages.some((item) => item.role === "assistant")
        );
        if (startsDialog) {
            dialogs.push({ messages: [message] });
        } else {
            previous.messages.push(message);
        }
    });
    grouped.assistant.reverse();
    grouped.nutrition.reverse();
    return grouped;
}

function renderMessageDialog(dialog) {
    return `
        <article class="message-dialog">
            ${dialog.messages.map((message) => `
                <div class="message ${message.role}">
                    <div class="message-meta">${speakerLabel(message.role)} · ${sourceLabel(message.source)} · ${formatTime(message.created_at)}</div>
                    <div class="message-text">${renderMessageContent(message)}</div>
                </div>
            `).join("")}
        </article>
    `;
}

function renderMessageHistoryGroup(category, dialogs) {
    const visibleLimit = state.visibleMessageDialogs[category];
    const visibleDialogs = dialogs.slice(0, visibleLimit);
    const remaining = dialogs.length - visibleDialogs.length;
    const titleKey = category === "assistant"
        ? "web.dialog_history_assistant"
        : "web.dialog_history_nutrition";

    return `
        <section class="message-history-group" aria-labelledby="message-history-${category}">
            <h3 id="message-history-${category}" class="visually-hidden">${escapeHtml(t(titleKey))}</h3>
            <div class="message-dialog-list">
                ${visibleDialogs.length
                    ? visibleDialogs.map(renderMessageDialog).join("")
                    : `<div class="empty-state">${escapeHtml(t("web.empty_history"))}</div>`}
            </div>
            ${remaining > 0 ? `
                <button type="button" class="ghost-button wide-button" data-message-more="${category}">
                    ${escapeHtml(t("web.show_earlier_dialogs", { count: remaining }))}
                </button>
            ` : ""}
        </section>
    `;
}

function renderMessages(messages) {
    const list = $("message-list");
    if (!list) {
        return;
    }
    if (!messages.length) {
        list.innerHTML = `<article class="message assistant"><div class="message-meta">${t("web.empty_history")}</div><div class="message-text">${t("web.empty_history_hint")}</div></article>`;
        return;
    }

    const dialogs = groupMessagesIntoDialogs(messages);
    const activeCategory = state.activeMessageHistory;
    list.innerHTML = `
        <div class="message-history-tabs" role="tablist" aria-label="${escapeAttribute(t("web.dialog_history"))}">
            <button type="button" class="mode-pill ${activeCategory === "nutrition" ? "active" : ""}"
                    data-message-category="nutrition" role="tab"
                    aria-selected="${activeCategory === "nutrition"}">
                ${escapeHtml(t("web.dialog_history_nutrition"))}
            </button>
            <button type="button" class="mode-pill ${activeCategory === "assistant" ? "active" : ""}"
                    data-message-category="assistant" role="tab"
                    aria-selected="${activeCategory === "assistant"}">
                ${escapeHtml(t("web.dialog_history_assistant"))}
            </button>
        </div>
        ${renderMessageHistoryGroup(activeCategory, dialogs[activeCategory])}
    `;
}

function renderLastResponse(replyText) {
    const card = $("last-response-card");
    const text = $("last-response-text");
    if (!card || !text) {
        return;
    }

    state.lastReplyText = replyText || "";
    if (!state.lastReplyText) {
        card.classList.add("hidden");
        text.innerHTML = "";
        return;
    }

    card.classList.remove("hidden");
    text.innerHTML = renderMarkdown(state.lastReplyText);
}

function setPendingSaveLoading(isLoading) {
    state.savingPendingAnalysis = isLoading;
    const button = $("save-last-analysis-button");
    if (!button) {
        return;
    }
    button.disabled = isLoading;
    button.textContent = isLoading ? t("web.saving") : t("web.save_last");
}

function updatePendingSave(items) {
    state.pendingSaveItems = Array.isArray(items) && items.length ? items : null;
    const card = $("pending-save-card");
    const text = $("pending-save-text");
    if (!card || !text) {
        return;
    }

    if (!state.pendingSaveItems) {
        card.classList.add("hidden");
        text.textContent = t("web.pending_save_default");
        setPendingSaveLoading(false);
        return;
    }

    card.classList.remove("hidden");
    const count = state.pendingSaveItems.length;
    text.textContent = count === 1
        ? t("web.pending_save_one")
        : t("web.pending_save_many", { count });
}

function renderNutritionHistory(entries) {
    const root = $("nutrition-history");
    if (!root) {
        return;
    }
    if (!entries.length) {
        root.innerHTML = `<div class="empty-state">${t("web.empty_meals")}</div>`;
        return;
    }

    const visibleEntries = entries.slice(0, state.visibleNutritionDays);
    const moreCount = entries.length - visibleEntries.length;

    root.innerHTML = `
        ${visibleEntries.map((day) => `
            <section class="day-card">
                <div class="day-head">
                    <div>
                        <h3>${escapeHtml(day.date)}</h3>
                        <p>${formatMacroLine(day.total)}</p>
                    </div>
                </div>
                <div class="meal-list">
                    ${day.meals.map((meal) => `
                        <article class="meal-card">
                            <div class="meal-head">
                                <div>
                                    <strong>${t("stats.meal")} ${meal.meal_number}</strong>
                                    <p>${formatMacroLine(meal.total)}</p>
                                </div>
                                <button type="button" class="danger-button" data-action="delete-meal" data-date="${escapeHtml(day.date)}" data-meal="${meal.meal_number}">${t("web.delete_meal")}</button>
                            </div>
                            <div class="item-list">
                                ${meal.items.map((item, index) => `
                                    <div class="item-row">
                                        <div>
                                            <strong>${escapeHtml(item.name)}</strong>
                                            <p>${item.amount_grams} ${t("common.grams")} · ${item.calories} ${t("common.calories")} · ${t("common.protein_short")} ${item.protein} · ${t("common.fat_short")} ${item.fat} · ${t("common.carbs_short")} ${item.carbs}</p>
                                        </div>
                                        <button type="button" class="ghost-button small-button" data-action="delete-item" data-date="${escapeHtml(day.date)}" data-meal="${meal.meal_number}" data-item="${index}">${t("web.delete")}</button>
                                    </div>
                                `).join("")}
                            </div>
                        </article>
                    `).join("")}
                </div>
            </section>
        `).join("")}
        ${moreCount > 0 ? `<button type="button" id="more-history-button" class="ghost-button wide-button">${t("web.more_days", { count: moreCount })}</button>` : ""}
    `;
}

function buildAllStatsMarkup(entries) {
    const visibleEntries = entries.slice(0, state.visibleStatsDays);
    const moreCount = entries.length - visibleEntries.length;
    const grandTotal = entries.reduce((acc, day) => {
        acc.calories += Number(day.total.calories || 0);
        acc.protein += Number(day.total.protein || 0);
        acc.fat += Number(day.total.fat || 0);
        acc.carbs += Number(day.total.carbs || 0);
        return acc;
    }, { calories: 0, protein: 0, fat: 0, carbs: 0 });

    return `
        <div class="stats-summary-card">
            <strong>${t("web.total_days", { count: entries.length })}</strong>
            <p>${formatMacroLine(grandTotal)}</p>
        </div>
        <div class="stats-day-list">
            ${visibleEntries.map((day) => `
                <div class="stats-day-row">
                    <strong>${escapeHtml(day.date)}</strong>
                    <span>${formatMacroLine(day.total)}</span>
                </div>
            `).join("")}
        </div>
        ${moreCount > 0 ? `<button type="button" id="more-stats-button" class="ghost-button wide-button">${t("web.more_days", { count: moreCount })}</button>` : ""}
    `;
}

function renderStats(stats) {
    const output = $("stats-output");
    if (!output) {
        return;
    }

    document.querySelectorAll(".stats-tab").forEach((button) => {
        button.classList.toggle("active", button.dataset.stats === state.statsView);
    });

    if (state.statsView === "all") {
        const history = state.bootstrap?.nutrition_history || [];
        output.innerHTML = history.length ? buildAllStatsMarkup(history) : `<div class="empty-state">${t("web.empty_days")}</div>`;
        return;
    }

    output.textContent = (stats && stats[state.statsView]) || t("common.no_data");
}

function renderModes(bootstrap) {
    const buttons = $("mode-buttons");
    if (!buttons) {
        return;
    }

    const currentMode = bootstrap.settings.mode;
    buttons.innerHTML = bootstrap.available_modes.map((mode) => `
        <button type="button" class="mode-pill ${mode.key === currentMode ? "active" : ""}" data-mode="${mode.key}">${escapeHtml(mode.label)}</button>
    `).join("");
}

function renderLanguages(bootstrap) {
    const buttons = $("language-buttons");
    if (!buttons) {
        return;
    }
    const current = bootstrap.settings.language_code;
    buttons.innerHTML = (state.locale.languages || []).map((language) => `
        <button type="button" class="mode-pill ${language.code === current ? "active" : ""}" data-language="${language.code}">${escapeHtml(language.name)}</button>
    `).join("");
}

function renderSettings(bootstrap) {
    renderModes(bootstrap);
    renderLanguages(bootstrap);
    const input = $("daily-limit-input");
    if (input) {
        input.value = bootstrap.settings.daily_limit || "";
    }
    const assistantNameInput = $("assistant-name-input");
    if (assistantNameInput) {
        assistantNameInput.value = bootstrap.settings.assistant_name || "Alex";
    }
}

function applyBootstrap(data) {
    state.bootstrap = data;
    if (data.locale) {
        state.locale = data.locale;
        applyStaticTranslations();
    }
    state.visibleNutritionDays = Math.min(state.visibleNutritionDays, Math.max(data.nutrition_history.length, 1));
    state.visibleStatsDays = Math.min(state.visibleStatsDays, Math.max(data.nutrition_history.length, 10));
    $("user-subtitle").innerHTML = `<strong>${escapeHtml(data.user.display_name)}</strong>`;
    renderMessages(data.messages || []);
    renderAssistantHistory(data.messages || []);
    renderSettings(data);
    renderStats(data.stats || {});
    renderNutritionHistory(data.nutrition_history || []);
    renderSectionVisibility();
    updatePendingSave(state.pendingSaveItems);
}

function updateToolUI() {
    document.querySelectorAll(".tool-tab").forEach((button) => {
        button.classList.toggle("active", button.dataset.tool === state.tool);
    });

    const primary = $("primary-action");
    const photoBox = $("photo-box");
    const textBox = $("text-box");
    const input = $("chat-input");
    const prompts = $("assistant-prompts");
    if (!primary || !photoBox || !textBox || !input) {
        return;
    }
    if (prompts) {
        prompts.classList.toggle("hidden", state.tool !== "online");
    }
    renderAssistantHistory(state.bootstrap?.messages || []);

    if (state.tool === "food") {
        primary.textContent = t("web.estimate");
        textBox.classList.remove("hidden");
        photoBox.classList.add("hidden");
        input.placeholder = t("web.placeholder_food");
        return;
    }

    if (state.tool === "photo") {
        primary.textContent = t("web.estimate_photo");
        textBox.classList.add("hidden");
        photoBox.classList.remove("hidden");
        return;
    }

    textBox.classList.remove("hidden");
    photoBox.classList.add("hidden");

    if (state.tool === "hundred") {
        primary.textContent = t("web.tool_hundred");
        input.placeholder = t("web.placeholder_hundred");
        return;
    }

    primary.textContent = t("web.ask");
    input.placeholder = t("web.placeholder_online");
    renderAssistantPrompts();
}

async function refreshBootstrap({ force = false } = {}) {
    const now = Date.now();
    if (!force && now - state.lastBootstrapRefreshAt < 5000) {
        return state.bootstrap;
    }
    if (state.bootstrapRefreshPromise) {
        return state.bootstrapRefreshPromise;
    }

    state.bootstrapRefreshPromise = (async () => {
        const data = await apiFetch(`/api/bootstrap?_=${Date.now()}`, {
            method: "GET",
            headers: {},
        });
        applyBootstrap(data);
        state.lastBootstrapRefreshAt = Date.now();
        return data;
    })();

    try {
        return await state.bootstrapRefreshPromise;
    } finally {
        state.bootstrapRefreshPromise = null;
    }
}

async function refreshWhenPageReturns() {
    if (document.visibilityState === "hidden") {
        return;
    }
    try {
        await refreshBootstrap();
    } catch (error) {
        if (navigator.onLine) {
            showToast(error.message || t("common.error"));
        }
    }
}

function applyResponseToUI(response) {
    if (state.bootstrap && response.messages) {
        state.bootstrap.messages = response.messages;
    }
    renderMessages(response.messages || []);
    renderAssistantHistory(response.messages || []);
    renderLastResponse(response.reply_text || "");
    if (state.bootstrap && response.settings) {
        state.bootstrap.settings = response.settings;
    }
    if (state.bootstrap && response.stats) {
        state.bootstrap.stats = response.stats;
    }
    if (state.bootstrap && response.nutrition_history) {
        state.bootstrap.nutrition_history = response.nutrition_history;
    }
    if (response.stats) {
        renderStats(response.stats);
    }
    if (response.nutrition_history) {
        renderNutritionHistory(response.nutrition_history);
    }
    updatePendingSave(response.items || null);
}

async function analyzeFoodText(text) {
    const response = await apiFetch("/api/analyze-text", {
        method: "POST",
        body: JSON.stringify({ text, save: false }),
    });
    applyResponseToUI(response);
    return response;
}

async function submitTextAction(endpoint, text) {
    const response = await apiFetch(endpoint, {
        method: "POST",
        body: JSON.stringify({ text }),
    });
    renderMessages(response.messages || []);
    if (state.bootstrap && response.messages) {
        state.bootstrap.messages = response.messages;
    }
    renderAssistantHistory(response.messages || []);
    renderLastResponse(response.reply_text || "");
    updatePendingSave(null);
    showToast(t("web.ready"));
}

async function analyzePhotoAndFood() {
    const fileInput = $("photo-input");
    const captionInput = $("photo-caption");
    if (!fileInput.files || !fileInput.files[0]) {
        showToast(t("web.photo_first"));
        return;
    }

    const formData = new FormData();
    formData.append("photo", fileInput.files[0]);
    formData.append("caption", captionInput.value.trim());
    const photoResponse = await apiFetch("/api/photo/analyze", {
        method: "POST",
        body: formData,
        headers: {},
    });

    const caption = captionInput.value.trim();
    const combinedText = [caption, photoResponse.recognized_text].filter(Boolean).join("\n");
    fileInput.value = "";
    captionInput.value = "";
    await analyzeFoodText(combinedText);
    showToast(t("web.photo_processed"));
}

async function savePendingAnalysis() {
    if (state.savingPendingAnalysis) {
        return;
    }
    if (!state.pendingSaveItems) {
        showToast(t("web.pending_save_default"));
        return;
    }

    const itemsToSave = state.pendingSaveItems;
    setPendingSaveLoading(true);

    try {
        const response = await apiFetch("/api/save-meal", {
            method: "POST",
            body: JSON.stringify({ items: itemsToSave }),
        });

        if (state.bootstrap) {
            state.bootstrap.nutrition_history = response.nutrition_history;
            state.bootstrap.stats = response.stats;
            state.visibleNutritionDays = Math.max(state.visibleNutritionDays, 1);
        }
        renderNutritionHistory(response.nutrition_history || []);
        renderStats(response.stats || {});
        updatePendingSave(null);
        renderLastResponse("");
        showToast(t("web.saved_as", { date: response.saved.date }));
    } finally {
        setPendingSaveLoading(false);
    }
}

async function saveLimit() {
    const raw = $("daily-limit-input").value.trim();
    const payload = { daily_limit: raw === "" ? null : Number(raw) };
    const response = await apiFetch("/api/settings/limit", {
        method: "POST",
        body: JSON.stringify(payload),
    });
    if (state.bootstrap) {
        state.bootstrap.settings = response.settings;
        state.bootstrap.stats = response.stats;
    }
    renderStats(response.stats);
    showToast(payload.daily_limit ? t("web.limit_saved") : t("web.limit_cleared"));
}

async function setMode(mode) {
    const response = await apiFetch("/api/settings/mode", {
        method: "POST",
        body: JSON.stringify({ mode }),
    });
    if (state.bootstrap) {
        state.bootstrap.settings = response.settings;
        renderSettings(state.bootstrap);
    }
    showToast(t("web.update_mode"));
}

async function setLanguage(languageCode, generate = false) {
    const response = await apiFetch("/api/settings/language", {
        method: "POST",
        body: JSON.stringify({ language_code: languageCode, generate }),
    });
    if (state.bootstrap) {
        state.bootstrap.settings = response.settings;
        state.bootstrap.available_modes = response.available_modes;
        state.bootstrap.stats = response.stats;
    }
    if (response.locale) {
        state.locale = response.locale;
    }
    applyStaticTranslations();
    renderSettings(state.bootstrap);
    renderMessages(state.bootstrap?.messages || []);
    renderAssistantHistory(state.bootstrap?.messages || []);
    renderStats(response.stats || state.bootstrap?.stats || {});
    renderNutritionHistory(state.bootstrap?.nutrition_history || []);
    renderSectionVisibility();
    updatePendingSave(state.pendingSaveItems);
    showToast(t("language.saved_toast"));
}

async function saveAssistantName() {
    const input = $("assistant-name-input");
    const assistantName = input.value.trim();
    const response = await apiFetch("/api/settings/assistant-name", {
        method: "POST",
        body: JSON.stringify({ assistant_name: assistantName }),
    });
    if (state.bootstrap) {
        state.bootstrap.settings = response.settings;
        renderSettings(state.bootstrap);
        renderMessages(state.bootstrap.messages || []);
        renderAssistantHistory(state.bootstrap.messages || []);
    }
    showToast(t("web.assistant_name_saved", { assistant_name: response.settings.assistant_name }));
}

async function generateLanguage() {
    const input = $("custom-language-input");
    const value = (input?.value || "").trim();
    if (!value) {
        showToast(t("language.prompt_custom"));
        return;
    }
    const button = $("generate-language-button");
    if (button) {
        button.disabled = true;
        button.textContent = t("language.generating");
    }
    try {
        await setLanguage(value, true);
        if (input) {
            input.value = "";
        }
        showToast(t("language.generated", { language_name: state.locale.languages.find((item) => item.code === state.bootstrap.settings.language_code)?.name || value }));
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = t("language.generate_button");
        }
    }
}

async function importLegacyStorageArchive() {
    const input = $("legacy-import-input");
    if (!input.files || !input.files[0]) {
        showToast(t("web.photo_first"));
        return;
    }

    const formData = new FormData();
    formData.append("archive", input.files[0]);
    const response = await apiFetch("/api/import-legacy-storage", {
        method: "POST",
        body: formData,
        headers: {},
    });
    input.value = "";
    applyBootstrap(response.dashboard);
    showToast(t("web.ready"));
}

async function handleHistoryDelete(action, dataset) {
    const { date, meal, item } = dataset;
    const endpoint = action === "delete-item"
        ? `/api/nutrition-history/${encodeURIComponent(date)}/${meal}/${item}`
        : `/api/nutrition-history/${encodeURIComponent(date)}/${meal}`;
    const response = await apiFetch(endpoint, { method: "DELETE", headers: {} });
    if (state.bootstrap) {
        state.bootstrap.nutrition_history = response.history;
        state.bootstrap.stats = response.stats;
    }
    renderNutritionHistory(response.history);
    renderStats(response.stats);
    showToast(action === "delete-item" ? t("web.delete_item") : t("web.delete_meal"));
}

function bindEvents() {
    $("chat-form").addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
            if (state.tool === "food") {
                const text = $("chat-input").value.trim();
                if (!text) {
                    showToast(t("web.text_required"));
                    return;
                }
                setActionLoading(true, t("web.estimate"));
                await analyzeFoodText(text);
                $("chat-input").value = "";
                showToast(t("web.ready"));
                return;
            }

            if (state.tool === "photo") {
                setActionLoading(true, t("web.estimate_photo"));
                await analyzePhotoAndFood();
                return;
            }

            const text = $("chat-input").value.trim();
            if (!text) {
                showToast(t("web.text_required"));
                return;
            }
            if (state.tool === "hundred") {
                setActionLoading(true, t("web.tool_hundred"));
                await submitTextAction("/api/hundred", text);
            } else {
                setActionLoading(true, t("web.ask"));
                await submitTextAction("/api/online", text);
            }
            $("chat-input").value = "";
        } catch (error) {
            showToast(error.message || t("common.error"));
        } finally {
            setActionLoading(false);
        }
    });

    document.querySelectorAll(".tool-tab").forEach((button) => {
        button.addEventListener("click", () => {
            state.tool = button.dataset.tool;
            updateToolUI();
        });
    });

    document.addEventListener("click", async (event) => {
        const target = event.target.closest("button");
        if (!target) {
            return;
        }

        if (target.matches("[data-stats]")) {
            state.statsView = target.dataset.stats;
            if (state.bootstrap) {
                renderStats(state.bootstrap.stats || {});
            }
            return;
        }
        if (target.matches("[data-toggle-section]")) {
            const section = target.dataset.toggleSection;
            state.sections[section] = !state.sections[section];
            renderSectionVisibility();
            return;
        }
        if (target.id === "more-history-button") {
            state.visibleNutritionDays += 7;
            renderNutritionHistory(state.bootstrap?.nutrition_history || []);
            return;
        }
        if (target.id === "more-stats-button") {
            state.visibleStatsDays += 7;
            renderStats(state.bootstrap?.stats || {});
            return;
        }
        if (target.matches("[data-message-more]")) {
            const category = target.dataset.messageMore;
            state.visibleMessageDialogs[category] += 10;
            renderMessages(state.bootstrap?.messages || []);
            return;
        }
        if (target.matches("[data-message-category]")) {
            state.activeMessageHistory = target.dataset.messageCategory;
            renderMessages(state.bootstrap?.messages || []);
            return;
        }
        if (target.id === "save-last-analysis-button") {
            try {
                await savePendingAnalysis();
            } catch (error) {
                showToast(error.message || t("common.error"));
            }
            return;
        }
        if (target.id === "toggle-import-button") {
            state.importVisible = !state.importVisible;
            $("import-content").classList.toggle("hidden", !state.importVisible);
            target.textContent = state.importVisible ? t("web.import_hide") : t("web.import_show");
            return;
        }
        if (target.id === "toggle-assistant-history-button") {
            state.assistantHistoryExpanded = !state.assistantHistoryExpanded;
            renderAssistantHistory(state.bootstrap?.messages || []);
            return;
        }
        if (target.matches("[data-mode]")) {
            try {
                await setMode(target.dataset.mode);
            } catch (error) {
                showToast(error.message || t("common.error"));
            }
            return;
        }
        if (target.matches("[data-language]")) {
            try {
                await setLanguage(target.dataset.language);
            } catch (error) {
                showToast(error.message || t("common.error"));
            }
            return;
        }
        if (target.id === "generate-language-button") {
            try {
                await generateLanguage();
            } catch (error) {
                showToast(error.message || t("common.error"));
            }
            return;
        }
        if (target.matches("[data-assistant-prompt]")) {
            const input = $("chat-input");
            if (input) {
                input.value = t(target.dataset.assistantPrompt);
                input.focus();
            }
            return;
        }
        if (target.dataset.action === "delete-meal" || target.dataset.action === "delete-item") {
            try {
                await handleHistoryDelete(target.dataset.action, target.dataset);
            } catch (error) {
                showToast(error.message || t("common.error"));
            }
        }
    });

    $("save-limit-button").addEventListener("click", async () => {
        try {
            await saveLimit();
        } catch (error) {
            showToast(error.message || t("common.error"));
        }
    });

    $("clear-limit-button").addEventListener("click", async () => {
        $("daily-limit-input").value = "";
        try {
            await saveLimit();
        } catch (error) {
            showToast(error.message || t("common.error"));
        }
    });

    $("save-assistant-name-button").addEventListener("click", async () => {
        try {
            await saveAssistantName();
        } catch (error) {
            showToast(error.message || t("common.error"));
        }
    });

    $("legacy-import-button").addEventListener("click", async () => {
        const button = $("legacy-import-button");
        button.disabled = true;
        try {
            await importLegacyStorageArchive();
        } catch (error) {
            showToast(error.message || t("common.error"));
        } finally {
            button.disabled = false;
        }
    });
}

document.addEventListener("DOMContentLoaded", async () => {
    const form = $("chat-form");
    if (!form) {
        return;
    }

    bindEvents();
    initializeSectionState();
    applyStaticTranslations();
    updateToolUI();
    updatePendingSave(null);
    renderLastResponse("");
    renderSectionVisibility();
    $("import-content").classList.add("hidden");

    try {
        await refreshBootstrap();
    } catch (error) {
        showToast(error.message || t("common.error"));
    }
});

window.addEventListener("resize", syncSectionStateWithViewport);
window.addEventListener("pageshow", () => refreshWhenPageReturns());
window.addEventListener("focus", () => refreshWhenPageReturns());
window.addEventListener("online", () => refreshBootstrap({ force: true }).catch(() => {}));
document.addEventListener("visibilitychange", () => refreshWhenPageReturns());
