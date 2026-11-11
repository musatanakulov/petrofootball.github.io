const API_URL = 'http://127.0.0.1:8000/api';

document.addEventListener('DOMContentLoaded', () => {
    if (window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.ready();
        window.Telegram.WebApp.expand();
    }
    fetchLeagues();
    fetchMatches();
    fetchStandings();
    fetchScorers();
    fetchDisqualifications();
});

function switchTab(tabName) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

    const tabMap = { 'teams': 0, 'matches': 1, 'standings': 2, 'scorers': 3, 'disq': 4 };
    document.querySelectorAll('.tab-btn')[tabMap[tabName]].classList.add('active');
    document.getElementById(`${tabName}-tab`).classList.add('active');
}

async function fetchLeagues() {
    try {
        const res = await fetch(`${API_URL}/leagues`);
        const leagues = await res.json();
        const container = document.getElementById('leagues-container');
        container.innerHTML = leagues.map(l => `
            <div class="league-section">
                <h3>${l.name}</h3>
                <div style="display: flex; flex-direction: column; gap: 6px;">
                    ${l.teams.length ? l.teams.map(t => `
                        <div style="display: flex; align-items: center; gap: 10px; padding: 8px; background: #F8FAFC; border-radius: 8px; cursor: pointer; border: 1px solid #E2E8F0;" onclick="openTeam(${t.id})">
                            <img src="${t.logo_url}" width="32" height="32" style="border-radius: 50%; object-fit: cover; border: 1px solid #CBD5E1;">
                            <span style="font-weight: 600; font-size: 14px; color: #0F172A;">${t.name}</span>
                        </div>
                    `).join('') : '<p class="no-data">Нет заявленных команд</p>'}
                </div>
            </div>
        `).join('');
    } catch (e) { console.error(e); }
}

// Открытие профиля команды и её состава с кликом по игрокам
async function openTeam(teamId) {
    try {
        const res = await fetch(`${API_URL}/teams/${teamId}`);
        const team = await res.json();
        
        let modal = document.getElementById('team-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'team-modal';
            modal.style.cssText = `
                position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                background: rgba(0,0,0,0.6); display: flex; justify-content: center; align-items: center; z-index: 1000; padding: 16px; box-sizing: border-box;
            `;
            document.body.appendChild(modal);
        }

        modal.innerHTML = `
            <div style="background: white; width: 100%; max-width: 450px; border-radius: 12px; padding: 20px; max-height: 85vh; overflow-y: auto; box-shadow: 0 10px 25px rgba(0,0,0,0.2);">
                <div style="display: flex; align-items: center; justify-content: space-between; border-bottom: 2px solid #E2E8F0; padding-bottom: 10px; margin-bottom: 15px;">
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <img src="${team.logo_url}" width="40" height="40" style="border-radius: 50%; object-fit: cover; border: 1px solid #CBD5E1;">
                        <h2 style="margin: 0; font-size: 18px; color: #1E2A8A;">${team.name}</h2>
                    </div>
                    <button onclick="document.getElementById('team-modal').style.display='none'" style="background: none; border: none; font-size: 20px; cursor: pointer; color: #64748B;">✕</button>
                </div>
                <h4 style="margin: 0 0 10px 0; color: #64748B; font-size: 13px; text-transform: uppercase;">Заявка команды (нажмите на игрока):</h4>
                <div style="display: flex; flex-direction: column; gap: 8px;">
                    ${team.players.map(p => `
                        <div onclick="openPlayerProfile(${p.id})" style="display: flex; align-items: center; justify-content: space-between; padding: 10px; background: #F8FAFC; border-radius: 8px; border: 1px solid #E2E8F0; cursor: pointer; transition: background 0.2s;">
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span style="background: #1E2A8A; color: #FBBF24; width: 26px; height: 26px; display: flex; align-items: center; justify-content: center; border-radius: 50%; font-size: 12px; font-weight: bold;">${p.number}</span>
                                <span style="font-weight: 600; font-size: 14px; color: #0F172A;">${p.name}</span>
                            </div>
                            <span style="font-size: 12px; color: #64748B;">⚽ ${p.stats.goals} | 🟨 ${p.stats.yellow_cards} | 🟥 ${p.stats.red_cards}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
        modal.style.display = 'flex';
    } catch (e) {
        console.error(e);
    }
}

