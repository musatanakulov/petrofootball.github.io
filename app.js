// Адрес бэкенда берётся из <meta name="api-origin"> в index.html.
// Пусто = тот же адрес, с которого открыта страница (когда фронтенд отдаёт сам FastAPI).
const API_ORIGIN = ((document.querySelector('meta[name="api-origin"]') || {}).content || '').trim().replace(/\/+$/, '')
    || (location.protocol === 'file:' ? 'http://127.0.0.1:8000' : '');
const API_URL = `${API_ORIGIN}/api`;

const PLACEHOLDER_IMG = 'data:image/svg+xml;utf8,' + encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><rect x="6" y="6" width="68" height="68" rx="14" fill="#6b7aa8"/><text x="40" y="52" font-size="36" text-anchor="middle">⚽</text></svg>'
);

// Превращает относительный путь /static/... в полный адрес бэкенда; пустые и мёртвые заглушки — в локальную картинку
function mediaUrl(url) {
    if (!url || url.includes('via.placeholder.com')) return PLACEHOLDER_IMG;
    if (/^(https?:|data:)/.test(url)) return url;
    return API_ORIGIN + url;
}

let leaguesData = [];
let currentTab = 'matches';
let currentLeagueId = 1;
let currentRound = '';
let IS_ADMIN = false;

function tgInitData() {
    return (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) || '';
}

// Подписанные Telegram данные — сервер проверяет их и решает, админ ли это
function adminHeaders(extra = {}) {
    return { ...extra, 'X-Telegram-Init-Data': tgInitData() };
}

async function checkAdmin() {
    try {
        const res = await fetch(`${API_URL}/auth/check`, { headers: adminHeaders() });
        IS_ADMIN = res.ok && (await res.json()).is_admin === true;
    } catch (e) {
        IS_ADMIN = false;
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.ready();
        window.Telegram.WebApp.expand();
    }
    await Promise.all([fetchLeagues(), checkAdmin(), loadMe()]);
    renderSubtabs();
    loadCurrentTabContent();

    if (isAdmin()) {
        const adminPanel = document.getElementById('admin-panel');
        if (adminPanel) adminPanel.style.display = 'block';
        fillRosterLeagues();
    }
});

async function fetchLeagues() {
    try {
        const res = await fetch(`${API_URL}/leagues`);
        if (!res.ok) throw new Error("Сетевая ошибка");
        leaguesData = await res.json();
        // Выбранную лигу не сбрасываем при повторной загрузке (после сохранения результата и т.п.)
        if (leaguesData.length > 0 && !leaguesData.some(l => l.id === currentLeagueId)) {
            currentLeagueId = leaguesData[0].id;
        }
    } catch (e) {
        console.error("Ошибка загрузки лиг:", e);
    }
}

function renderSubtabs() {
    const container = document.getElementById('league-subtabs');
    if (!leaguesData.length) { container.innerHTML = ''; return; }

    container.innerHTML = leaguesData.map(l => `
        <button class="subtab-btn ${l.id === currentLeagueId ? 'active' : ''}" onclick="selectLeague(${l.id})">
            ${l.name}
        </button>
    `).join('');
}

function selectLeague(leagueId) {
    currentLeagueId = leagueId;
    currentRound = ''; 
    renderSubtabs();
    loadCurrentTabContent();
}

function switchTab(tabName) {
    currentTab = tabName;
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    const tabMap = { 'matches': 0, 'results': 1, 'standings': 2, 'stats': 3, 'vote': 4 };
    document.querySelectorAll('.tab-btn')[tabMap[tabName]].classList.add('active');
    // «Статистика» показывает содержимое выбранного подпункта (бомбардиры или дисквалификации)
    document.getElementById(`${tabName === 'stats' ? currentStatsView : tabName}-tab`).classList.add('active');

    const roundsSub = document.getElementById('rounds-subtabs');
    if (roundsSub) roundsSub.style.display = (tabName === 'results') ? 'flex' : 'none';
    const statsSub = document.getElementById('stats-subtabs');
    if (statsSub) statsSub.style.display = (tabName === 'stats') ? 'flex' : 'none';
    if (tabName === 'stats') renderStatsSubtabs();

    currentRound = '';
    loadCurrentTabContent();
}

// --- СТАТИСТИКА: подпункты «Бомбардиры» и «Дисквалификации» ---
const STATS_VIEWS = { scorers: 'Бомбардиры', disq: 'Дисквалификации' };
let currentStatsView = 'scorers';

function renderStatsSubtabs() {
    document.getElementById('stats-subtabs').innerHTML = Object.entries(STATS_VIEWS).map(([key, label]) => `
        <button class="subtab-btn ${key === currentStatsView ? 'active' : ''}" onclick="selectStatsView('${key}')">${label}</button>
    `).join('');
}

function selectStatsView(view) {
    currentStatsView = view;
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    document.getElementById(`${view}-tab`).classList.add('active');
    renderStatsSubtabs();
    loadCurrentTabContent();
}

function loadCurrentTabContent() {
    if (currentTab === 'matches') fetchMatches(currentLeagueId);
    else if (currentTab === 'results') fetchResults(currentLeagueId);
    else if (currentTab === 'standings') fetchStandings(currentLeagueId);
    else if (currentTab === 'stats') {
        if (currentStatsView === 'scorers') fetchScorers(currentLeagueId);
        else fetchDisqualifications(currentLeagueId);
    }
    else if (currentTab === 'vote') fetchVote(currentLeagueId);
    if (currentTab !== 'vote') stopVoteTimer();
}

// Ошибки FastAPI бывают строкой или списком объектов (ошибки валидации) — превращаем в читаемый текст
function formatApiError(data, fallback) {
    const d = data && data.detail;
    if (!d) return fallback;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(e => `${(e.loc || []).slice(1).join(' → ')}: ${e.msg}`).join('\n');
    return JSON.stringify(d);
}

function isAdmin() {
    return IS_ADMIN;
}

// Номер тура из названия («Тур 12» -> 12)
function roundNo(name) {
    const m = String(name || '').match(/\d+/);
    return m ? parseInt(m[0], 10) : 1e6;
}

// Подпись даты/времени матча: «сб, 21 ноября · 15:00» или «дата уточняется»
function whenLabel(m) {
    if (!m || m.date_tbd) return 'дата уточняется';
    return `${m.weekday ? m.weekday + ', ' : ''}${m.date}${m.time ? ' · ' + m.time : ''}`;
}

// --- РАСПИСАНИЕ (Только ближайший предстоящий тур) ---
async function fetchMatches(leagueId) {
    try {
        const res = await fetch(`${API_URL}/matches/upcoming`);
        const matches = await res.json();
        const leagueObj = leaguesData.find(l => l.id === leagueId);
        const filtered = matches.filter(m => leagueObj && m.league === leagueObj.name);

        const container = document.getElementById('matches-container');
        if (!filtered.length) { 
            container.innerHTML = '<p class="no-data">Все матчи сыграны или расписание не загружено</p>'; 
            return; 
        }

        // Ближайший тур — с наименьшим номером среди ещё не сыгранных; матчи внутри — по времени (без времени — в конце)
        const rounds = [...new Set(filtered.map(m => m.round || 'Тур 1'))].sort((a, b) => roundNo(a) - roundNo(b));
        const targetRound = rounds[0];
        const roundMatches = filtered.filter(m => (m.round || 'Тур 1') === targetRound)
            .sort((a, b) => (a.starts_at || '9999').localeCompare(b.starts_at || '9999'));
        const allTbd = roundMatches.every(m => m.date_tbd);

        const myCard = await myTeamCard();

        container.innerHTML = myCard + `
            <div style="text-align: center; margin-bottom: 12px; font-weight: bold; color: var(--heading); font-size: 15px;">
                📌 Ближайший тур: ${esc(targetRound)}
            </div>
            ${isAdmin() ? '<button class="sanction-btn add-top" onclick="openScheduleModal()">📅 Назначить дату и время</button>' : ''}
            ${allTbd ? '<p class="vote-note" style="text-align: center;">Дата и время матчей этого тура пока не назначены. Расписание на неделю публикуется во вторник.</p>' : ''}
        ` + roundMatches.map(m => `
            <div class="match-card">
                <div class="match-team" onclick="openTeamProfile(${m.home_team_id})">
                    <img loading="lazy" decoding="async" src="${mediaUrl(m.home_logo)}">
                    <span style="font-size: 12px; font-weight: 700;">${m.home_team}</span>
                </div>
                <div style="text-align: center;">
                    ${m.date_tbd
                        ? '<span class="match-score" style="background: var(--surface-3); color: var(--text-2); font-size: 11px; padding: 6px 8px; box-shadow: none;">время<br>уточняется</span>'
                        : `<span class="match-score" style="background: var(--surface-3); color: var(--heading); font-size: 14px; padding: 4px 10px;">${esc(m.time)}</span><br>
                           <span class="match-time" style="font-size: 11px; color: var(--text-2);">${esc(m.weekday ? m.weekday + ', ' : '')}${esc(m.date)}</span>`}
                </div>
                <div class="match-team away" onclick="openTeamProfile(${m.away_team_id})">
                    <span style="font-size: 12px; font-weight: 700;">${m.away_team}</span>
                    <img loading="lazy" decoding="async" src="${mediaUrl(m.away_logo)}">
                </div>
            </div>
        `).join('');
    } catch (e) { console.error(e); }
}

