const statusLine = document.getElementById("status-line");
const gameList = document.getElementById("game-list");
const scanBtn = document.getElementById("scan-btn");
const idleView = document.getElementById("idle-view");
const playView = document.getElementById("play-view");
const gameVideo = document.getElementById("game-video");
const fullscreenBtn = document.getElementById("fullscreen-btn");
const endGameBtn = document.getElementById("end-game-btn");
const backgroundGameBanner = document.getElementById("background-game-banner");
const backgroundGameText = document.getElementById("background-game-text");
const backgroundJoinBtn = document.getElementById("background-join-btn");
const backgroundEndGameBtn = document.getElementById("background-end-game-btn");
const storageToggleBtn = document.getElementById("storage-toggle-btn");
const storageForm = document.getElementById("storage-form");
const cifsFields = document.getElementById("cifs-fields");
const cifsHostInput = document.getElementById("cifs-host");
const cifsShareInput = document.getElementById("cifs-share");
const cifsSubpathInput = document.getElementById("cifs-subpath");
const cifsUsernameInput = document.getElementById("cifs-username");
const cifsPasswordInput = document.getElementById("cifs-password");

let whepUrl = null;
let peerConnection = null;

// Mesmo vocabulario do backend (backend/app/input/keymap.py) - nomes
// logicos que correspondem aos binds input_player1_* do RetroArch.
const KEYBOARD_KEY_TO_INPUT = {
    ArrowUp: "up",
    ArrowDown: "down",
    ArrowLeft: "left",
    ArrowRight: "right",
    Enter: "start",
    ShiftRight: "select",
    KeyX: "a",
    KeyZ: "b",
    KeyS: "x",
    KeyA: "y",
    KeyQ: "l",
    KeyW: "r",
};

// Botao 0 do Gamepad API (posicao fisica "A" num pad Xbox) -> "b" do
// RetroPad, e vice-versa - convencao usada pelos autoconfigs do libretro
// para pads Xbox-style. Nao validado com controle fisico ainda; se sair
// trocado no teste real, inverter aqui (sem mudanca de arquitetura).
const GAMEPAD_BUTTON_TO_KEY = {
    0: "b", 1: "a", 2: "y", 3: "x",
    4: "l", 5: "r",
    8: "select", 9: "start",
    12: "up", 13: "down", 14: "left", 15: "right",
};

let inputSocket = null;
let gamepadPollHandle = null;
const _prevGamepadButtonState = {};

async function fetchJSON(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.detail || `Erro ${response.status}`);
    }
    return data;
}

function renderGames(games) {
    gameList.innerHTML = "";
    if (games.length === 0) {
        gameList.innerHTML = "<li>Nenhum jogo encontrado. Clique em \"Escanear biblioteca\".</li>";
        return;
    }
    for (const game of games) {
        const li = document.createElement("li");
        li.className = "game-item";

        const meta = document.createElement("div");
        meta.className = "meta";

        const consoleTag = document.createElement("span");
        consoleTag.className = "console-tag";
        consoleTag.textContent = game.console;

        const titleSpan = document.createElement("span");
        titleSpan.textContent = game.title;

        const btn = document.createElement("button");
        btn.className = "play-btn";
        btn.dataset.id = game.id;
        btn.textContent = "Jogar";
        btn.addEventListener("click", () => playGame(game.id));

        meta.appendChild(consoleTag);
        meta.appendChild(titleSpan);
        li.appendChild(meta);
        li.appendChild(btn);
        gameList.appendChild(li);
    }
}

async function loadConfig() {
    try {
        const config = await fetchJSON("/config");
        whepUrl = config.whep_url;
        applyStorageConfig(config.storage);
    } catch (err) {
        statusLine.textContent = `Erro ao carregar config: ${err.message}`;
    }
}

function applyStorageConfig(storage) {
    if (!storage) {
        return;
    }
    const radio = storageForm.querySelector(`input[name="storage-mode"][value="${storage.mode}"]`);
    if (radio) {
        radio.checked = true;
    }
    cifsFields.classList.toggle("hidden", storage.mode !== "cifs");
    if (storage.mode === "cifs") {
        cifsHostInput.value = storage.cifs_host || "";
        cifsShareInput.value = storage.cifs_share || "";
        cifsSubpathInput.value = storage.cifs_subpath || "";
        cifsUsernameInput.value = storage.cifs_username || "";
    }
}