// Открытие персонального профиля игрока
async function openPlayerProfile(playerId) {
    try {
        const res = await fetch(`${API_URL}/players/${playerId}`);
        const p = await res.json();

        let profileModal = document.getElementById('player-modal');
        if (!profileModal) {
            profileModal = document.createElement('div');
            profileModal.id = 'player-modal';
            profileModal.style.cssText = `
                position: fixed; top: 0; left: 0; width: 100%; height: 100%;
                background: rgba(0,0,0,0.7); display: flex; justify-content: center; align-items: center; z-index: 1100; padding: 16px; box-sizing: border-box;
            `;
            document.body.appendChild(profileModal);
        }

        profileModal.innerHTML = `
            <div style="background: white; width: 100%; max-width: 380px; border-radius: 16px; padding: 24px; text-align: center; box-shadow: 0 15px 30px rgba(0,0,0,0.3); position: relative;">
                <button onclick="document.getElementById('player-modal').style.display='none'" style="position: absolute; top: 12px; right: 12px; background: none; border: none; font-size: 20px; cursor: pointer; color: #64748B;">✕</button>
                
                <img src="${p.photo_url}" width="100" height="100" style="border-radius: 50%; object-fit: cover; border: 3px solid #1E2A8A; margin-bottom: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.1);">
                
                <h3 style="margin: 0 0 4px 0; font-size: 20px; color: #0F172A;">${p.name}</h3>
                <p style="margin: 0 0 16px 0; color: #64748B; font-size: 13px; font-weight: 600;">${p.team_name} | № ${p.number}</p>
                
                <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 16px;">
                    <div style="background: #F8FAFC; padding: 10px; border-radius: 10px; border: 1px solid #E2E8F0;">
                        <div style="font-size: 11px; color: #64748B; text-transform: uppercase;">Голов забито</div>
                        <div style="font-size: 18px; font-weight: 800; color: #1E2A8A;">⚽ ${p.stats.goals}</div>
                    </div>
                    <div style="background: #F8FAFC; padding: 10px; border-radius: 10px; border: 1px solid #E2E8F0;">
                        <div style="font-size: 11px; color: #64748B; text-transform: uppercase;">Игрок матча</div>
                        <div style="font-size: 18px; font-weight: 800; color: #D97706;">⭐ ${p.stats.man_of_the_match}</div>
                    </div>
                    <div style="background: #FEFCE8; padding: 10px; border-radius: 10px; border: 1px solid #FEF08A;">
                        <div style="font-size: 11px; color: #854D0E; text-transform: uppercase;">Желтые карточки</div>
                        <div style="font-size: 18px; font-weight: 800; color: #CA8A04;">🟨 ${p.stats.yellow_cards}</div>
                    </div>
                    <div style="background: #FEF2F2; padding: 10px; border-radius: 10px; border: 1px solid #FEE2E2;">
                        <div style="font-size: 11px; color: #991B1B; text-transform: uppercase;">Красные карточки</div>
                        <div style="font-size: 18px; font-weight: 800; color: #DC2626;">🟥 ${p.stats.red_cards}</div>
                    </div>
                </div>

                <button onclick="document.getElementById('player-modal').style.display='none'" style="width: 100%; background: #1E2A8A; color: white; border: none; padding: 10px; border-radius: 8px; font-weight: bold; cursor: pointer;">Закрыть</button>
            </div>
        `;
        profileModal.style.display = 'flex';
    } catch (e) {
        console.error(e);
    }
}