// --- РЕЗУЛЬТАТЫ (Все матчи по турам из Excel графика) ---
async function fetchResults(leagueId) {
    try {
        const res = await fetch(`${API_URL}/matches`);
        const matches = await res.json();
        const leagueObj = leaguesData.find(l => l.id === leagueId);
        const filtered = matches.filter(m => leagueObj && m.league === leagueObj.name);

        const container = document.getElementById('results-container');
        const roundsSubtabContainer = document.getElementById('rounds-subtabs');

        if (!filtered.length) {
            roundsSubtabContainer.innerHTML = '';
            container.innerHTML = '<p class="no-data">График матчей не загружен</p>';
            return;
        }

        const rounds = [...new Set(filtered.map(m => m.round || 'Тур 1'))];
        if (!currentRound || !rounds.includes(currentRound)) {
            currentRound = rounds[0];
        }

        roundsSubtabContainer.innerHTML = rounds.map(r => `
            <button class="subtab-btn ${r === currentRound ? 'active' : ''}" onclick="selectRound('${r}')">${r}</button>
        `).join('');

        const roundMatches = filtered.filter(m => (m.round || 'Тур 1') === currentRound);

        // Админ: тур с перенесённым матчем можно закрыть для голосования «Игрок тура»
        let adminBar = '';
        if (isAdmin()) {
            try {
                const info = (await (await fetch(`${API_URL}/leagues/${leagueId}/rounds`)).json()).find(r => r.round === currentRound);
                if (info && info.finished < info.total) {
                    adminBar = info.closed
                        ? `<button class="sanction-btn" style="margin: 0 0 10px;" onclick="toggleRoundClosure(false)">🔓 Открыть тур снова (голосование отменено)</button>`
                        : `<button class="sanction-btn" style="margin: 0 0 10px;" onclick="toggleRoundClosure(true)">🔒 Закрыть тур для голосования (не сыграно матчей: ${info.total - info.finished})</button>`;
                }
            } catch (e) { console.error(e); }
        }

        container.innerHTML = adminBar + roundMatches.map(m => `
            <div class="match-card ${m.annulled ? 'annulled' : ''}" ${(isAdmin() && !m.annulled) ? `onclick="openResultModal(${m.id})" style="cursor:pointer;" title="Ввести результат"` : ''}>
                <div class="match-team" onclick="event.stopPropagation(); openTeamProfile(${m.home_team_id})">
                    <img loading="lazy" decoding="async" src="${mediaUrl(m.home_logo)}">
                    <span style="font-size: 12px; font-weight: 700;">${m.home_team}</span>
                </div>
                <div style="text-align: center;">
                    <span class="match-score" style="background: ${m.status === 'finished' ? 'var(--accent)' : 'var(--border)'}; color: ${m.status === 'finished' ? 'var(--gold)' : 'var(--text-2)'};">
                        ${m.status === 'finished' ? `${m.home_score} : ${m.away_score}` : '- : -'}
                    </span><br>
                    <span class="match-time" style="font-size: 11px;">${m.annulled ? 'Аннулирован' : (m.status === 'finished' ? (m.technical ? 'Технический результат' : (m.man_of_the_match ? `⭐ ${m.man_of_the_match}` : 'Сыграно')) : (m.date_tbd ? (isAdmin() ? 'Нажмите для ввода счета' : 'Не сыгран') : esc(whenLabel(m))))}</span>
                </div>
                <div class="match-team away" onclick="event.stopPropagation(); openTeamProfile(${m.away_team_id})">
                    <span style="font-size: 12px; font-weight: 700;">${m.away_team}</span>
                    <img loading="lazy" decoding="async" src="${mediaUrl(m.away_logo)}">
                </div>
            </div>
        `).join('');
    } catch (e) { console.error(e); }
}

async function toggleRoundClosure(close) {
    if (!isAdmin()) return;
    const msg = close
        ? `Закрыть «${currentRound}» для голосования?\n\nГолосование за «Игрока тура» пойдёт по уже сыгранным матчам (с ближайшего понедельника), несыгранные матчи ждать не будем.`
        : `Открыть «${currentRound}» снова? Голосование начнётся только когда будут сыграны все матчи тура.`;
    if (!confirm(msg)) return;
    try {
        const res = await fetch(`${API_URL}/leagues/${currentLeagueId}/rounds/${encodeURIComponent(currentRound)}/close`, {
            method: close ? 'POST' : 'DELETE', headers: adminHeaders()
        });
        if (!res.ok) { alert(formatApiError(await res.json().catch(() => ({})), 'Не удалось изменить')); return; }
        fetchResults(currentLeagueId);
    } catch (e) { console.error(e); alert('Сетевая ошибка. Попробуйте ещё раз.'); }
}

function selectRound(roundName) {
    currentRound = roundName;
    fetchResults(currentLeagueId);
}

// --- НАЗНАЧЕНИЕ ДАТЫ И ВРЕМЕНИ ТУРА (админ; расписание на неделю обычно назначается во вторник) ---
let scheduleCache = [];

function ensureModal(id, zIndex) {
    let modal = document.getElementById(id);
    if (!modal) {
        modal = document.createElement('div');
        modal.id = id;
        modal.style.cssText = `position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: var(--overlay); display: flex; justify-content: center; align-items: center; z-index: ${zIndex}; padding: 16px; box-sizing: border-box;`;
        document.body.appendChild(modal);
    }
    modal.style.display = 'flex';
    return modal;
}

async function openScheduleModal(preRound) {
    if (!isAdmin()) return;
    try {
        const res = await fetch(`${API_URL}/matches`);
        const all = await res.json();
        const league = leaguesData.find(l => l.id === currentLeagueId);
        scheduleCache = all.filter(m => league && m.league === league.name && m.status === 'scheduled' && !m.annulled);
        const rounds = [...new Set(scheduleCache.map(m => m.round))].sort((a, b) => roundNo(a) - roundNo(b));
        if (!rounds.length) { alert('В этой лиге нет несыгранных матчей.'); return; }
        const round = rounds.includes(preRound) ? preRound : rounds[0];

        const modal = ensureModal('schedule-modal', 1300);
        modal.innerHTML = `
            <div style="background: var(--surface-solid); width: 100%; max-width: 460px; border-radius: 16px; padding: 20px; max-height: 90vh; overflow-y: auto;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <h3 style="margin: 0; color: var(--heading);">Дата и время матчей</h3>
                    <button onclick="document.getElementById('schedule-modal').style.display='none'" style="background: none; border: none; font-size: 20px; cursor: pointer;">✕</button>
                </div>
                <label class="sanction-label" style="margin-top: 0;">Тур</label>
                <select id="schedule-round" class="sanction-input" onchange="renderScheduleRows(this.value)">
                    ${rounds.map(r => `<option value="${esc(r)}" ${r === round ? 'selected' : ''}>${esc(r)}</option>`).join('')}
                </select>
                <div style="display: flex; gap: 8px; align-items: center; margin: 8px 0;">
                    <input type="date" id="schedule-bulk-date" class="sanction-input" style="margin: 0; flex: 1;" title="Дата для всех матчей тура">
                    <button class="sanction-btn" style="margin: 0;" onclick="applyBulkDate()">Дату всем</button>
                </div>
                <div id="schedule-rows"></div>
                <div id="schedule-message" style="margin: 10px 0 0; font-size: 12px; font-weight: bold; line-height: 1.5;"></div>
                <button onclick="saveSchedule()" style="width: 100%; margin-top: 10px; background: var(--accent-grad); color: white; border: none; padding: 12px; border-radius: 8px; font-weight: bold; cursor: pointer;">💾 Сохранить расписание</button>
                <p class="sanction-hint" style="margin-top: 8px;">Оставьте дату и время пустыми, чтобы снять назначение. Сыгранные матчи не меняются.</p>
            </div>`;
        renderScheduleRows(round);
    } catch (e) { console.error(e); alert('Не удалось загрузить матчи.'); }
}

function renderScheduleRows(round) {
    const rows = scheduleCache.filter(m => m.round === round).sort((a, b) => a.id - b.id);
    document.getElementById('schedule-message').textContent = '';
    document.getElementById('schedule-rows').innerHTML = rows.map(m => {
        const [d, t] = m.starts_at ? m.starts_at.split('T') : ['', ''];
        return `
        <div class="tile" style="padding: 8px 10px; margin-bottom: 6px;">
            <div style="font-size: 12px; font-weight: 700; margin-bottom: 6px;">${esc(m.home_team)} — ${esc(m.away_team)}</div>
            <div style="display: flex; gap: 8px;">
                <input type="date" id="sch-date-${m.id}" class="sanction-input" style="margin: 0; flex: 1.4;" value="${d}">
                <input type="time" id="sch-time-${m.id}" class="sanction-input" style="margin: 0; flex: 1;" value="${t}">
            </div>
        </div>`;
    }).join('');
}

function applyBulkDate() {
    const d = document.getElementById('schedule-bulk-date').value;
    if (!d) return;
    const round = document.getElementById('schedule-round').value;
    scheduleCache.filter(m => m.round === round).forEach(m => { document.getElementById(`sch-date-${m.id}`).value = d; });
}

async function saveSchedule() {
    const round = document.getElementById('schedule-round').value;
    const msg = document.getElementById('schedule-message');
    const items = scheduleCache.filter(m => m.round === round).map(m => ({
        match_id: m.id,
        match_date: document.getElementById(`sch-date-${m.id}`).value || null,
        match_time: document.getElementById(`sch-time-${m.id}`).value || null
    }));
    try {
        const res = await fetch(`${API_URL}/matches/schedule`, {
            method: 'POST', headers: adminHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ items })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { msg.style.color = 'var(--red)'; msg.textContent = formatApiError(data, 'Не удалось сохранить'); return; }
        fetchMatches(currentLeagueId);
        if (currentTab === 'results') fetchResults(currentLeagueId);
        if (data.warnings && data.warnings.length) {
            msg.style.color = 'var(--amber)';
            msg.innerHTML = '✅ Сохранено. ⚠️ Проверьте время:<br>' + data.warnings.map(w => '• ' + esc(w)).join('<br>');
            // перечитываем матчи, чтобы форма показывала сохранённое
            const fresh = await (await fetch(`${API_URL}/matches`)).json();
            const league = leaguesData.find(l => l.id === currentLeagueId);
            scheduleCache = fresh.filter(m => league && m.league === league.name && m.status === 'scheduled' && !m.annulled);
        } else {
            document.getElementById('schedule-modal').style.display = 'none';
        }
    } catch (e) { console.error(e); msg.style.color = 'var(--red)'; msg.textContent = 'Сетевая ошибка. Попробуйте ещё раз.'; }
}

// --- МОЯ КОМАНДА И НАСТРОЙКИ ---
let myTeamId = Number(safeStorage('myTeam')) || null;
let me = { identified: false, notifications: true, lang: null };

function safeStorage(key, value) {
    try {
        if (value === undefined) return localStorage.getItem(key);
        if (value === null) localStorage.removeItem(key); else localStorage.setItem(key, value);
    } catch (e) { /* хранилище недоступно — работаем без него */ }
    return null;
}

async function loadMe() {
    try {
        const res = await fetch(`${API_URL}/me`, { headers: adminHeaders() });
        if (!res.ok) return;
        me = await res.json();
        if (me.identified) {
            if (me.lang && !safeStorage('lang') && me.lang !== (window.APP_LANG || 'ru')) { safeStorage('lang', me.lang); location.reload(); return; }
            if (me.favorite_team_id) { myTeamId = me.favorite_team_id; safeStorage('myTeam', String(myTeamId)); }
            else if (myTeamId) saveMeSettings({ favorite_team_id: myTeamId });   // выбранное локально переносим на сервер
        }
    } catch (e) { console.error(e); }
}

async function saveMeSettings(body) {
    if (!me.identified) return false;   // вне Telegram настройки живут только в этом браузере
    try {
        const res = await fetch(`${API_URL}/me/settings`, {
            method: 'POST', headers: adminHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify(body)
        });
        if (res.ok) { me = await res.json(); return true; }
    } catch (e) { console.error(e); }
    return false;
}