async function submitStorageForm(event) {
    event.preventDefault();
    const mode = storageForm.querySelector('input[name="storage-mode"]:checked').value;
    statusLine.textContent = "Configurando storage...";
    try {
        const result = mode === "cifs"
            ? await fetchJSON("/storage/cifs", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    host: cifsHostInput.value,
                    share: cifsShareInput.value,
                    subpath: cifsSubpathInput.value,
                    username: cifsUsernameInput.value,
                    password: cifsPasswordInput.value,
                }),
            })
            : await fetchJSON("/storage/local", { method: "POST" });
        cifsPasswordInput.value = "";
        statusLine.textContent = `Storage configurado: ${result.scanned} jogo(s) encontrado(s).`;
        renderGames(result.games);
    } catch (err) {
        statusLine.textContent = `Erro ao configurar storage: ${err.message}`;
    }
}

async function refreshStatus() {
    try {
        const data = await fetchJSON("/status");
        if (data.running && data.game) {
            statusLine.textContent = `Status: jogando ${data.game.title}`;
        } else {
            statusLine.textContent = "Status: ocioso";
        }
        updateBackgroundGameBanner(data);
    } catch (err) {
        statusLine.textContent = `Status: erro (${err.message})`;
    }
}

function updateBackgroundGameBanner(data) {
    const isIdleView = !idleView.classList.contains("hidden");
    if (isIdleView && data.running && data.game) {
        backgroundGameText.textContent = `${data.game.title} está rodando.`;
        backgroundGameBanner.classList.remove("hidden");
    } else {
        backgroundGameBanner.classList.add("hidden");
    }
}

async function joinGame() {
    statusLine.textContent = "Conectando ao jogo em andamento...";
    try {
        disconnectWhep();
        showPlayView();
        await connectWhep();
        connectInputSocket();
        await refreshStatus();
    } catch (err) {
        showIdleView();
        statusLine.textContent = `Erro ao conectar: ${err.message}`;
    }
}

async function loadGames() {
    try {
        const games = await fetchJSON("/games");
        renderGames(games);
    } catch (err) {
        statusLine.textContent = `Erro ao carregar jogos: ${err.message}`;
    }
}

async function scanLibrary() {
    scanBtn.disabled = true;
    statusLine.textContent = "Escaneando biblioteca...";
    try {
        const result = await fetchJSON("/scan", { method: "POST" });
        statusLine.textContent = `Escaneado: ${result.scanned} (novos: ${result.added}, atualizados: ${result.updated})`;
        renderGames(result.games);
    } catch (err) {
        statusLine.textContent = `Erro ao escanear: ${err.message}`;
    } finally {
        scanBtn.disabled = false;
    }
}

function showPlayView() {
    idleView.classList.add("hidden");
    playView.classList.remove("hidden");
}

function showIdleView() {
    playView.classList.add("hidden");
    idleView.classList.remove("hidden");
}

async function connectWhep() {
    if (!whepUrl) {
        await loadConfig();
    }
    const pc = new RTCPeerConnection();
    peerConnection = pc;

    pc.ontrack = (event) => {
        gameVideo.srcObject = event.streams[0];
    };
    pc.addTransceiver("video", { direction: "recvonly" });
    //pc.addTransceiver("audio", { direction: "recvonly" });

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    const resp = await fetch(whepUrl, {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: offer.sdp,
    });

    if (!resp.ok) {
        throw new Error(`WHEP falhou: ${resp.status}`);
    }

    const answerSdp = await resp.text();
    await pc.setRemoteDescription({ type: "answer", sdp: answerSdp });
}

function disconnectWhep() {
    if (peerConnection) {
        peerConnection.close();
        peerConnection = null;
    }
    gameVideo.srcObject = null;
}

function connectInputSocket() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    inputSocket = new WebSocket(`${proto}//${location.host}/ws/input`);
    inputSocket.addEventListener("open", () => {
        gamepadPollHandle = requestAnimationFrame(pollGamepad);
    });
    inputSocket.addEventListener("close", (event) => {
        stopGamepadPolling();
        // code 4000 = outro dispositivo assumiu o controle (backend fechou ativamente).
        // Volta para idle sem encerrar o jogo. Qualquer outro close é desconexão normal.
        if (event.code === 4000 && !playView.classList.contains("hidden")) {
            disconnectWhep();
            showIdleView();
            statusLine.textContent = "Controle assumido por outro dispositivo.";
            refreshStatus();
        }
    });
}

