const tg = window.Telegram.WebApp;
tg.expand(); // Разворачиваем Mini App на весь экран

// Укажите URL вашего запущенного бэкенда (например, локальный или публичный адрес сервера)
const API_URL = "https://your-backend-domain.com/api"; 

document.addEventListener("DOMContentLoaded", () => {
    loadLeagues();

    document.getElementById("back-btn").addEventListener("click", () => {
        switchScreen("leagues-screen");
    });
});

async function loadLeagues() {
    try {
        const response = await fetch(`${API_URL}/leagues`);
        const leagues = await response.json();
        
        const container = document.getElementById("leagues-container");
        container.innerHTML = "";

        leagues.forEach(league => {
            const leagueDiv = document.createElement("div");
            leagueDiv.className = "league-section";
            leagueDiv.innerHTML = `<h2>${league.name}</h2>`;

            league.teams.forEach(team => {
                const teamCard = document.createElement("div");
                teamCard.className = "team-card";
                teamCard.innerHTML = `
                    <img src="${team.logo_url || 'https://via.placeholder.com/40'}" class="team-logo-small" alt="logo">
                    <span>${team.name}</span>
                `;
                teamCard.addEventListener("click", () => loadTeamProfile(team.id));
                leagueDiv.appendChild(teamCard);
            });

            container.appendChild(leagueDiv);
        });
    } catch (error) {
        console.error("Ошибка загрузки лиг:", error);
    }
}

async function loadTeamProfile(teamId) {
    try {
        const response = await fetch(`${API_URL}/teams/${teamId}`);
        const team = await response.json();

        const profileContainer = document.getElementById("team-profile-container");
        
        let playersRows = team.players.map(p => `
            <tr>
                <td>${p.number}</td>
                <td>${p.name}</td>
                <td>${p.stats.goals}</td>
                <td>${p.stats.assists}</td>
                <td>${p.stats.yellow_cards}/${p.stats.red_cards}</td>
            </tr>
        `).join("");

        profileContainer.innerHTML = `
            <div class="profile-header">
                <img src="${team.logo_url || 'https://via.placeholder.com/80'}" class="team-logo-large" alt="logo">
                <h2>${team.name}</h2>
            </div>
            <h3>Состав и статистика игроков</h3>
            <table>
                <thead>
                    <tr>
                        <th>№</th>
                        <th>Игрок</th>
                        <th>Г</th>
                        <th>П</th>
                        <th>К/К</th>
                    </tr>
                </thead>
                <tbody>
                    ${playersRows}
                </tbody>
            </table>
        `;

        switchScreen("team-screen");
    } catch (error) {
        console.error("Ошибка загрузки профиля команды:", error);
    }
}

function switchScreen(screenId) {
    document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
    document.getElementById(screenId).classList.add("active");
}