function setMyTeam(teamId) {
    myTeamId = teamId || null;
    safeStorage('myTeam', myTeamId ? String(myTeamId) : null);
    saveMeSettings(myTeamId ? { favorite_team_id: myTeamId } : { clear_favorite: true });
    loadCurrentTabContent();
}

function toggleMyTeam(teamId) {
    setMyTeam(myTeamId === teamId ? null : teamId);
    openTeamProfile(teamId);   // обновить звезду
}

// Карточка «Моя команда» над расписанием: ближайший несыгранный матч
async function myTeamCard() {
    if (!myTeamId) return '';
    try {
        const team = await (await fetch(`${API_URL}/teams/${myTeamId}`)).json();
        const next = (team.matches || []).find(m => m.status === 'scheduled' && !m.annulled);
        const body = next
            ? `Следующий матч: <b>${esc(next.opponent_name)}</b> · ${esc(next.round)} · ${esc(whenLabel(next))}`
            : (team.withdrawn ? 'Команда снята с турнира' : 'Несыгранных матчей нет');
        return `<div class="my-team-card" onclick="openTeamProfile(${team.id})">
            <img loading="lazy" src="${mediaUrl(team.logo_url)}" width="34" height="34" style="object-fit: contain;">
            <div><div class="my-team-title">⭐ ${esc(team.name)}</div><div class="my-team-sub">${body}</div></div></div>`;
    } catch (e) { console.error(e); return ''; }
}