function disconnectInputSocket() {
    stopGamepadPolling();
    if (inputSocket) {
        inputSocket.close();
        inputSocket = null;
    }
}

function stopGamepadPolling() {
    if (gamepadPollHandle !== null) {
        cancelAnimationFrame(gamepadPollHandle);
        gamepadPollHandle = null;
    }
}

function pollGamepad() {
    const pads = navigator.getGamepads();
    const pad = pads[0];
    if (pad) {
        for (const indexStr of Object.keys(GAMEPAD_BUTTON_TO_KEY)) {
            const index = Number(indexStr);
            const pressed = pad.buttons[index]?.pressed ?? false;
            if (pressed !== !!_prevGamepadButtonState[index]) {
                _prevGamepadButtonState[index] = pressed;
                sendInputEvent(GAMEPAD_BUTTON_TO_KEY[index], pressed);
            }
        }
    }
    gamepadPollHandle = requestAnimationFrame(pollGamepad);
}

function sendInputEvent(key, pressed) {
    if (inputSocket && inputSocket.readyState === WebSocket.OPEN) {
        inputSocket.send(JSON.stringify({ key, pressed }));
    }
}

function handleGameKeydown(event) {
    if (playView.classList.contains("hidden")) {
        return;
    }
    const key = KEYBOARD_KEY_TO_INPUT[event.code];
    if (key === undefined) {
        return;
    }
    event.preventDefault();
    if (!event.repeat) {
        sendInputEvent(key, true);
    }
}

function handleGameKeyup(event) {
    if (playView.classList.contains("hidden")) {
        return;
    }
    const key = KEYBOARD_KEY_TO_INPUT[event.code];
    if (key === undefined) {
        return;
    }
    event.preventDefault();
    sendInputEvent(key, false);
}

async function playGame(gameId) {
    statusLine.textContent = "Iniciando jogo...";
    try {
        await fetchJSON("/play", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ game_id: gameId }),
        });
        showPlayView();
        await connectWhep();
        connectInputSocket();
        await refreshStatus();
    } catch (err) {
        statusLine.textContent = `Erro ao iniciar jogo: ${err.message}`;
    }
}

async function stopGame() {
    statusLine.textContent = "Encerrando jogo...";
    try {
        await fetchJSON("/stop", { method: "POST" });
        disconnectInputSocket();
        disconnectWhep();
        showIdleView();
        await refreshStatus();
        await loadGames();
    } catch (err) {
        statusLine.textContent = `Erro ao encerrar jogo: ${err.message}`;
    }
}

scanBtn.addEventListener("click", scanLibrary);
endGameBtn.addEventListener("click", stopGame);
backgroundJoinBtn.addEventListener("click", joinGame);
backgroundEndGameBtn.addEventListener("click", stopGame);
storageToggleBtn.addEventListener("click", () => storageForm.classList.toggle("hidden"));
storageForm.addEventListener("submit", submitStorageForm);
for (const radio of storageForm.querySelectorAll('input[name="storage-mode"]')) {
    radio.addEventListener("change", () => {
        cifsFields.classList.toggle("hidden", radio.value !== "cifs" || !radio.checked);
    });
}
fullscreenBtn.addEventListener("click", () => {
    gameVideo.requestFullscreen();
});
document.addEventListener("keydown", handleGameKeydown);
document.addEventListener("keyup", handleGameKeyup);

// É streaming em tempo real, não há "pausar o jogo" - qualquer tentativa de
// pausa (clique no vídeo, tecla de mídia do teclado/OS, gesto do sistema)
// é revertida imediatamente, antes que o usuário confunda isso com o jogo
// de fato ter parado no Pi.
gameVideo.addEventListener("pause", () => {
    if (!playView.classList.contains("hidden")) {
        gameVideo.play().catch(() => {});
    }
});
gameVideo.addEventListener("click", (event) => event.preventDefault());
gameVideo.addEventListener("dblclick", (event) => event.preventDefault());

loadConfig();
loadGames();
refreshStatus();
setInterval(refreshStatus, 5000);