async function fetchMatches() {
    try {
        const res = await fetch(`${API_URL}/matches`);
        const matches = await res.json();
        const container = document.getElementById('matches-container');
        if (!matches.length) { container.innerHTML = '<p class="no-data">Матчи не запланированы</p>'; return; }

        container.innerHTML = matches.map(m => `
            <div class="match-card">
                <div class="match-team">
                    <img src="${m.home_logo}">
                    <span style="font-size: 12px; font-weight: 700;">${m.home_team}</span>
                </div>
                <div style="text-align: center;">
                    <span class="match-score">${m.status === 'finished' ? `${m.home_score} : ${m.away_score}` : m.time}</span><br>
                    <span class="match-time">${m.date}</span>
                </div>
                <div class="match-team away">
                    <img src="${m.away_logo}">
                    <span style="font-size: 12px; font-weight: 700;">${m.away_team}</span>
                </div>
            </div>
        `).join('');
    } catch (e) { console.error(e); }
}

async function fetchStandings() {
    try {
        const res = await fetch(`${API_URL}/leagues/1/standings`);
        const data = await res.json();
        const container = document.getElementById('standings-container');
        
        if (!data.length) { container.innerHTML = '<p class="no-data">Нет данных таблицы</p>'; return; }

        container.innerHTML = `
            <div class="league-section">
                <h3>Первая лига — Таблица</h3>
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
                            <tr>
                                <td><b>${index + 1}</b></td>
                                <td style="text-align: left; display: flex; align-items: center; gap: 6px;">
                                    <img src="${row.logo_url}" width="22" height="22" style="border-radius: 50%; object-fit: cover;">
                                    ${row.team_name}
                                </td>
                                <td>${row.played}</td>
                                <td>${row.wins}</td>
                                <td>${row.draws}</td>
                                <td>${row.losses}</td>
                                <td>${row.goals_for}</td>
                                <td>${row.goals_against}</td>
                                <td>${row.goal_difference > 0 ? '+' + row.goal_difference : row.goal_difference}</td>
                                <td><b style="color: #1E2A8A;">${row.points}</b></td>
                            </tr>
                        `).join('')}
                    </table>
                </div>
            </div>
        `;
    } catch (e) { console.error(e); }
}

async function fetchScorers() {
    try {
        const res = await fetch(`${API_URL}/leagues/1/top-scorers`);
        const data = await res.json();
        const container = document.getElementById('scorers-container');

        if (!data.length) { container.innerHTML = '<p class="no-data">Голы еще не забиты</p>'; return; }

        container.innerHTML = `
            <div class="league-section">
                <h3>Топ-бомбардиры (Первая лига)</h3>
                <div style="display: flex; flex-direction: column; gap: 6px;">
                    ${data.map((p, idx) => `
                        <div style="display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; background: #F8FAFC; border-radius: 8px; border: 1px solid #E2E8F0; font-size: 14px;">
                            <span><b>${idx + 1}.</b> ${p.player_name} <span style="color: #64748B; font-size: 12px;">(${p.team_name})</span></span>
                            <span style="font-weight: 800; color: #D97706; background: #FEF3C7; padding: 2px 8px; border-radius: 6px;">⚽ ${p.goals}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    } catch (e) { console.error(e); }
}

async function fetchDisqualifications() {
    try {
        const res = await fetch(`${API_URL}/leagues/1/disqualification`);
        const data = await res.json();
        const container = document.getElementById('disq-container');

        if (!data.length) {
            container.innerHTML = '<div class="league-section"><h3>Дисквалификации</h3><p class="no-data">Нет дисквалифицированных игроков</p></div>';
            return;
        }

        container.innerHTML = `
            <div class="league-section">
                <h3>Дисквалификации и санкции КДК</h3>
                <div style="display: flex; flex-direction: column; gap: 8px;">
                    ${data.map(item => `
                        <div class="disq-item">
                            <strong>${item.player_name}</strong> <span style="color: #64748B;">(${item.team_name})</span><br>
                            <span style="color: #DC2626; font-weight: 700;">Причина: ${item.reason}</span><br>
                            <span style="font-size: 11px; color: #4B5563;">Карточек: Желтых — ${item.yellow_cards}, Красных — ${item.red_cards}</span>
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
    } catch (e) { console.error(e); }
}