function openSettingsModal() {
    const modal = ensureModal('settings-modal', 1450);
    const theme = safeStorage('theme') || 'auto';
    const seg = (name, current, options) => options.map(([v, label]) =>
        `<button class="seg-btn ${current === v ? 'active' : ''}" onclick="setSetting('${name}', '${v}')">${label}</button>`).join('');
    const teamOptions = leaguesData.map(l => `
        <optgroup label="${esc(l.name)}">${l.teams.slice().sort((a, b) => a.name.localeCompare(b.name, 'ru'))
            .map(t => `<option value="${t.id}" ${t.id === myTeamId ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</optgroup>`).join('');
    modal.innerHTML = `
        <div style="background: var(--surface-solid); width: 100%; max-width: 440px; border-radius: 16px; padding: 20px; max-height: 90vh; overflow-y: auto;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <h3 style="margin: 0; color: var(--heading);">⚙️ Настройки</h3>
                <button onclick="document.getElementById('settings-modal').style.display='none'" style="background: none; border: none; font-size: 20px; cursor: pointer;">✕</button>
            </div>
            <label class="sanction-label" style="margin-top: 0;">Язык / Тіл</label>
            <div class="seg">${seg('lang', window.APP_LANG || 'ru', [['ru', 'Русский'], ['kk', 'Қазақша']])}</div>

            <label class="sanction-label">Тема</label>
            <div class="seg">${seg('theme', theme, [['auto', 'Авто'], ['light', 'Светлая'], ['dark', 'Тёмная']])}</div>

            <label class="sanction-label">⭐ Моя команда</label>
            <select id="my-team-select" class="sanction-input" onchange="setMyTeam(Number(this.value))">
                <option value="0">— не выбрана —</option>${teamOptions}
            </select>
            <div class="sanction-hint">Её ближайший матч показывается в «Расписании», в таблице она подсвечена${me.identified ? '. Напоминания о её матчах придут в Telegram.' : '.'}</div>

            <label class="sanction-label">🔔 Уведомления в Telegram</label>
            <label class="switch-row">
                <input type="checkbox" id="notify-toggle" ${me.notifications ? 'checked' : ''} ${me.identified ? '' : 'disabled'} onchange="setSetting('notifications', this.checked)">
                <span>Напоминания о матчах моей команды и об открытии голосования</span>
            </label>
            ${me.identified ? '' : '<div class="sanction-hint">Доступно при входе из Telegram.</div>'}
        </div>`;
}

function setSetting(name, value) {
    if (name === 'theme') {
        safeStorage('theme', value === 'auto' ? null : value);
        if (window.applyTheme) window.applyTheme();
        openSettingsModal();
    } else if (name === 'notifications') {
        saveMeSettings({ notifications: !!value });
    } else if (name === 'lang') {
        safeStorage('lang', value);
        saveMeSettings({ lang: value }).finally(() => location.reload());   // страница перерисуется на выбранном языке
    }
}

// Внешние ссылки: внутри Telegram открываем средствами Mini App (t.me — в самом Telegram, остальное — в браузере),
// вне Telegram — обычной новой вкладкой
function openExternalLink(event, url) {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.initData) {
        event.preventDefault();
        if (/^https:\/\/t\.me\//.test(url) && tg.openTelegramLink) tg.openTelegramLink(url);
        else if (tg.openLink) tg.openLink(url);
        else window.open(url, '_blank');
        return false;
    }
    event.preventDefault();
    window.open(url, '_blank', 'noopener');
    return false;
}

// --- О ТУРНИРЕ (краткая выжимка регламента) ---
function openAboutModal() {
    const modal = ensureModal('about-modal', 1400);
    const sec = (title, body, open = false) => `<details class="about-sec" ${open ? 'open' : ''}><summary>${title}</summary><div class="about-body">${body}</div></details>`;
    modal.innerHTML = `
        <div style="background: var(--surface-solid); width: 100%; max-width: 480px; border-radius: 16px; padding: 20px; max-height: 90vh; overflow-y: auto;">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
                <div>
                    <h3 style="margin: 0; color: var(--heading);">О турнире</h3>
                    <div style="font-size: 12px; color: var(--text-2); margin-top: 2px;">Кубок Акима города Петропавловск по футзалу среди любительских команд · 2026–2027</div>
                </div>
                <button onclick="document.getElementById('about-modal').style.display='none'" style="background: none; border: none; font-size: 20px; cursor: pointer;">✕</button>
            </div>
            ${sec('📅 Даты и место', `<p>Старт турнира: <b>21–22 ноября 2026</b>.</p>
                <p>Место: Дворец спорта имени Александра Винокурова, спортивный зал ТОО «Қызылжар Су».</p>
                <p>Организаторы: Акимат города Петропавловск совместно с Федерацией футбола Северо-Казахстанской области.</p>
                <p>Дата и время матчей каждого тура назначаются на неделю Оргкомитетом.</p>`, true)}
            ${sec('🏆 Формат', `<p>Три лиги, система «каждый с каждым»: <b>I лига — 14</b>, <b>II лига — 14</b>, <b>III лига — 10</b> команд.</p>
                <p>Матч — 40 минут: 2 тайма по 20 минут, перерыв 5 минут, время не останавливается. В каждом тайме у команды один тайм-аут.</p>`)}
            ${sec('📊 Очки и места', `<p>Победа — 3 очка, ничья — 1, поражение — 0.</p>
                <p>При равенстве очков места определяются по порядку:</p>
                <ol><li>разница забитых и пропущенных мячей</li><li>количество побед</li><li>количество забитых мячей</li><li>личные встречи</li><li>меньше удалений</li><li>меньше предупреждений</li></ol>
                <p>Если и это поровну за первое место, играется дополнительный матч, затем дополнительное время (2×10 минут) и 6-метровые удары.</p>`)}
            ${sec('🟨🟥 Карточки и дисквалификации', `<ul>
                <li>Две жёлтые в одном матче — удаление и пропуск следующего матча.</li>
                <li>Три жёлтые в разных матчах — пропуск следующего матча.</li>
                <li>Красная карточка — пропуск одного матча.</li>
                <li>Оргкомитет вправе продлить или изменить срок дисквалификации.</li></ul>
                <p>Участие незаявленного или дисквалифицированного игрока — техническое поражение 0:5.</p>`)}
            ${sec('🔁 Переходы между лигами', `<ul>
                <li>I лига: 13–14 места → во II лигу</li>
                <li>II лига: 1–2 места → в I лигу; 13–14 места → в III лигу</li>
                <li>III лига: 1–2 места → во II лигу</li></ul>`)}
            ${sec('⚠️ Технические результаты и снятие команды', `<ul>
                <li>Неявка — поражение 0:5, при повторной — снятие с турнира.</li>
                <li>Самовольный уход с площадки или отказ продолжать матч — снятие с турнира, сопернику 5:0.</li>
                <li>Снялась, сыграв <b>менее 50%</b> матчей — все её результаты, голы и карточки аннулируются.</li>
                <li>Снялась, сыграв <b>50% и более</b> — сыгранные результаты сохраняются, в оставшихся матчах ей засчитывается 0:5.</li></ul>`)}
            ${sec('👥 Заявка и допуск', `<ul>
                <li>Игроки не моложе 16 лет (несовершеннолетние — с нотариальным согласием родителей).</li>
                <li>В заявке команды не более 25 игроков, в заявке на матч — не более 20.</li>
                <li>Протокол матча заполняется капитанами не позднее чем за 10 минут до начала.</li></ul>`)}
            ${sec('📝 Протесты', `<p>Подаются в течение 15 минут после окончания матча. Протесты на судейские решения по игровым эпизодам не принимаются.</p>`)}
            ${sec('🥇 Награждение', `<p>I и II лиги: команды на 1–3 местах получают кубки, медали, дипломы и денежные призы. III лига: кубки, медали и дипломы.</p>`)}
            <div class="about-social">
                <div class="about-social-title">Официальные соцсети</div>
                <div class="about-social-row">
                    <a class="btn btn-ghost" href="https://t.me/footballpetropavlovsk" onclick="return openExternalLink(event, this.href)">✈️ Telegram</a>
                    <a class="btn btn-ghost" href="https://instagram.com/footballpetropavlovsk" onclick="return openExternalLink(event, this.href)">📷 Instagram</a>
                </div>
            </div>
            <p class="sanction-hint" style="margin-top: 12px;">Краткая выжимка регламента. Полный текст — у Оргкомитета.</p>
        </div>`;
}

// --- ТУРНИРНАЯ ТАБЛИЦА ---
async function fetchStandings(leagueId) {
    try {
        const res = await fetch(`${API_URL}/leagues/${leagueId}/standings`);
        const data = await res.json();
        const container = document.getElementById('standings-container');
        if (!data.length) { container.innerHTML = '<p class="no-data">Нет данных таблицы</p>'; return; }

        // Зоны переходов между лигами (регламент, ст. 3.6). Пока турнир не начался, порядок алфавитный — зоны не показываем.
        const started = data.some(r => r.played > 0);
        const zones = started ? leagueZones(leagueId, data.length) : { up: [], down: [] };
        const zoneClass = pos => zones.up.includes(pos) ? 'zone-up' : (zones.down.includes(pos) ? 'zone-down' : '');
        const legend = [
            zones.up.length ? '<span><i class="zone-dot up"></i> Переход в лигу выше</span>' : '',
            zones.down.length ? '<span><i class="zone-dot down"></i> Переход в лигу ниже</span>' : ''
        ].join('');

        container.innerHTML = `
            <div class="league-section">
                <h3>Турнирная таблица</h3>
                <div class="table-responsive">
                    <table>
                        <tr>
                            <th>М</th>
                            <th style="text-align: left;">Команда</th>
                            <th>И</th>
                            <th>В</th>
                            <th>Н</th>
                            <th>П</th>
                            <th>ЗМ</th>
                            <th>ПМ</th>
                            <th>РМ</th>
                            <th>О</th>
                        </tr>
                        ${data.map((row, index) => `
                            <tr class="${zoneClass(index + 1)} ${row.team_id === myTeamId ? "my-team" : ""}">
                                <td><b>${index + 1}</b></td>
                                <td style="text-align: left; cursor: pointer; color: var(--heading); font-weight: 600;" onclick="openTeamProfile(${row.team_id})">
                                    <img loading="lazy" decoding="async" src="${mediaUrl(row.logo_url)}" width="22" height="22" style="object-fit: contain; vertical-align: middle; margin-right: 6px;">
                                    ${row.team_id === myTeamId ? '⭐ ' : ''}${row.team_name}${row.withdrawn ? '<span class="badge-withdrawn">снята</span>' : ''}
                                </td>
                                <td>${row.played}</td>
                                <td>${row.wins}</td>
                                <td>${row.draws}</td>
                                <td>${row.losses}</td>
                                <td>${row.goals_for}</td>
                                <td>${row.goals_against}</td>
                                <td>${row.goal_difference > 0 ? '+' + row.goal_difference : row.goal_difference}</td>
                                <td><b style="color: var(--heading);">${row.points}</b></td>
                            </tr>
                        `).join('')}
                    </table>
                </div>
                ${legend ? `<div class="zone-legend">${legend}</div>` : ''}
            </div>
        `;
    } catch (e) { console.error(e); }
}

// Регламент 3.6: I лига — 13–14 места вниз; II лига — 1–2 вверх, 13–14 вниз; III лига — 1–2 вверх
function leagueZones(leagueId, teamsCount) {
    const league = leaguesData.find(l => l.id === leagueId);
    const name = ((league && league.name) || '').toLowerCase();
    const bottom = teamsCount >= 6 ? [teamsCount - 1, teamsCount] : [];
    if (name.startsWith('перв')) return { up: [], down: bottom };
    if (name.startsWith('втор')) return { up: [1, 2], down: bottom };
    if (name.startsWith('трет')) return { up: [1, 2], down: [] };
    return { up: [], down: [] };
}

// --- БОМБАРДИРЫ ---
async function fetchScorers(leagueId) {
    try {
        const res = await fetch(`${API_URL}/leagues/${leagueId}/top-scorers`);
        const data = await res.json();
        const container = document.getElementById('scorers-container');
        if (!data.length) { container.innerHTML = '<p class="no-data">Голы еще не забиты</p>'; return; }

        container.innerHTML = `
            <div class="league-section">
                <h3>Топ-бомбардиры</h3>
                <div style="display: flex; flex-direction: column; gap: 6px;">
                    ${data.map((p, idx) => `
                        <div onclick="openPlayerProfile(${p.team_id}, ${p.player_id})" style="display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; background: var(--surface-2); border-radius: 8px; border: 1px solid var(--border); font-size: 14px; cursor: pointer;">
                            <span><b>${idx + 1}.</b> ${p.player_name} <span style="color: var(--text-2); font-size: 12px;">(${p.team_name})</span></span>
                            <span style="font-weight: 800; color: var(--amber); background: var(--amber-soft); padding: 2px 8px; border-radius: 6px;">
                                ⚽ ${p.goals} ${p.own_goals > 0 ? `<span style="color:var(--red);">(-${p.own_goals})</span>` : ''}
                            </span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    } catch (e) { console.error(e); }
}

// --- ДИСКВАЛИФИКАЦИИ ---
let lastDisqData = [];

function renderSanctionRows(item) {
    if (!item.sanctions.length) {
        return '<div class="sanction-row pending">Причина удаления и срок дисквалификации уточняются</div>';
    }
    return item.sanctions.map(s => `
        <div class="sanction-row">
            ${s.reason ? `<div><b>Причина удаления:</b> ${esc(s.reason)}${s.auto ? ' <span class="sanction-auto">авто</span>' : ''}</div>` : ''}
            <div><b>Срок дисквалификации:</b> ${s.term_text ? esc(s.term_text) : '<i>уточняется</i>'}</div>
            ${isAdmin() ? `<div class="sanction-actions">
                <button class="sanction-btn" data-act="edit" data-sanction="${s.id}">✏ Изменить</button>
                <button class="sanction-btn danger" data-act="delete" data-sanction="${s.id}">🗑 Удалить</button>
            </div>` : ''}
        </div>`).join('');
}

async function fetchDisqualifications(leagueId) {
    try {
        const res = await fetch(`${API_URL}/leagues/${leagueId}/disqualification`);
        const data = await res.json();
        const container = document.getElementById('disq-container');
        lastDisqData = data;

        container.innerHTML = `
            <div class="league-section">
                <h3>Дисквалификации и санкции КДК</h3>
                ${isAdmin() ? '<button class="sanction-btn add-top" data-act="add-any">➕ Добавить санкцию игроку</button>' : ''}
                ${!data.length ? '<p class="no-data">Нет дисквалифицированных игроков</p>' : `
                <div style="display: flex; flex-direction: column; gap: 8px;">
                    ${data.map(item => `
                        <div class="disq-item">
                            <strong class="disq-player" data-act="profile" data-team="${item.team_id}" data-player="${item.player_id}">${esc(item.player_name)}</strong>
                            <span style="color: var(--text-2);">(${esc(item.team_name)})</span>
                            ${item.reason ? `<br><span style="color: var(--red); font-weight: 700;">Основание: ${esc(item.reason)}</span>` : ''}
                            <br><span style="font-size: 11px; color: var(--text-2);">Карточек: Желтых — ${item.yellow_cards}, Красных — ${item.red_cards}</span>
                            ${renderSanctionRows(item)}
                            ${isAdmin() ? `<button class="sanction-btn" data-act="add" data-player="${item.player_id}">➕ Указать причину и срок</button>` : ''}
                        </div>
                    `).join('')}
                </div>`}
            </div>
        `;
    } catch (e) { console.error(e); }
}

// --- САНКЦИИ КДК: админ вносит причину удаления и срок дисквалификации ---
let sanctionDraft = { playerId: null, sanctionId: null };

function findSanction(sanctionId) {
    for (const item of lastDisqData) {
        const s = item.sanctions.find(x => x.id === sanctionId);
        if (s) return { sanction: s, item };
    }
    return null;
}

function openSanctionModal({ playerId = null, sanctionId = null } = {}) {
    if (!isAdmin()) return;
    const found = sanctionId !== null ? findSanction(sanctionId) : null;
    if (sanctionId !== null && !found) return;
    const current = found ? found.sanction : { reason: '', term: '', matches: null, until_date: null };
    sanctionDraft = { playerId: found ? found.item.player_id : playerId, sanctionId };

    const league = leaguesData.find(l => l.id === currentLeagueId);
    let playerBlock;
    if (sanctionDraft.playerId !== null) {
        const item = lastDisqData.find(i => i.player_id === sanctionDraft.playerId);
        let name = item ? `${item.player_name} (${item.team_name})` : '';
        if (!name && league) {
            for (const t of league.teams) {
                const p = t.players.find(x => x.id === sanctionDraft.playerId);
                if (p) name = `${p.name} (${t.name})`;
            }
        }
        playerBlock = `<div class="sanction-player">${esc(name)}</div>`;
    } else {
        playerBlock = `
            <select id="sanction-team" class="sanction-input" onchange="fillSanctionPlayers()">
                ${league ? league.teams.map(t => `<option value="${t.id}">${esc(t.name)}</option>`).join('') : ''}
            </select>
            <select id="sanction-player" class="sanction-input"></select>`;
    }

    let modal = document.getElementById('sanction-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'sanction-modal';
        modal.style.cssText = 'position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: var(--overlay); display: flex; justify-content: center; align-items: center; z-index: 1300; padding: 16px; box-sizing: border-box;';
        document.body.appendChild(modal);
    }
    modal.innerHTML = `
        <div style="background: var(--surface-solid); width: 100%; max-width: 420px; border-radius: 16px; padding: 20px; max-height: 90vh; overflow-y: auto;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <h3 style="margin: 0; color: var(--heading);">${sanctionId !== null ? 'Изменить санкцию' : 'Санкция КДК'}</h3>
                <button onclick="closeSanctionModal()" style="background: none; border: none; font-size: 20px; cursor: pointer;">✕</button>
            </div>
            ${playerBlock}
            <label class="sanction-label">Причина удаления</label>
            <textarea id="sanction-reason" class="sanction-input" rows="3" maxlength="500" placeholder="Например: грубая игра, оскорбление арбитра">${esc(current.reason)}</textarea>
            <label class="sanction-label">Срок дисквалификации</label>
            <div style="display: flex; gap: 8px;">
                <div style="flex: 1;">
                    <input id="sanction-matches" type="number" min="1" max="99" class="sanction-input" placeholder="Матчей" value="${current.matches || ''}">
                </div>
                <div style="flex: 1.4;">
                    <input id="sanction-until" type="date" class="sanction-input" title="Срок до даты (включительно)" value="${current.until_date || ''}">
                </div>
            </div>
            <div class="sanction-hint">Укажите либо число матчей, либо дату «до». Когда срок пройдёт, игрок сам исчезнет из списка.</div>
            <input id="sanction-term" class="sanction-input" maxlength="100" placeholder="Или срок текстом (останется, пока вы не удалите)" value="${esc(current.term)}">
            <p id="sanction-error" style="color: var(--red); font-size: 12px; font-weight: bold; margin: 6px 0 0;"></p>
            <button onclick="saveSanction()" style="width: 100%; margin-top: 10px; background: var(--accent-grad); color: white; border: none; padding: 12px; border-radius: 8px; font-weight: bold; cursor: pointer;">💾 Сохранить</button>
        </div>`;
    modal.style.display = 'flex';
    if (sanctionDraft.playerId === null) fillSanctionPlayers();
}

function fillSanctionPlayers() {
    const league = leaguesData.find(l => l.id === currentLeagueId);
    const team = league && league.teams.find(t => t.id === Number(document.getElementById('sanction-team').value));
    document.getElementById('sanction-player').innerHTML = team && team.players.length
        ? team.players.map(p => `<option value="${p.id}">${p.number}. ${esc(p.name)}</option>`).join('')
        : '<option value="">В команде нет игроков</option>';
}

function closeSanctionModal() {
    const modal = document.getElementById('sanction-modal');
    if (modal) modal.style.display = 'none';
}

async function saveSanction() {
    const reason = document.getElementById('sanction-reason').value.trim();
    const term = document.getElementById('sanction-term').value.trim();
    const matches = Number(document.getElementById('sanction-matches').value) || null;
    const untilDate = document.getElementById('sanction-until').value || null;
    const errorEl = document.getElementById('sanction-error');
    let playerId = sanctionDraft.playerId;
    if (playerId === null) playerId = Number(document.getElementById('sanction-player').value) || null;
    if (playerId === null) { errorEl.textContent = 'Выберите игрока'; return; }
    if (!reason && !term && !matches && !untilDate) { errorEl.textContent = 'Укажите причину удаления или срок дисквалификации'; return; }
    if (matches && untilDate) { errorEl.textContent = 'Срок задаётся либо в матчах, либо датой — не обоими сразу'; return; }

    const editing = sanctionDraft.sanctionId !== null;
    try {
        const res = await fetch(editing ? `${API_URL}/sanctions/${sanctionDraft.sanctionId}` : `${API_URL}/sanctions`, {
            method: editing ? 'PUT' : 'POST',
            headers: adminHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ player_id: playerId, reason, term, matches, until_date: untilDate })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { errorEl.textContent = formatApiError(data, 'Не удалось сохранить'); return; }
        closeSanctionModal();
        fetchDisqualifications(currentLeagueId);
    } catch (e) {
        console.error(e);
        errorEl.textContent = 'Сетевая ошибка. Попробуйте ещё раз.';
    }
}

async function deleteSanction(sanctionId) {
    if (!confirm('Удалить эту санкцию? Игрок останется в списке, если у него есть красная карточка или 3 жёлтых.')) return;
    try {
        const res = await fetch(`${API_URL}/sanctions/${sanctionId}`, { method: 'DELETE', headers: adminHeaders() });
        if (!res.ok) alert(formatApiError(await res.json().catch(() => ({})), 'Не удалось удалить'));
    } catch (e) {
        console.error(e);
        alert('Сетевая ошибка. Попробуйте ещё раз.');
    }
    fetchDisqualifications(currentLeagueId);
}

document.getElementById('disq-container').addEventListener('click', (e) => {
    const el = e.target.closest('[data-act]');
    if (!el) return;
    const act = el.dataset.act;
    if (act === 'profile') openPlayerProfile(Number(el.dataset.team), Number(el.dataset.player));
    else if (act === 'add') openSanctionModal({ playerId: Number(el.dataset.player) });
    else if (act === 'add-any') openSanctionModal();
    else if (act === 'edit') openSanctionModal({ sanctionId: Number(el.dataset.sanction) });
    else if (act === 'delete') deleteSanction(Number(el.dataset.sanction));
});

// --- ГОЛОСОВАНИЕ: ИГРОК ТУРА ---
// Голосование открывается в понедельник после завершения тура и длится 24 часа (решает сервер).
const TOURNAMENT_TZ = 'Asia/Almaty'; // UTC+5, как на сервере
let voteTimer = null;
let voteClockSkew = 0; // поправка на разницу часов устройства и сервера

function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function stopVoteTimer() {
    if (voteTimer) { clearInterval(voteTimer); voteTimer = null; }
}

function formatVoteDate(iso) {
    return new Date(iso).toLocaleDateString(window.APP_LANG === 'kk' ? 'kk-KZ' : 'ru-RU', { weekday: 'long', day: 'numeric', month: 'long', timeZone: TOURNAMENT_TZ });
}

function formatCountdown(ms) {
    const mins = Math.max(0, Math.floor(ms / 60000));
    const h = Math.floor(mins / 60), m = mins % 60;
    return h > 0 ? `${h} ч ${m} мин` : `${m} мин`;
}

function startVoteTimer(leagueId) {
    stopVoteTimer();
    const tick = () => {
        const nowMs = Date.now() + voteClockSkew;
        let needRefresh = false;
        document.querySelectorAll('.vote-countdown').forEach(el => {
            const left = Date.parse(el.dataset.closes) - nowMs;
            if (left <= 0) needRefresh = true; else el.textContent = `До конца: ${formatCountdown(left)}`;
        });
        document.querySelectorAll('.vote-opens').forEach(el => {
            if (Date.parse(el.dataset.opens) - nowMs <= 0) needRefresh = true;
        });
        if (needRefresh) { stopVoteTimer(); fetchVote(leagueId); }
    };
    tick();
    if (voteTimer === null && document.querySelector('.vote-countdown, .vote-opens')) voteTimer = setInterval(tick, 30000);
}

function renderVoteCandidate(p, c) {
    const showBar = p.results_visible;
    const mine = p.my_vote === c.player_id;
    return `
        <div class="vote-candidate ${c.is_winner ? 'winner' : ''} ${mine ? 'mine' : ''}">
            <img loading="lazy" decoding="async" src="${mediaUrl(c.photo_url)}" class="vote-photo" alt="">
            <div class="vote-info">
                <div class="vote-name">${c.is_winner ? '🏆 ' : ''}${esc(c.name)}${mine ? ' <span class="vote-mine">✔ ваш голос</span>' : ''}</div>
                <div class="vote-sub">№ ${esc(c.number)} · ${esc(c.team_name)}</div>
                <div class="vote-sub">Игрок матча: ${esc(c.match)}</div>
                ${showBar ? `
                    <div class="vote-bar"><div class="vote-bar-fill" style="width: ${c.percent}%"></div></div>
                    <div class="vote-sub"><b>${c.votes}</b> гол. (${c.percent}%)</div>` : ''}
            </div>
            ${p.can_vote ? `<button class="vote-btn" data-league="${p.league_id}" data-round="${esc(p.round)}" data-player="${c.player_id}" data-name="${esc(c.name)}">Голосовать</button>` : ''}
        </div>`;
}

function renderPoll(p, canIdentify) {
    const title = `🗳 Игрок тура — ${esc(p.round)}`;
    if (p.status === 'upcoming') {
        return `
            <div class="league-section">
                <h3>${title}</h3>
                <p class="vote-note vote-opens" data-opens="${p.opens_at}">Голосование откроется <b>${esc(formatVoteDate(p.opens_at))}</b> и продлится 24 часа.<br>Кандидатов: ${p.candidates_count}</p>
            </div>`;
    }
    const active = p.status === 'active';
    let note = '';
    if (active && p.can_vote) note = 'Выберите лучшего игрока тура. Голос можно отдать один раз.';
    else if (active && p.my_vote !== null) note = 'Ваш голос принят. Итоги станут окончательными, когда голосование закончится.';
    else if (active && !canIdentify) note = 'Голосовать можно только из Telegram.';
    const totalLine = `Всего голосов: ${p.total_votes}`;
    return `
        <div class="league-section">
            <h3>${title}</h3>
            <div class="vote-head">
                ${active ? `<span class="vote-badge live">Идёт голосование</span><span class="vote-countdown" data-closes="${p.closes_at}"></span>`
                         : `<span class="vote-badge done">Голосование завершено</span>`}
            </div>
            ${note ? `<p class="vote-note">${note}</p>` : ''}
            <div class="vote-list">${p.candidates.map(c => renderVoteCandidate(p, c)).join('')}</div>
            <p class="vote-total">${totalLine}${active && !p.results_visible ? ' · итоги откроются после вашего голоса' : ''}</p>
        </div>`;
}

async function fetchVote(leagueId) {
    const container = document.getElementById('vote-container');
    try {
        const res = await fetch(`${API_URL}/leagues/${leagueId}/polls`, { headers: adminHeaders() });
        if (!res.ok) throw new Error('bad status');
        const data = await res.json();
        if (currentTab !== 'vote' || currentLeagueId !== leagueId) return; // пользователь уже ушёл
        voteClockSkew = Date.parse(data.server_time) - Date.now();

        if (!data.polls.length) {
            stopVoteTimer();
            container.innerHTML = `
                <div class="league-section">
                    <h3>🗳 Игрок тура</h3>
                    <p class="no-data">Голосование появится после завершения тура.<br>Оно открывается в понедельник и длится 24 часа.</p>
                </div>`;
            return;
        }
        container.innerHTML = data.polls.map(p => renderPoll(p, data.can_identify_voter)).join('');
        startVoteTimer(leagueId);
    } catch (e) {
        console.error(e);
        container.innerHTML = '<p class="no-data">Не удалось загрузить голосование</p>';
    }
}

document.getElementById('vote-container').addEventListener('click', async (e) => {
    const btn = e.target.closest('.vote-btn');
    if (!btn) return;
    if (!confirm(`Отдать голос за «${btn.dataset.name}»? Изменить выбор будет нельзя.`)) return;
    document.querySelectorAll('.vote-btn').forEach(b => b.disabled = true);
    try {
        const res = await fetch(`${API_URL}/polls/vote`, {
            method: 'POST',
            headers: adminHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ league_id: Number(btn.dataset.league), round_name: btn.dataset.round, player_id: Number(btn.dataset.player) })
        });
        if (!res.ok) alert(formatApiError(await res.json().catch(() => ({})), 'Не удалось проголосовать'));
    } catch (err) {
        console.error(err);
        alert('Сетевая ошибка. Попробуйте ещё раз.');
    }
    fetchVote(currentLeagueId);
});

// --- ПРОФИЛЬ КОМАНДЫ (С календарем матчей) ---
async function openTeamProfile(teamId) {
    try {
        const res = await fetch(`${API_URL}/teams/${teamId}`);
        const team = await res.json();
        
        let modal = document.getElementById('team-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'team-modal';
            modal.style.cssText = `position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: var(--overlay); display: flex; justify-content: center; align-items: center; z-index: 1000; padding: 16px; box-sizing: border-box;`;
            document.body.appendChild(modal);
        }

        const s = team.stats;
        modal.innerHTML = `
            <div style="background: var(--surface-solid); width: 100%; max-width: 480px; border-radius: 16px; padding: 20px; max-height: 90vh; overflow-y: auto;">
                <div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid var(--border); padding-bottom: 12px; margin-bottom: 15px;">
                    <div style="display: flex; align-items: center; gap: 12px;">
                        <img loading="lazy" decoding="async" src="${mediaUrl(team.logo_url)}" width="52" height="52" style="object-fit: contain;">
                        <div>
                            <h2 style="margin: 0; font-size: 18px; color: var(--heading);">${esc(team.name)}${team.withdrawn ? '<span class="badge-withdrawn">снята с турнира</span>' : ''}</h2>
                            <span style="font-size: 12px; color: var(--text-2);">Очков: <b>${s.points}</b> | РМ: <b>${s.goal_difference > 0 ? '+' + s.goal_difference : s.goal_difference}</b></span>
                            ${team.withdrawn && team.withdrawal_note ? `<div style="font-size: 11px; color: var(--text-3); margin-top: 2px;">Решение Оргкомитета: ${esc(team.withdrawal_note)}</div>` : ''}
                        </div>
                    </div>
                    <div style="display: flex; align-items: center; gap: 4px;">
                        <button onclick="toggleMyTeam(${team.id})" title="Моя команда" style="background: none; border: none; font-size: 22px; cursor: pointer;">${team.id === myTeamId ? '⭐' : '☆'}</button>
                        <button onclick="document.getElementById('team-modal').style.display='none'" style="background: none; border: none; font-size: 22px; cursor: pointer;">✕</button>
                    </div>
                </div>

                <div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin-bottom: 12px; text-align: center;">
                    <div style="background: var(--surface-2); padding: 6px; border-radius: 8px; border: 1px solid var(--border);">
                        <div style="font-size: 10px; color: var(--text-2);">Игры</div>
                        <div style="font-size: 14px; font-weight: bold;">${s.played}</div>
                    </div>
                    <div style="background: var(--green-bg); padding: 6px; border-radius: 8px; border: 1px solid var(--green-border);">
                        <div style="font-size: 10px; color: var(--green);">Победы</div>
                        <div style="font-size: 14px; font-weight: bold; color: var(--green);">${s.wins}</div>
                    </div>
                    <div style="background: var(--amber-bg); padding: 6px; border-radius: 8px; border: 1px solid var(--amber-border);">
                        <div style="font-size: 10px; color: var(--amber);">Ничьи</div>
                        <div style="font-size: 14px; font-weight: bold; color: var(--amber);">${s.draws}</div>
                    </div>
                    <div style="background: var(--red-bg); padding: 6px; border-radius: 8px; border: 1px solid var(--red-soft);">
                        <div style="font-size: 10px; color: var(--red);">Пораж.</div>
                        <div style="font-size: 14px; font-weight: bold; color: var(--red);">${s.losses}</div>
                    </div>
                </div>

                <h4 style="margin: 15px 0 8px 0; color: var(--heading); font-size: 13px; border-top: 1px solid var(--border); padding-top: 10px;">📅 Календарь и результаты матчей:</h4>
                <div style="display: flex; flex-direction: column; gap: 6px; max-height: 180px; overflow-y: auto; margin-bottom: 14px;">
                    ${team.matches && team.matches.length ? team.matches.map(m => `
                        <div style="display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; background: var(--surface-2); border-radius: 8px; border: 1px solid var(--border); font-size: 11px;">
                            <div>
                                <span style="font-weight: bold; color: var(--heading);">${m.round}</span><br>
                                <span style="color: var(--text-2);">${esc(whenLabel(m))}</span>
                            </div>
                            <div style="display: flex; align-items: center; gap: 6px;">
                                <span style="font-weight: 600; text-align: right; max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${m.is_home ? team.name : m.opponent_name}</span>
                                <img loading="lazy" decoding="async" src="${mediaUrl(m.is_home ? team.logo_url : m.opponent_logo)}" width="20" height="20" style="object-fit: contain;">
                                <span style="background: ${m.status === 'finished' ? 'var(--accent)' : 'var(--border)'}; color: ${m.status === 'finished' ? 'var(--gold)' : 'var(--text-2)'}; padding: 2px 6px; border-radius: 4px; font-weight: bold; white-space: nowrap;">
                                    ${m.annulled ? 'аннул.' : (m.status === 'finished' ? `${m.home_score} : ${m.away_score}${m.technical ? ' тех.' : ''}` : 'vs')}
                                </span>
                                <img loading="lazy" decoding="async" src="${mediaUrl(!m.is_home ? team.logo_url : m.opponent_logo)}" width="20" height="20" style="object-fit: contain;">
                                <span style="font-weight: 600; text-align: left; max-width: 90px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${!m.is_home ? team.name : m.opponent_name}</span>
                            </div>
                        </div>
                    `).join('') : '<p class="no-data">Матчи не найдены</p>'}
                </div>

                <h4 style="margin: 0 0 10px 0; color: var(--heading); font-size: 13px; border-top: 1px solid var(--border); padding-top: 10px;">👥 Состав и статистика игроков:</h4>
                <div style="display: flex; flex-direction: column; gap: 6px;">
                    ${team.players.length ? team.players.map(p => `
                        <div onclick="openPlayerProfile(${team.id}, ${p.id})" style="display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; background: var(--surface-2); border-radius: 8px; border: 1px solid var(--border); font-size: 12px; cursor: pointer;">
                            <span><b>${p.number}.</b> ${p.name}</span>
                            <span style="white-space: nowrap; color: var(--text-2);">
                                ⚽ ${p.stats.goals} ${p.stats.own_goals > 0 ? `(-${p.stats.own_goals})` : ''} | 🟨 ${p.stats.yellow_cards} | 🟥 ${p.stats.red_cards} | ⭐ ${p.stats.man_of_the_match}
                            </span>
                        </div>
                    `).join('') : '<p class="no-data">Состав не загружен</p>'}
                </div>
                ${isAdmin() ? `
                <div style="margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border);">
                    ${team.withdrawn
                        ? `<button class="sanction-btn" style="margin: 0; width: 100%; padding: 10px;" onclick="restoreTeam(${team.id})">↩ Вернуть команду в турнир</button>`
                        : `<button class="sanction-btn danger" style="margin: 0; width: 100%; padding: 10px;" onclick="withdrawTeam(${team.id})">⛔ Снять команду с турнира</button>`}
                </div>` : ''}
            </div>
        `;
        modal.style.display = 'flex';
    } catch (e) { console.error(e); }
}

// --- СНЯТИЕ КОМАНДЫ С ТУРНИРА (регламент, ст. 3.13) ---
async function withdrawTeam(teamId) {
    if (!isAdmin()) return;
    try {
        const pres = await fetch(`${API_URL}/teams/${teamId}/withdraw-preview`, { headers: adminHeaders() });
        const prev = await pres.json();
        if (!pres.ok) { alert(formatApiError(prev, 'Не удалось получить данные')); return; }
        const consequence = prev.mode === 'annul'
            ? `Сыграно ${prev.played} из ${prev.total} матчей (${prev.percent}%) — меньше 50%.\n\nВСЕ матчи команды будут АННУЛИРОВАНЫ: результаты, голы и карточки у неё и у соперников.`
            : `Сыграно ${prev.played} из ${prev.total} матчей (${prev.percent}%) — 50% и больше.\n\nСыгранные результаты сохранятся. В оставшихся ${prev.remaining} матчах команде засчитается техническое поражение 0:5, соперникам 5:0.`;
        if (!confirm(`Снять «${prev.team}» с турнира?\n\n${consequence}\n\nЭто можно отменить кнопкой «Вернуть команду».`)) return;
        const note = prompt('Решение Оргкомитета (необязательно, видно всем):', '') || '';
        const res = await fetch(`${API_URL}/teams/${teamId}/withdraw`, {
            method: 'POST', headers: adminHeaders({ 'Content-Type': 'application/json' }), body: JSON.stringify({ note })
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { alert(formatApiError(data, 'Не удалось снять команду')); return; }
        alert(data.message);
        await fetchLeagues();
        loadCurrentTabContent();
        openTeamProfile(teamId);
    } catch (e) { console.error(e); alert('Сетевая ошибка. Попробуйте ещё раз.'); }
}

async function restoreTeam(teamId) {
    if (!isAdmin() || !confirm('Вернуть команду в турнир? Аннулированные матчи восстановятся, технические результаты, назначенные из-за снятия, отменятся.')) return;
    try {
        const res = await fetch(`${API_URL}/teams/${teamId}/restore`, { method: 'POST', headers: adminHeaders() });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { alert(formatApiError(data, 'Не удалось вернуть команду')); return; }
        await fetchLeagues();
        loadCurrentTabContent();
        openTeamProfile(teamId);
    } catch (e) { console.error(e); alert('Сетевая ошибка. Попробуйте ещё раз.'); }
}

// --- ЗАГРУЗКА EXCEL ГРАФИКА ТУРОВ ---
async function uploadExcelSchedule() {
    const leagueName = document.getElementById('excel-league-name').value;
    const fileInput = document.getElementById('excel-schedule-file');
    const statusText = document.getElementById('excel-upload-status');

    if (fileInput.files.length === 0) {
        statusText.innerText = "Выберите Excel файл!";
        statusText.style.color = "var(--red)";
        return;
    }

    const formData = new FormData();
    formData.append("league_name", leagueName);
    formData.append("file", fileInput.files[0]);

    statusText.innerText = "Загрузка графика...";
    statusText.style.color = "var(--amber)";

    try {
        const res = await fetch(`${API_URL}/upload-schedule-excel`, {
            method: 'POST',
            headers: adminHeaders(),
            body: formData
        });
        const data = await res.json();
        if (res.ok) {
            statusText.innerText = `✅ Успешно! ${data.message}`;
            statusText.style.color = "var(--green)";
            fileInput.value = "";
            await fetchLeagues();
        } else {
            statusText.innerText = `❌ Ошибка: ${formatApiError(data, 'Сбой на сервере')}`;
            statusText.style.color = "var(--red)";
        }
    } catch (e) {
        console.error(e);
        statusText.innerText = "❌ Сетевая ошибка.";
        statusText.style.color = "var(--red)";
    }
}

// --- ЗАГРУЗКА СОСТАВА КОМАНДЫ ИЗ WORD ---
function fillRosterLeagues() {
    const select = document.getElementById('roster-league');
    if (!select) return;
    const prev = select.value;
    select.innerHTML = leaguesData.map(l => `<option value="${l.name}">${l.name}</option>`).join('');
    if (prev && leaguesData.some(l => l.name === prev)) select.value = prev;
    fillRosterTeams();
}

function fillRosterTeams() {
    const leagueName = document.getElementById('roster-league').value;
    const league = leaguesData.find(l => l.name === leagueName);
    const list = document.getElementById('roster-team-list');
    list.innerHTML = league
        ? league.teams.map(t => `<option value="${t.name}">${t.players.length ? `игроков: ${t.players.length}` : 'состав не загружен'}</option>`).join('')
        : '';
}

async function uploadRoster() {
    const leagueName = document.getElementById('roster-league').value;
    const teamName = document.getElementById('roster-team').value.trim();
    const fileInput = document.getElementById('roster-file');
    const logoInput = document.getElementById('roster-logo');
    const statusText = document.getElementById('roster-upload-status');

    if (!teamName || fileInput.files.length === 0) {
        statusText.innerText = "Укажите команду и выберите файл .docx!";
        statusText.style.color = "var(--red)";
        return;
    }

    const formData = new FormData();
    formData.append("league_name", leagueName);
    formData.append("team_name", teamName);
    formData.append("file", fileInput.files[0]);
    if (logoInput.files.length) formData.append("logo", logoInput.files[0]);

    statusText.innerText = "Загрузка состава...";
    statusText.style.color = "var(--amber)";

    try {
        const res = await fetch(`${API_URL}/upload-roster`, { method: 'POST', headers: adminHeaders(), body: formData });
        const data = await res.json();
        if (res.ok) {
            statusText.innerText = `✅ ${data.message}`;
            statusText.style.color = "var(--green)";
            fileInput.value = ""; logoInput.value = ""; document.getElementById('roster-team').value = "";
            await fetchLeagues();
            fillRosterLeagues();
            loadCurrentTabContent();
        } else {
            statusText.innerText = `❌ Ошибка: ${formatApiError(data, 'Сбой на сервере')}`;
            statusText.style.color = "var(--red)";
        }
    } catch (e) {
        console.error(e);
        statusText.innerText = "❌ Сетевая ошибка.";
        statusText.style.color = "var(--red)";
    }
}

// --- МАССОВАЯ ЗАГРУЗКА МЕДИА ПАЧКОЙ ---
async function uploadBulkMedia() {
    const input = document.getElementById('bulk-media-upload');
    const statusText = document.getElementById('upload-status');
    
    if (input.files.length === 0) {
        statusText.innerText = "Выберите файлы для загрузки!";
        statusText.style.color = "var(--red)";
        return;
    }

    const formData = new FormData();
    for (let i = 0; i < input.files.length; i++) {
        formData.append("files", input.files[i]);
    }

    statusText.innerText = "Загрузка и привязка файлов...";
    statusText.style.color = "var(--amber)";

    try {
        const res = await fetch(`${API_URL}/upload-media-bulk`, {
            method: 'POST',
            headers: adminHeaders(),
            body: formData
        });
        
        const data = await res.json();
        if (res.ok) {
            statusText.innerText = `✅ Успешно! ${data.message}`;
            statusText.style.color = "var(--green)";
            input.value = ""; 
        } else {
            statusText.innerText = `❌ Ошибка: ${formatApiError(data, 'Сбой на сервере')}`;
            statusText.style.color = "var(--red)";
        }
    } catch (e) {
        console.error(e);
        statusText.innerText = "❌ Сетевая ошибка при загрузке.";
        statusText.style.color = "var(--red)";
    }
}

// --- ПРОФИЛЬ ИГРОКА ---
async function openPlayerProfile(teamId, playerId) {
    try {
        const res = await fetch(`${API_URL}/teams/${teamId}`);
        const team = await res.json();
        const p = team.players.find(x => x.id === playerId);
        if (!p) return;

        let modal = document.getElementById('player-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'player-modal';
            modal.style.cssText = `position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: var(--overlay); display: flex; justify-content: center; align-items: center; z-index: 1100; padding: 16px; box-sizing: border-box;`;
            document.body.appendChild(modal);
        }

        const photoUrl = mediaUrl(p.photo_url);

        modal.innerHTML = `
            <div style="background: var(--surface-solid); width: 100%; max-width: 340px; border-radius: 16px; padding: 24px; text-align: center; position: relative; box-shadow: var(--shadow-lg);">
                <button onclick="document.getElementById('player-modal').style.display='none'" style="position: absolute; top: 12px; right: 12px; background: var(--surface-2); border: none; border-radius: 50%; width: 30px; height: 30px; font-size: 16px; font-weight: bold; color: var(--text-2); cursor: pointer; display: flex; align-items: center; justify-content: center;">✕</button>
                
                <img src="${photoUrl}" style="width: 110px; height: 110px; border-radius: 50%; object-fit: cover; border: 4px solid var(--accent); margin-bottom: 12px; background: var(--surface-2);">
                
                <h2 style="margin: 0 0 6px 0; color: var(--heading); font-size: 18px; line-height: 1.2;">${p.name}</h2>
                <p style="margin: 0 0 20px 0; font-size: 14px; color: var(--text-2); font-weight: 500;">№ ${p.number} | ${team.name}</p>
                
                <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; text-align: left;">
                    <div style="background: var(--surface-2); padding: 12px; border-radius: 10px; border: 1px solid var(--border);">
                        <span style="font-size: 12px; color: var(--text-2); font-weight: 600;">Голы</span><br>
                        <span style="font-weight: 900; font-size: 16px; color: var(--text);">⚽ ${p.stats.goals}</span>
                        ${p.stats.own_goals > 0 ? `<div style="color:var(--red); font-size:11px; font-weight: bold; margin-top:2px;">(-${p.stats.own_goals} авто)</div>` : ''}
                    </div>
                    <div style="background: var(--amber-bg); padding: 12px; border-radius: 10px; border: 1px solid var(--amber-border);">
                        <span style="font-size: 12px; color: var(--amber); font-weight: 600;">Игрок матча</span><br>
                        <span style="font-weight: 900; font-size: 16px; color: var(--amber);">⭐ ${p.stats.man_of_the_match}</span>
                    </div>
                    <div style="background: var(--red-bg); padding: 12px; border-radius: 10px; border: 1px solid var(--red-soft);">
                        <span style="font-size: 12px; color: var(--red); font-weight: 600;">Желтые</span><br>
                        <span style="font-weight: 900; font-size: 16px; color: var(--red);">🟨 ${p.stats.yellow_cards}</span>
                    </div>
                    <div style="background: var(--red-bg); padding: 12px; border-radius: 10px; border: 1px solid var(--red-border);">
                        <span style="font-size: 12px; color: var(--red); font-weight: 600;">Красные</span><br>
                        <span style="font-weight: 900; font-size: 16px; color: var(--red);">🟥 ${p.stats.red_cards}</span>
                    </div>
                </div>
            </div>
        `;
        modal.style.display = 'flex';
    } catch (e) { console.error(e); }
}

// --- ОКНО АДМИНА ДЛЯ ВВОДА РЕЗУЛЬТАТОВ ---
async function openResultModal(matchId) {
    if (!isAdmin()) return;
    try {
        const res = await fetch(`${API_URL}/matches`);
        const matches = await res.json();
        const match = matches.find(m => m.id === matchId);
        if (!match) return;

        const leagueObj = leaguesData.find(l => l.id === currentLeagueId);
        const homeTeamObj = leagueObj.teams.find(t => t.id === match.home_team_id);
        const awayTeamObj = leagueObj.teams.find(t => t.id === match.away_team_id);

        let modal = document.getElementById('admin-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'admin-modal';
            modal.style.cssText = `position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: var(--overlay); display: flex; justify-content: center; align-items: center; z-index: 1200; padding: 16px; box-sizing: border-box;`;
            document.body.appendChild(modal);
        }

        // Игроки, которым по санкциям нельзя играть в этом матче (регламент, ст. 3.9)
        window._suspended = {};
        try {
            const sres = await fetch(`${API_URL}/matches/${matchId}/suspended`);
            if (sres.ok) (await sres.json()).forEach(s => { window._suspended[s.player_id] = s; });
        } catch (e) { console.error(e); }
        const suspendedList = Object.values(window._suspended);
        const mark = p => window._suspended[p.id] ? '⛔ ' : '';

        const allPlayers = [...(homeTeamObj ? homeTeamObj.players : []), ...(awayTeamObj ? awayTeamObj.players : [])];

        modal.innerHTML = `
            <div style="background: var(--surface-solid); width: 100%; max-width: 450px; border-radius: 16px; padding: 20px; max-height: 90vh; overflow-y: auto;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <h3 style="margin:0; color: var(--heading);">Ввод результата матча</h3>
                    <button onclick="document.getElementById('admin-modal').style.display='none'" style="background:none; border:none; font-size:20px; cursor:pointer;">✕</button>
                </div>
                ${suspendedList.length ? `
                <div class="warn-banner">⛔ Не имеют права играть в этом матче: <b>${suspendedList.map(s => esc(s.player_name)).join(', ')}</b>.
                    Участие дисквалифицированного игрока — техническое поражение 0:5 (регламент, ст. 3.9).</div>` : ''}
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; font-weight: bold;">
                    <div>${match.home_team}</div>
                    <div style="display: flex; gap: 6px; align-items: center;">
                        <input type="number" id="home-score-input" value="${match.home_score}" min="0" max="99" step="1" inputmode="numeric" onkeydown="blockNonDigitKeys(event)" oninput="clampScoreInput(this)" style="width: 45px; text-align:center; font-size:16px;" /> :
                        <input type="number" id="away-score-input" value="${match.away_score}" min="0" max="99" step="1" inputmode="numeric" onkeydown="blockNonDigitKeys(event)" oninput="clampScoreInput(this)" style="width: 45px; text-align:center; font-size:16px;" />
                    </div>
                    <div>${match.away_team}</div>
                </div>

                <div class="tech-box">
                    <div class="tech-title">Технический результат (ст. 3.9–3.11, 9.4)</div>
                    <div class="tech-actions">
                        <button class="tech-btn" onclick="setTechnicalResult('home')">Победа: ${esc(match.home_team)} 5:0</button>
                        <button class="tech-btn" onclick="setTechnicalResult('away')">Победа: ${esc(match.away_team)} 0:5</button>
                        <button id="tech-cancel" class="tech-btn ghost" onclick="setTechnicalResult(null)" style="display: none;">Отменить</button>
                    </div>
                    <div id="tech-note" class="tech-note"></div>
                </div>

                <div id="match-details">
                <div style="margin-bottom: 12px;">
                    <label style="font-size: 12px; font-weight: bold; color: var(--heading);">⭐ Игрок матча (MOTM):</label>
                    <select id="motm-player-select" style="width: 100%; padding: 6px; margin-top: 4px; border-radius: 6px; border: 1px solid var(--border-strong);">
                        <option value="">-- Никто --</option>
                        ${allPlayers.map(p => `<option value="${p.id}" ${match.man_of_the_match === p.name ? 'selected' : ''}>${mark(p)}${p.name}</option>`).join('')}
                    </select>
                </div>

                <h4 style="margin: 0 0 8px 0; font-size: 13px; color: var(--heading);">События матча:</h4>
                <div id="events-list" style="display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px;"></div>

                <div style="background: var(--surface-2); padding: 10px; border-radius: 8px; border: 1px solid var(--border); margin-bottom: 16px;">
                    <select id="event-team" style="width: 100%; padding: 6px; margin-bottom: 6px; border-radius: 6px;" onchange="updatePlayerSelects()">
                        <option value="${match.home_team_id}">${match.home_team}</option>
                        <option value="${match.away_team_id}">${match.away_team}</option>
                    </select>
                    <select id="event-player" style="width: 100%; padding: 6px; margin-bottom: 6px; border-radius: 6px;"></select>
                    <select id="event-type" style="width: 100%; padding: 6px; margin-bottom: 6px; border-radius: 6px;">
                        <option value="goal">⚽ Гол</option>
                        <option value="own_goal">⚽🥅 Автогол (-1)</option>
                        <option value="yellow_card">🟨 Желтая карточка</option>
                        <option value="red_card">🟥 Красная карточка</option>
                    </select>
                    <input type="number" id="event-minute" min="0" max="60" step="1" inputmode="numeric" onkeydown="blockNonDigitKeys(event)" oninput="clampScoreInput(this)" placeholder="Минута (необяз.)" style="width: 100%; padding: 6px; box-sizing: border-box; border-radius: 6px; border: 1px solid var(--border-strong);" />
                    <button onclick="addMatchEvent(${matchId})" style="width: 100%; margin-top: 8px; background: var(--green-btn); color: white; border: none; padding: 8px; border-radius: 6px; font-weight: bold; cursor: pointer;">+ Добавить событие</button>
                    <p id="event-message" style="margin: 8px 0 0; font-size: 12px; font-weight: bold; min-height: 16px;"></p>
                </div>
                </div>

                <button onclick="saveMatchResult(${matchId})" style="width: 100%; background: var(--accent-grad); color: white; border: none; padding: 12px; border-radius: 8px; font-weight: bold; cursor: pointer;">💾 Сохранить результат</button>
                ${match.status === 'finished' ? `
                <button onclick="resetMatchResult(${matchId})" style="width: 100%; margin-top: 8px; background: var(--surface-solid); color: var(--red); border: 1px solid var(--red-border); padding: 10px; border-radius: 8px; font-weight: bold; cursor: pointer;">↩ Вернуть в статус «не сыгран»</button>` : ''}
            </div>
        `;
        modal.style.display = 'flex';

        window._modalTeamsData = { 
            home: leagueObj.teams.find(t => t.id === match.home_team_id), 
            away: leagueObj.teams.find(t => t.id === match.away_team_id) 
        };
        updatePlayerSelects();
        renderCurrentEvents(match.events || []);
        window._techResult = false;
        if (match.technical) setTechnicalResult(match.home_score > match.away_score ? 'home' : 'away');
    } catch (e) { console.error(e); }
}

// Технический результат: 5:0 / 0:5, без голов, карточек и «Игрока матча»
function setTechnicalResult(winner) {
    const h = document.getElementById('home-score-input'), a = document.getElementById('away-score-input');
    const details = document.getElementById('match-details');
    const note = document.getElementById('tech-note');
    const cancel = document.getElementById('tech-cancel');
    if (winner === null) {
        window._techResult = false;
        h.disabled = a.disabled = false;
        h.value = a.value = 0;
        details.classList.remove('is-disabled');
        note.textContent = ''; cancel.style.display = 'none';
        return;
    }
    window._techResult = true;
    h.value = winner === 'home' ? 5 : 0;
    a.value = winner === 'home' ? 0 : 5;
    h.disabled = a.disabled = true;
    renderCurrentEvents([]);                       // события не вносятся
    document.getElementById('motm-player-select').value = '';
    setEventMessage('');
    details.classList.add('is-disabled');
    note.textContent = 'Технический результат: голы, карточки и «Игрок матча» не вносятся.';
    cancel.style.display = '';
}

// Поля счёта и минуты: только цифры, от 0 до max (стрелки, ввод с клавиатуры и вставка не уведут в минус)
function blockNonDigitKeys(e) {
    if (['-', '+', 'e', 'E', '.', ','].includes(e.key)) e.preventDefault();
}

function clampScoreInput(input) {
    if (input.value === '') return;
    const max = Number(input.max) || 99;
    const n = Math.floor(Number(input.value));
    if (!Number.isFinite(n) || n < 0) input.value = 0;
    else if (n > max) input.value = max;
    else if (String(n) !== input.value) input.value = n;
}

// Ошибочно внесённый результат: вернуть матч в «не сыгран» (счёт, события, «игрок матча» и автосанкции сбрасываются)
async function resetMatchResult(matchId) {
    if (!confirm('Вернуть матч в статус «не сыгран»?\n\nСчёт, голы, карточки и «игрок матча» этого матча будут удалены, автоматические дисквалификации по нему снимутся.')) return;
    try {
        const res = await fetch(`${API_URL}/matches/${matchId}/reset`, { method: 'POST', headers: adminHeaders() });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { alert(formatApiError(data, 'Не удалось вернуть матч')); return; }
        document.getElementById('admin-modal').style.display = 'none';
        await fetchLeagues();
        loadCurrentTabContent();
    } catch (e) {
        console.error(e);
        alert('Сетевая ошибка. Попробуйте ещё раз.');
    }
}

function updatePlayerSelects() {
    const teamId = parseInt(document.getElementById('event-team').value);
    const teams = window._modalTeamsData;
    const team = (teamId === teams.home.id) ? teams.home : teams.away;
    const playerSelect = document.getElementById('event-player');

    playerSelect.innerHTML = team.players.map(p => `<option value="${p.id}">${(window._suspended || {})[p.id] ? '⛔ ' : ''}${p.number}. ${p.name}</option>`).join('');
}

let tempMatchEvents = [];
function renderCurrentEvents(events) {
    tempMatchEvents = [...events];
    const container = document.getElementById('events-list');
    if (!tempMatchEvents.length) {
        container.innerHTML = '<span style="font-size:12px; color:var(--text-3); font-style:italic;">События не добавлены</span>';
        return;
    }
    // Вторая жёлтая одного игрока в матче показывается как красная (дисквалификация на 1 матч назначается автоматически)
    const yellowSeen = {};
    container.innerHTML = tempMatchEvents.map((ev, idx) => {
        let icon, extra = '', bg = 'var(--surface-2)';
        if (ev.event_type === 'yellow_card') {
            yellowSeen[ev.player_id] = (yellowSeen[ev.player_id] || 0) + 1;
            if (yellowSeen[ev.player_id] === 2) {
                icon = '🟨→🟥'; bg = 'var(--red-soft)';
                extra = '<div style="color:var(--red); font-weight:700; margin-top:2px;">Вторая жёлтая = красная. Автоматическая дисквалификация на 1 матч</div>';
            } else if (yellowSeen[ev.player_id] > 2) {
                icon = '⚠️'; bg = 'var(--amber-soft)';
                extra = '<div style="color:var(--amber); font-weight:700; margin-top:2px;">Третья жёлтая в матче невозможна — удалите это событие</div>';
            } else icon = '🟨';
        } else {
            icon = ev.event_type === 'goal' ? '⚽' : (ev.event_type === 'own_goal' ? '⚽🥅' : '🟥');
        }
        return `
        <div style="display: flex; justify-content: space-between; align-items: center; background: ${bg}; padding: 6px 10px; border-radius: 6px; font-size: 12px;">
            <span>${icon} <b>${esc(ev.player_name)}</b> (${ev.minute || '?'} мин)${extra}</span>
            <button onclick="removeMatchEvent(${idx})" style="background:none; border:none; color:var(--red); cursor:pointer; font-weight:bold;">✕</button>
        </div>`;
    }).join('');
}

function setEventMessage(text, isError) {
    const el = document.getElementById('event-message');
    if (!el) return;
    el.textContent = text || '';
    el.style.color = isError ? 'var(--red)' : 'var(--amber)';
}

function addMatchEvent(matchId) {
    const teamId = parseInt(document.getElementById('event-team').value);
    const playerId = parseInt(document.getElementById('event-player').value);
    const eventType = document.getElementById('event-type').value;
    const minute = parseInt(document.getElementById('event-minute').value) || null;
    setEventMessage('');
    if (!playerId) { setEventMessage('Выберите игрока', true); return; }

    const teams = window._modalTeamsData;
    const team = (teamId === teams.home.id) ? teams.home : teams.away;
    const player = team.players.find(p => p.id === playerId);

    // Дисквалифицированный игрок не имеет права играть (ст. 3.9) — спрашиваем подтверждение
    const sus = (window._suspended || {})[playerId];
    if (sus && !confirm(`⛔ ${sus.player_name} дисквалифицирован (${sus.term_text || sus.reason || 'санкция'}).\nУчастие дисквалифицированного игрока — техническое поражение 0:5.\n\nВсё равно добавить событие?`)) return;

    // Правила карточек (сервер проверяет то же самое)
    const mine = tempMatchEvents.filter(e => e.player_id === playerId);
    const yellows = mine.filter(e => e.event_type === 'yellow_card').length;
    const reds = mine.filter(e => e.event_type === 'red_card').length;
    if (eventType === 'yellow_card') {
        if (yellows >= 2) { setEventMessage('Три жёлтые одному игроку в матче невозможны: вторая жёлтая уже означает красную', true); return; }
        if (reds >= 1) { setEventMessage('Игрок уже удалён с поля красной карточкой', true); return; }
    } else if (eventType === 'red_card') {
        if (reds >= 1) { setEventMessage('Игрок уже удалён с поля красной карточкой', true); return; }
        if (yellows >= 2) { setEventMessage('Игрок уже удалён по второй жёлтой, отдельная красная не нужна', true); return; }
    }
    if (eventType === 'yellow_card' && yellows === 1) {
        setEventMessage('Это вторая жёлтая: она станет красной, игроку автоматически назначится дисквалификация на 1 матч', false);
    }

    tempMatchEvents.push({
        player_id: playerId,
        team_id: teamId,
        player_name: player ? player.name : '',
        event_type: eventType,
        minute: minute
    });
    renderCurrentEvents(tempMatchEvents);
}

function removeMatchEvent(index) {
    tempMatchEvents.splice(index, 1);
    renderCurrentEvents(tempMatchEvents);
}

async function saveMatchResult(matchId) {
    // Счёт не может быть отрицательным или больше 99
    const homeScore = Math.min(99, Math.max(0, parseInt(document.getElementById('home-score-input').value) || 0));
    const awayScore = Math.min(99, Math.max(0, parseInt(document.getElementById('away-score-input').value) || 0));
    const motmId = document.getElementById('motm-player-select').value;

    const payload = {
        home_score: homeScore,
        away_score: awayScore,
        technical: !!window._techResult,
        man_of_the_match_id: (motmId && !window._techResult) ? parseInt(motmId) : null,
        events: tempMatchEvents.map(e => ({ player_id: e.player_id, team_id: e.team_id, event_type: e.event_type, minute: e.minute }))
    };

    try {
        const res = await fetch(`${API_URL}/matches/${matchId}/result`, {
            method: 'POST',
            headers: adminHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok) {
            document.getElementById('admin-modal').style.display = 'none';
            if (data.message && data.message.includes('дисквалификац')) alert(data.message);
            loadCurrentTabContent();
            await fetchLeagues();
        } else {
            alert(formatApiError(data, "Ошибка сохранения"));
        }
    } catch (e) {
        console.error(e);
    }
}